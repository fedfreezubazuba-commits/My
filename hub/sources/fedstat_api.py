"""
Перенесено без изменений логики из legacy/fedstat_client.py (проверено вживую 2026-10-17).
Не меняйте параметры запросов, токены, паузы и повторы без необходимости.

Мини-клиент для ЕМИСС (fedstat.ru).

Официального REST API у fedstat.ru нет (страница /api — просто главная).
Клиент использует то, что реально работает (проверено 2026-10-17):

  1. /opendata/list.xml                    — реестр открытых данных (~7000 показателей)
  2. /opendata/<identifier>/meta.xml       — паспорт набора (ведомство, даты, контакты)
  3. /indicator/<id>                       — HTML-страница показателя; в ней JS-объект
                                             с измерениями (filters) и их значениями
  4. POST /indicator/downloadData.do?format=sdmx|excel
                                           — выгрузка данных с нужным срезом
                                             (нужен CSRF-токен со страницы п.3)

Ограничения: сервер легко отвечает 503 при частых запросах на выгрузку —
делайте паузы (клиент повторяет с задержкой).

Пример:
    python fedstat_client.py search адвокат
    python fedstat_client.py dims 36984
    python fedstat_client.py csv 36984 out.csv --filter 36991=1564439 --filter 33560=1558883 --years 2015-2025
"""
import argparse
import csv
import http.cookiejar
import json
import os
import re
from html.parser import HTMLParser
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

BASE = "https://www.fedstat.ru"
BACKSLASH_U = re.escape(chr(92)) + "u([0-9a-fA-F]{4})"
WORDS = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine".split())}


class Fedstat:
    def __init__(self, pause=2.0):
        self.pause = pause
        jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        self.op.addheaders = [("User-Agent", "Mozilla/5.0")]

    def _get(self, url, data=None, retries=4):
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        for attempt in range(retries):
            try:
                with self.op.open(url, data=body, timeout=300) as r:
                    return r.headers.get("Content-Type", ""), r.read()
            except urllib.error.HTTPError as e:
                if e.code != 503 or attempt == retries - 1:
                    raise
                time.sleep(self.pause * 5 * (attempt + 1))

    # --- открытые данные ---------------------------------------------------
    def opendata_list(self):
        """[(identifier, indicator_id, title, data_link)] из реестра открытых данных."""
        _, b = self._get(BASE + "/opendata/list.xml")
        out = []
        for it in ET.fromstring(b).iter("item"):
            ident = it.findtext("identifier", "").strip()
            words = ident.split("-", 1)[1] if "-" in ident else ""
            ind_id = "".join(WORDS.get(w, "?") for w in words.split("a")) if words else ""
            out.append((ident, ind_id, " ".join(it.findtext("title", "").split()),
                        it.findtext("link", "").strip()))
        return out

    def passport(self, identifier):
        _, b = self._get(f"{BASE}/opendata/{identifier}/meta.xml")
        root = ET.fromstring(b)
        return {el.tag: " ".join((el.text or "").split()) for el in root if len(el) == 0}

    # --- каталог (навигация) ----------------------------------------------
    def catalog(self, cache_path=None, refresh=False):
        """
        Дерево показателей «ведомство → разделы → показатели» со страницы
        /organizations/ плюс годы наличия данных со страницы /indicators/.
        Узел: {"name", "code", "children": [...]} или {"id", "name", "excluded"}.
        Результат кэшируется в cache_path (обе страницы по ~10 МБ).
        """
        if cache_path and not refresh and os.path.exists(cache_path):
            with open(cache_path, encoding="utf-8") as fh:
                return json.load(fh)
        _, b = self._get(BASE + "/organizations/")
        p = _OrgTreeParser()
        p.feed(b.decode("utf-8"))
        _, b = self._get(BASE + "/indicators/")
        t = b.decode("utf-8")
        meta = {}
        for key in ("dataStart", "dataEnd"):
            for i, v in re.findall(r'id="indicator(\d+)_' + key + r'">\s*(\d*)\s*<', t):
                meta.setdefault(i, {})[key] = v
        cat = {"roots": p.root["children"], "years": meta,
               "updated": time.strftime("%Y-%m-%d %H:%M")}
        if cache_path:
            with open(cache_path, "w", encoding="utf-8") as fh:
                json.dump(cat, fh, ensure_ascii=False)
        return cat

    # --- показатель --------------------------------------------------------
    def indicator(self, ind_id):
        """Измерения показателя и CSRF-токен для выгрузки."""
        _, b = self._get(f"{BASE}/indicator/{ind_id}")
        raw = b.decode("utf-8")
        t = re.sub(BACKSLASH_U, lambda m: chr(int(m.group(1), 16)), raw)
        ids = lambda s: [x.strip() for x in s.split(",") if x.strip()]
        block = t[t.find("filters:"):t.find("left_columns:")]
        heads = list(re.finditer(r"(\d+):\s*\{\s*title:\s*'([^']*)',\s*all:", block))
        filters = {}
        for i, m in enumerate(heads):
            seg = block[m.end(): heads[i + 1].start() if i + 1 < len(heads) else len(block)]
            filters[m.group(1)] = {
                "title": m.group(2).strip(),
                "values": dict(re.findall(r"(\d+):\s*\{\s*title:\s*'([^']*)'", seg)),
            }
        tok = re.search(r'name="struts.token.name"[^>]*value="([^"]*)"', raw)
        tk = re.search(r'name="token"[^>]*value="([^"]*)"', raw)
        return {
            "id": str(ind_id),
            "title": re.search(r"title:\s*'([^']*)'", t).group(1).strip(),
            "unit": (re.search(r"unit:\s*'([^']*)'", t) or [None, ""])[1],
            "left": ids(re.search(r"left_columns:\s*\[([^\]]*)\]", t).group(1)),
            "top": ids(re.search(r"top_columns:\s*\[([^\]]*)\]", t).group(1)),
            "filters": filters,
            "token": (tok.group(1), tk.group(1)) if tok and tk else None,
        }

    def download(self, ind_id, fmt="sdmx", select=None):
        """
        Выгрузка данных. select = {filter_id: [value_id, ...]}; не указанные
        измерения берутся целиком. fmt: 'sdmx' (XML) или 'excel' (.xls).
        """
        info = self.indicator(ind_id)
        select = {str(k): [str(v) for v in vs] for k, vs in (select or {}).items()}
        lt = info["left"] + info["top"]
        p = [("title", info["title"]),
             ("struts.token.name", info["token"][0]), ("token", info["token"][1]),
             ("id", info["id"])]
        p += [("lineObjectIds", k) for k in info["filters"] if k not in lt]
        p += [("lineObjectIds", k) for k in info["left"]]
        p += [("columnObjectIds", k) for k in info["top"]]
        for k, f in info["filters"].items():
            for v in select.get(k, f["values"].keys()):
                p.append(("selectedFilterIds", f"{k}_{v}"))
        time.sleep(self.pause)
        ct, b = self._get(f"{BASE}/indicator/downloadData.do?format={fmt}", p)
        if b"CSRF" in b[:500] or ct.startswith("text/html"):
            raise RuntimeError("Сервер вернул HTML вместо данных: " + b[:300].decode("utf-8", "replace"))
        return b


class _OrgTreeParser(HTMLParser):
    """Разбирает вложенные div.ved_item страницы /organizations/ в дерево."""

    def __init__(self):
        super().__init__()
        self.root = {"children": []}
        self.nodes = [self.root]   # стек узлов дерева
        self.divs = []             # стек div: True, если div открыл узел
        self.field = None          # куда пишем текст span

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = (a.get("class") or "").split()
        if tag == "div":
            is_node = "ved_item" in cls
            self.divs.append(is_node)
            if is_node:
                node = {"name": "", "excluded": "i_excluded" in cls or "group_excluded" in cls}
                self.nodes[-1].setdefault("children", []).append(node)
                self.nodes.append(node)
        elif tag == "a" and len(self.nodes) > 1:
            m = re.match(r"/indicator/(\d+)", a.get("href") or "")
            if m:
                self.nodes[-1]["id"] = m.group(1)
        elif tag == "span" and len(self.nodes) > 1:
            for c, f in (("i_name", "name"), ("i_code", "code")):
                if c in cls and not self.nodes[-1].get(f):
                    self.field = f

    def handle_endtag(self, tag):
        if tag == "div" and self.divs:
            if self.divs.pop():
                self.nodes.pop()
        elif tag == "span":
            self.field = None

    def handle_data(self, data):
        if self.field:
            n = self.nodes[-1]
            n[self.field] = (n.get(self.field, "") + data).strip()


def sdmx_to_rows(xml_bytes):
    """SDMX-ML 1.0 Generic -> список словарей (измерения расшифрованы по CodeLists)."""
    ns = {"m": "http://www.SDMX.org/resources/SDMXML/schemas/v1_0/message",
          "s": "http://www.SDMX.org/resources/SDMXML/schemas/v1_0/structure",
          "g": "http://www.SDMX.org/resources/SDMXML/schemas/v1_0/generic"}
    root = ET.fromstring(xml_bytes)
    names, codes = {}, {}
    for cl in root.iter(f"{{{ns['s']}}}CodeList"):
        names[cl.get("id")] = cl.findtext("s:Name", "", ns)
        codes[cl.get("id")] = {c.get("value"): c.findtext("s:Description", "", ns)
                               for c in cl.findall("s:Code", ns)}
    rows = []
    for s in root.iter(f"{{{ns['g']}}}Series"):
        base = {}
        for v in s.findall("g:SeriesKey/g:Value", ns):
            c = v.get("concept")
            base[names.get(c, c)] = codes.get(c, {}).get(v.get("value"), v.get("value"))
        for v in s.findall("g:Attributes/g:Value", ns):
            base[v.get("concept")] = v.get("value")
        for o in s.findall("g:Obs", ns):
            rows.append({**base, "TIME": o.findtext("g:Time", "", ns),
                         "VALUE": o.find("g:ObsValue", ns).get("value")})
    return rows


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("search").add_argument("text")
    sub.add_parser("dims").add_argument("id")
    c = sub.add_parser("csv")
    c.add_argument("id"); c.add_argument("out")
    c.add_argument("--filter", action="append", default=[], help="filter_id=value_id[,value_id]")
    c.add_argument("--years", help="напр. 2015-2025")
    x = sub.add_parser("excel"); x.add_argument("id"); x.add_argument("out")
    a = ap.parse_args()
    fs = Fedstat()

    if a.cmd == "search":
        for ident, ind_id, title, _ in fs.opendata_list():
            if a.text.lower() in title.lower():
                print(f"{ind_id:>7}  {title}")
    elif a.cmd == "dims":
        info = fs.indicator(a.id)
        print(info["title"], f"[{info['unit']}]")
        for k, f in info["filters"].items():
            vals = list(f["values"].items())
            print(f"\n{k}: {f['title']} ({len(vals)} знач.)")
            for vid, vt in vals[:15]:
                print(f"    {vid}: {vt.strip()}")
            if len(vals) > 15:
                print("    ...")
    elif a.cmd == "csv":
        sel = {}
        for f in a.filter:
            k, v = f.split("=", 1)
            sel[k] = v.split(",")
        if a.years:
            y0, y1 = map(int, a.years.split("-"))
            sel["3"] = [str(y) for y in range(y0, y1 + 1)]
        rows = sdmx_to_rows(fs.download(a.id, "sdmx", sel))
        cols = list(dict.fromkeys(k for r in rows for k in r))
        with open(a.out, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, cols, delimiter=";")
            w.writeheader(); w.writerows(rows)
        print(f"{len(rows)} строк -> {a.out}")
    elif a.cmd == "excel":
        open(a.out, "wb").write(fs.download(a.id, "excel"))
        print("saved", a.out)


if __name__ == "__main__":
    main()
