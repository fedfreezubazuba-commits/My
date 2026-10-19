"""
Перенесено без изменений логики из legacy/cbr_client.py (проверено вживую 2026-10-17).
Не меняйте параметры запросов, токены, паузы и повторы без необходимости.

Навигатор и выгрузка данных «Сервиса получения данных» Банка России.

API: https://www.cbr.ru/dataservice (документация:
https://cbr.ru/statistics/data-service/APIdocumentation/). Ключ не нужен.
Клиент использует (проверено 2026-10-17):

  1. /publications                 — дерево «раздел → категория» (NoActive=1 — раздел)
  2. /categoryNew                  — все показатели всех категорий, с годами наличия
  3. /dataNew?categoryId&y1&y2     — данные; i_ids/m1_ids/m2_ids — фильтры (повторяемые);
                                     в ответе Links — расшифровка показателей/разрезов/единиц
  4. /DatasetDescription?datasetId — методология показателя (HTML)

Поле date в данных — отчётная дата в понимании ЦБ: для месячных рядов это
обычно 1-е число месяца, следующего за периодом (январь 2025 → 2025-02-01),
для остатков — дата «на 1 число».

Без аргументов запускается пошаговый режим (дерево → показатель → разрезы →
годы → файл). Примеры:
    python cbr_client.py
    python cbr_client.py tree
    python cbr_client.py search ипотек
    python cbr_client.py info 18 37
    python cbr_client.py export 18 deposits.xlsx -i 37 --m1 2 --years 2020-2026
    python cbr_client.py export 5 m2.csv -i 7 --wide
"""
import argparse
import csv
import datetime as dt
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request

BASE = "https://www.cbr.ru/dataservice"
SITE = "https://www.cbr.ru"
CHUNK_YEARS = 10   # длинные периоды запрашиваются кусками
PERIODICITY = {"day": "дневная", "week": "недельная", "month": "месячная",
               "quarter": "квартальная", "halfyear": "полугодовая", "year": "годовая"}


class CBR:
    def __init__(self, pause=0.3):
        self.pause = pause
        self.op = urllib.request.build_opener()
        self.op.addheaders = [("User-Agent", "Mozilla/5.0"), ("Accept", "application/json")]
        self._cats = None

    def _get(self, path, params=(), retries=4):
        url = BASE + path + ("?" + urllib.parse.urlencode(list(params)) if params else "")
        for attempt in range(retries):
            try:
                with self.op.open(url, timeout=120) as r:
                    return json.load(r)
            except (urllib.error.URLError, TimeoutError) as e:
                code = getattr(e, "code", None)
                if (code and code < 500) or attempt == retries - 1:
                    raise
                time.sleep(2 * (attempt + 1))

    # --- каталог -----------------------------------------------------------
    def tree(self):
        """Дерево публикаций: [{"id","name","section","desc","updated","children"}]."""
        pubs = self._get("/publications")
        nodes = {p["id"]: {"id": p["id"], "name": p["category_name"].strip(),
                           "section": bool(p["NoActive"]), "desc": p.get("pub_description") or "",
                           "updated": (p.get("updated_time") or "")[:10], "children": []}
                 for p in pubs}
        roots = []
        for p in pubs:
            (nodes[p["parent_id"]]["children"] if p["parent_id"] in nodes else roots).append(nodes[p["id"]])
        return roots

    def indicators(self, category_id=None):
        """Показатели из /categoryNew: {"category_id","indicator_id","indicator_name",
        "indicator_parent","begin_dt","end_dt","link",...}."""
        if self._cats is None:
            self._cats = self._get("/categoryNew")["category"]
        if category_id is None:
            return self._cats
        return [c for c in self._cats if c["category_id"] == int(category_id)]

    def updated(self, category_id):
        """Даты обновления показателей категории: {indicator_id: 'YYYY-MM-DD'}."""
        if not hasattr(self, "_upd"):
            self._upd = {}
        if category_id not in self._upd:
            ds = self._get("/datasets", [("publicationId", category_id)])
            self._upd[category_id] = {d["id"]: (d.get("updated_time") or "")[:10] for d in ds}
        return self._upd[category_id]

    def indicator(self, category_id, indicator_id):
        for c in self.indicators(category_id):
            if c["indicator_id"] == int(indicator_id):
                return c
        raise KeyError(f"Показатель {indicator_id} не найден в категории {category_id}")

    def structure(self, category_id, indicator_id, years=3):
        """Разрезы показателя по данным последних `years` лет:
        {"m1": {id: name}, "m2": {id: name}, "units": {id: name}, "links": [...]}."""
        ind = self.indicator(category_id, indicator_id)
        y2 = ind["end_dt"]
        y1 = max(ind["begin_dt"], y2 - years + 1)
        d = self._get("/dataNew", [("categoryId", category_id), ("y1", y1), ("y2", y2),
                                   ("i_ids", indicator_id)])
        links = d["Links"]
        periodicity = sorted({r["periodicity"] for r in d["RowData"]})
        pick = lambda k: dict(sorted({l[k + "_id"]: l[k + "_name"] for l in links
                                      if l[k + "_id"] is not None}.items()))
        return {"m1": pick("measure1"), "m2": pick("measure2"),
                "units": {l["unit_id"]: l["un_name"] for l in links}, "links": links,
                "periodicity": [PERIODICITY.get(p, p) for p in periodicity]}

    def description(self, dataset_id):
        """Методология показателя — простым текстом."""
        try:
            d = self._get("/DatasetDescription", [("datasetId", dataset_id)])
        except urllib.error.HTTPError:
            return ""
        raw = " ".join(x.get("description") or "" for x in d)
        return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", raw)).split())

    # --- данные ------------------------------------------------------------
    def data(self, category_id, y1, y2, i_ids=(), m1_ids=(), m2_ids=()):
        """Данные с расшифровкой: список словарей (длинный формат)."""
        rows, names = [], {}
        for a in range(int(y1), int(y2) + 1, CHUNK_YEARS):
            b = min(a + CHUNK_YEARS - 1, int(y2))
            p = [("categoryId", category_id), ("y1", a), ("y2", b)]
            p += [("i_ids", i) for i in i_ids] + [("m1_ids", i) for i in m1_ids]
            p += [("m2_ids", i) for i in m2_ids]
            d = self._get("/dataNew", p)
            for l in d["Links"]:
                names[(l["indicator_id"], l["measure1_id"], l["measure2_id"], l["unit_id"])] = l
            rows += d["RowData"]
            time.sleep(self.pause)
        out = []
        for r in rows:
            l = names.get((r["indicator_id"], r["measure1_id"], r["measure2_id"], r["unit_id"]), {})
            out.append({
                "date": dt.date.fromisoformat(r["date"][:10]),
                "periodicity": PERIODICITY.get(r["periodicity"], r["periodicity"]),
                "indicator_id": r["indicator_id"],
                "indicator": l.get("indicator_name", ""),
                "measure1_id": r["measure1_id"],
                "measure1": l.get("measure1_name") or "",
                "measure2_id": r["measure2_id"],
                "measure2": l.get("measure2_name") or "",
                "unit": l.get("un_name", ""),
                "value": r["obs_val"],
            })
        nz = lambda v: -1 if v is None else v
        out.sort(key=lambda r: (r["indicator_id"], nz(r["measure1_id"]), nz(r["measure2_id"]), r["date"]))
        return _dedup(out), list(names.values())


def _dedup(rows):
    seen, out = set(), []
    for r in rows:
        k = (r["date"], r["indicator_id"], r["measure1_id"], r["measure2_id"], r["unit"])
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


# --- преобразование и запись ----------------------------------------------
LONG_COLS = [("date", "Дата"), ("periodicity", "Периодичность"),
             ("indicator_id", "ID показателя"), ("indicator", "Показатель"),
             ("measure1_id", "ID разреза 1"), ("measure1", "Разрез 1"),
             ("measure2_id", "ID разреза 2"), ("measure2", "Разрез 2"),
             ("unit", "Ед. изм."), ("value", "Значение")]


def series_name(r):
    parts = [r["indicator"], r["measure1"], r["measure2"]]
    return " | ".join(p for p in parts if p) + (f", {r['unit']}" if r["unit"] else "")


def to_wide(rows):
    """Строки — даты, столбцы — ряды (показатель | разрез 1 | разрез 2, ед.)."""
    cols = list(dict.fromkeys(series_name(r) for r in rows))
    table = {}
    for r in rows:
        table.setdefault(r["date"], {})[series_name(r)] = r["value"]
    return cols, [(d, table[d]) for d in sorted(table)]


def write_csv(path, rows, wide=False):
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        num = lambda v: "" if v is None else str(v).replace(".", ",")
        if wide:
            cols, data = to_wide(rows)
            w.writerow(["Дата"] + cols)
            for d, vals in data:
                w.writerow([d.isoformat()] + [num(vals.get(c)) for c in cols])
        else:
            w.writerow([t for _, t in LONG_COLS])
            for r in rows:
                w.writerow([r[k].isoformat() if k == "date" else num(r[k]) if k == "value"
                            else ("" if r[k] is None else r[k]) for k, _ in LONG_COLS])


def write_xlsx(path, rows, meta):
    """Листы: «Таблица» (широкая), «Данные» (длинная), «Описание» (параметры запроса)."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    bold = Font(bold=True)

    ws = wb.active
    ws.title = "Таблица"
    cols, data = to_wide(rows)
    ws.append(["Дата"] + cols)
    for d, vals in data:
        ws.append([d] + [vals.get(c) for c in cols])
    for c in ws[1]:
        c.font = bold
        c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.row_dimensions[1].height = 75
    ws.column_dimensions["A"].width = 12
    for i in range(2, len(cols) + 2):
        ws.column_dimensions[get_column_letter(i)].width = 22
    for row in ws.iter_rows(min_row=2, max_col=1):
        row[0].number_format = "DD.MM.YYYY"
    ws.freeze_panes = "B2"

    ws = wb.create_sheet("Данные")
    ws.append([t for _, t in LONG_COLS])
    for r in rows:
        ws.append([r[k] for k, _ in LONG_COLS])
    for c in ws[1]:
        c.font = bold
    for row in ws.iter_rows(min_row=2, max_col=1):
        row[0].number_format = "DD.MM.YYYY"
    for letter, width in zip("ABCDEFGHIJ", (12, 13, 8, 45, 8, 30, 8, 40, 14, 12)):
        ws.column_dimensions[letter].width = width
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    ws = wb.create_sheet("Описание")
    for k, v in meta:
        ws.append([k, v])
        ws.cell(ws.max_row, 1).font = bold
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 110
    for row in ws.iter_rows(min_col=2, max_col=2):
        row[0].alignment = Alignment(wrap_text=True, vertical="top")
    wb.save(path)


def export(cbr, category_id, out, i_ids=(), m1_ids=(), m2_ids=(), years=None, wide=False):
    inds = cbr.indicators(category_id)
    if not inds:
        raise ValueError(f"Категория {category_id} не найдена (см. команду tree)")
    sel = [c for c in inds if not i_ids or c["indicator_id"] in i_ids]
    if not sel:
        raise ValueError(f"В категории {category_id} нет показателей с ID {', '.join(map(str, i_ids))}")
    y1, y2 = years or (min(c["begin_dt"] for c in sel), max(c["end_dt"] for c in sel))
    rows, _ = cbr.data(category_id, y1, y2, i_ids, m1_ids, m2_ids)
    if not rows:
        raise ValueError("Данных не найдено — проверьте ID показателя/разрезов и годы")
    cmd = f"python cbr_client.py export {category_id} {out}"
    cmd += "".join(f" {flag} {','.join(map(str, v))}" for flag, v in
                   (("-i", i_ids), ("--m1", m1_ids), ("--m2", m2_ids)) if v)
    cmd += f" --years {y1}-{y2}" + (" --wide" if wide else "")
    if out.lower().endswith(".xlsx"):
        meta = [("Источник", "Банк России, Сервис получения данных (https://cbr.ru/statistics/data-service/)"),
                ("Категория", f"{category_id}: {inds[0]['category_name']}"),
                ("Показатели", "; ".join(f"{c['indicator_id']}: {c['indicator_name']}" for c in sel)),
                ("Разрез 1 (ID)", ", ".join(map(str, m1_ids)) or "все"),
                ("Разрез 2 (ID)", ", ".join(map(str, m2_ids)) or "все"),
                ("Годы", f"{y1}–{y2}"),
                ("Строк", len(rows)),
                ("Выгружено", dt.datetime.now().strftime("%Y-%m-%d %H:%M")),
                ("Повторить", cmd),
                ("Дата", "Отчётная дата ЦБ: для месячных рядов обычно 1-е число следующего месяца")]
        meta += [(f"Методология {c['indicator_id']}", SITE + c["link"]) for c in sel if c.get("link")]
        write_xlsx(out, rows, meta)
    else:
        write_csv(out, rows, wide)
    return rows, cmd


def report(rows, out, cmd):
    print(f"{len(rows)} значений, {len({series_name(r) for r in rows})} рядов -> {out}")
    print("Повторить:", cmd)


# --- вывод в консоль -------------------------------------------------------
def print_tree(cbr, with_indicators=False):
    counts = {}
    for c in cbr.indicators():
        counts[c["category_id"]] = counts.get(c["category_id"], 0) + 1

    def walk(nodes, depth):
        for n in nodes:
            pad = "    " * depth
            if n["section"]:
                print(f"{pad}{n['name']}")
            else:
                print(f"{pad}[{n['id']:>3}] {n['name']}  ({counts.get(n['id'], 0)} пок.)")
                if with_indicators:
                    for c in sorted(cbr.indicators(n["id"]), key=lambda c: c["indicator_name"]):
                        print(f"{pad}        {c['indicator_id']:>4}  {c['indicator_name']}"
                              f"  [{c['begin_dt']}–{c['end_dt']}]")
            walk(n["children"], depth + 1)
    walk(cbr.tree(), 0)


def print_info(cbr, category_id, indicator_id):
    ind = cbr.indicator(category_id, indicator_id)
    st = cbr.structure(category_id, indicator_id)
    print(f"{ind['indicator_id']}: {ind['indicator_name']}")
    print(f"Категория {category_id}: {ind['category_name']}")
    print(f"Годы: {ind['begin_dt']}–{ind['end_dt']}   Ед. изм.: {', '.join(st['units'].values())}")
    if ind.get("link"):
        print("Методология:", SITE + ind["link"])
    for key, title in (("m1", "Разрез 1 (--m1)"), ("m2", "Разрез 2 (--m2)")):
        if st[key]:
            print(f"\n{title}:")
            for k, v in st[key].items():
                print(f"    {k:>5}  {v}")
    desc = cbr.description(indicator_id)
    if desc:
        print("\nОписание:", desc[:600] + ("…" if len(desc) > 600 else ""))


# --- пошаговый режим ---------------------------------------------------------
def ask(prompt, default=""):
    try:
        s = input(f"{prompt}" + (f" [{default}]" if default != "" else "") + ": ").strip("﻿ \t")
    except EOFError:
        raise SystemExit
    if s.lower() in ("q", "й", "выход"):
        raise SystemExit
    return s or str(default)


def choose(title, items, multi=False):
    """items — список (value, text). Возвращает value или список value (пусто = все)."""
    print(f"\n{title}")
    for n, (_, text) in enumerate(items, 1):
        print(f"  {n:>3}. {text}")
    hint = "номера через запятую или диапазон 1-5, Enter — все" if multi else "номер"
    while True:
        s = ask(f"Выберите ({hint}; 0 — назад, q — выход)")
        if s == "0":
            return None
        if multi and s == "":
            return []
        try:
            nums = set()
            for part in s.replace(" ", "").split(","):
                a, _, b = part.partition("-")
                nums.update(range(int(a), int(b or a) + 1))
            picked = [items[n - 1][0] for n in sorted(nums) if 1 <= n <= len(items)]
            if picked and (multi or len(picked) == 1):
                return picked if multi else picked[0]
        except ValueError:
            pass
        print("  Не понял выбор, попробуйте ещё раз.")


def interactive(cbr):
    print("Навигатор данных Банка России (q — выход в любой момент)")
    stack, nodes = [], cbr.tree()
    while True:
        node = choose("Раздел / категория:", [(n, n["name"] + ("" if n["section"] else f"  [{n['id']}]"))
                                               for n in nodes])
        if node is None:
            if stack:
                nodes = stack.pop()
            continue
        if node["section"]:
            stack.append(nodes)
            nodes = node["children"]
            continue
        if not cbr.indicators(node["id"]):
            print("  В этой категории нет показателей.")
            continue
        if run_category(cbr, node):
            if ask("\nВыгрузить что-то ещё? (д/н)", "н").lower() not in ("д", "y", "да"):
                return


def run_category(cbr, node):
    cid = node["id"]
    inds = sorted(cbr.indicators(cid), key=lambda c: c["indicator_name"])
    picked = choose(f"Показатели категории «{node['name']}»:",
                    [(c, f"{c['indicator_name']}  [{c['begin_dt']}–{c['end_dt']}]") for c in inds],
                    multi=True)
    if picked is None:
        return False
    sel = picked or inds
    m1, m2 = [], []
    if len(sel) == 1:
        st = cbr.structure(cid, sel[0]["indicator_id"])
        print(f"\nЕд. изм.: {', '.join(st['units'].values())}")
        for key, title in (("m1", "Разрез 1"), ("m2", "Разрез 2")):
            if len(st[key]) > 1:
                ch = choose(f"{title}:", list(st[key].items()), multi=True)
                if ch is None:
                    return False
                (m1 if key == "m1" else m2).extend(ch)
    else:
        print(f"\nВыбрано показателей: {len(sel)} — разрезы будут выгружены все.")
    y_from, y_to = min(c["begin_dt"] for c in sel), max(c["end_dt"] for c in sel)
    while True:
        s = ask(f"Годы (напр. 2018-{y_to})", f"{y_from}-{y_to}")
        m = re.fullmatch(r"(\d{4})(?:\s*-\s*(\d{4}))?", s)
        if m:
            y1, y2 = int(m.group(1)), int(m.group(2) or m.group(1))
            break
        print("  Формат: 2018-2026 или 2024")
    default = f"cbr_{cid}_{dt.date.today():%Y%m%d}.xlsx"
    out = ask("Файл (.xlsx или .csv)", default)
    if not out.lower().endswith((".xlsx", ".csv")):
        out += ".xlsx"
    wide = out.lower().endswith(".csv") and ask("CSV широкой таблицей? (д/н)", "н").lower() in ("д", "y", "да")
    i_ids = [c["indicator_id"] for c in picked]
    try:
        rows, cmd = export(cbr, cid, out, i_ids, m1, m2, (y1, y2), wide)
        report(rows, out, cmd)
    except ValueError as e:
        print(" ", e)
        return False
    return True


def ids(s):
    return [int(x) for x in re.split(r"[,\s]+", s.strip()) if x]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8") if sys.stdin and hasattr(sys.stdin, "reconfigure") else None
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("nav", help="пошаговый режим (по умолчанию)")
    t = sub.add_parser("tree", help="дерево разделов и категорий")
    t.add_argument("--full", action="store_true", help="с показателями")
    sub.add_parser("search", help="поиск показателя по названию").add_argument("text")
    i = sub.add_parser("info", help="разрезы, годы и методология показателя")
    i.add_argument("category", type=int); i.add_argument("indicator", type=int)
    e = sub.add_parser("export", help="выгрузка в .xlsx или .csv")
    e.add_argument("category", type=int); e.add_argument("out")
    e.add_argument("-i", "--indicators", type=ids, default=[], help="ID показателей через запятую (по умолчанию все)")
    e.add_argument("--m1", type=ids, default=[], help="ID разреза 1 через запятую")
    e.add_argument("--m2", type=ids, default=[], help="ID разреза 2 через запятую")
    e.add_argument("--years", help="напр. 2015-2026 (по умолчанию весь ряд)")
    e.add_argument("--wide", action="store_true", help="CSV широкой таблицей (даты × ряды)")
    a = ap.parse_args()
    cbr = CBR()

    if a.cmd in (None, "nav"):
        interactive(cbr)
    elif a.cmd == "tree":
        print_tree(cbr, a.full)
    elif a.cmd == "search":
        # грубая основа слова, чтобы «ипотека» находила «ипотечным»
        stems = [w[:max(4, len(w) - 2)] for w in a.text.lower().split()]
        for c in cbr.indicators():
            text = (c["indicator_name"] + " " + c["category_name"]).lower()
            if all(s in text for s in stems):
                print(f"кат. {c['category_id']:>3}  пок. {c['indicator_id']:>4}  {c['indicator_name']}"
                      f"  [{c['begin_dt']}–{c['end_dt']}]  — {c['category_name']}")
    elif a.cmd == "info":
        print_info(cbr, a.category, a.indicator)
    elif a.cmd == "export":
        years = None
        if a.years:
            y = [int(x) for x in a.years.split("-")]
            years = (y[0], y[-1])
        try:
            rows, cmd = export(cbr, a.category, a.out, a.indicators, a.m1, a.m2, years, a.wide)
        except ValueError as e:
            raise SystemExit(str(e))
        report(rows, a.out, cmd)


if __name__ == "__main__":
    main()
