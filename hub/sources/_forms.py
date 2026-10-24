"""
Запрос формы на сайте («Форма на сайте», web_form).

Пользователь один раз заполняет форму во встроенном браузере (коды товаров, страна, импорт/экспорт, период)
и нажимает поиск. Браузер записывает запросы страницы (browser.record); здесь из них выбирается тот, что
вернул таблицу с числами, и разбираются его поля:

  • поля периода (год, месяц с/по, «202401», даты) — хаб будет перебирать их сам: многие формы не дают
    выбрать несколько лет сразу, поэтому история «с 2020 года» — это несколько запросов, по году (или по
    месяцу, если в ответе нет столбца с датой);
  • размер страницы (pageSize, rows…) — увеличивается, чтобы все месяцы года поместились в один ответ;
  • остальные поля (коды, страна, вид торговли…) повторяются как есть; их можно поменять в окне настройки.

Ответ — HTML с таблицей или JSON со списком записей — превращается в таблицу (_tables.Table), а она в ряды:
строка = набор подписей (код, товар, страна…) и дата, числовые столбцы (количество, стоимость) — ряды.
"""
import datetime as dt
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..core import periods as P
from . import _tables as T
from . import _web as W

YEAR_NAME = re.compile(r"year|yr|год|^y$", re.I)
START_NAME = re.compile(r"start|begin|from|^s|min|с$|нач", re.I)
END_NAME = re.compile(r"end|to$|^e|max|till|по$|кон", re.I)
MONTH_NAME = re.compile(r"month|mon|mth|мес", re.I)
DATE_NAME = re.compile(r"date|time|period|дата|период|day", re.I)
PAGE_SIZE_NAME = re.compile(r"^(page_?size|pagesize|rows|limit|per_?page|size|length|pageRows)$", re.I)
PAGE_SIZE = 1000
CODE_HEAD = re.compile(r"code|код|编码|代码|^id$|^no\.?$|序号|^№|номер|rank|^hs\b|hs_?code|тн ?вэд|partner|country|страна",
                       re.I)
UNIT_HEAD = re.compile(r"(^|\s)units?$|^unit|单位|^ед\.?|единиц|measure", re.I)
QTY_HEAD = re.compile(r"quantit|qty|weight|volume|amount of goods|количеств|объ[её]м|вес|数量|重量", re.I)
MONTH_ROW = re.compile(r"^\s*(\d{4})\s*(?:[-/.年]\s*)?(\d{1,2})\s*月?\s*$")
CURRENCY = [(re.compile(r"us ?dollar|usd|美元|\$|долл", re.I), "USD"),
            (re.compile(r"rmb|yuan|cny|人民币|юан", re.I), "CNY"),
            (re.compile(r"eur|евро", re.I), "EUR"), (re.compile(r"руб|rub", re.I), "руб.")]


# --- запрос и его поля ----------------------------------------------------------------------
def _is_json(req) -> bool:
    ct = (req.get("content_type") or "").lower()
    body = (req.get("body") or "").lstrip()
    return "json" in ct or body.startswith("{")


def fields(req) -> list[tuple[str, str, str]]:
    """[(где, имя, значение)]: где — query (в адресе) или body (в теле запроса)."""
    out = [("query", k, v) for k, v in parse_qsl(urlsplit(req["url"]).query, keep_blank_values=True)]
    body = req.get("body") or ""
    if body and _is_json(req):
        try:
            data = json.loads(body)
        except ValueError:
            data = {}
        if isinstance(data, dict):
            out += [("body", k, "" if v is None else str(v)) for k, v in data.items()
                    if not isinstance(v, (dict, list))]
    elif body:
        out += [("body", k, v) for k, v in parse_qsl(body, keep_blank_values=True)]
    return out


def with_fields(req, values: dict) -> dict:
    """Тот же запрос с другими значениями полей ({имя: значение}; поле ищется и в адресе, и в теле)."""
    if not values:
        return dict(req)
    parts = urlsplit(req["url"])
    q = [(k, str(values[k]) if k in values else v) for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    new = dict(req, url=urlunsplit(parts._replace(query=urlencode(q))))
    body = req.get("body") or ""
    if body and _is_json(req):
        try:
            data = json.loads(body)
        except ValueError:
            return new
        if isinstance(data, dict):
            for k in data:
                if k in values and not isinstance(data[k], (dict, list)):
                    old = data[k]
                    v = values[k]
                    data[k] = int(v) if isinstance(old, int) and str(v).lstrip("-").isdigit() else v
            new["body"] = json.dumps(data, ensure_ascii=False)
    elif body:
        new["body"] = urlencode([(k, str(values[k]) if k in values else v)
                                 for k, v in parse_qsl(body, keep_blank_values=True)])
    return new


# --- поля периода --------------------------------------------------------------------------
def _int(v):
    try:
        return int(str(v).strip())
    except ValueError:
        return None


def detect_period(flds, today: dt.date | None = None) -> dict | None:
    """Какие поля задают период. Варианты:
    {"kind": "year_months", "year": имя, "start": имя|None, "end": имя|None} — год + месяцы с/по (таможня КНР);
    {"kind": "yyyymm", "start": имя, "end": имя|None} — «202401»;
    {"kind": "dates", "start": имя, "end": имя|None, "fmt": "%Y-%m-%d"} — даты;
    None — периода в запросе нет (запрос повторяется как есть)."""
    today = today or dt.date.today()
    names = {n: v for _w, n, v in flds}
    year = next((n for n, v in names.items() if YEAR_NAME.search(n) and (_int(v) or 0) in range(1990, today.year + 2)),
                None)
    months = [n for n, v in names.items() if MONTH_NAME.search(n) and not re.search(r"flag|type|show|mode", n, re.I)
              and (_int(v) or 0) in range(1, 13)]
    if year:
        start = next((n for n in months if START_NAME.search(n.lower().replace("month", ""))), None)
        end = next((n for n in months if n != start and END_NAME.search(n.lower().replace("month", ""))), None)
        if not start and len(months) == 2:
            start, end = months
        return {"kind": "year_months", "year": year, "start": start, "end": end}
    ym = [n for n, v in names.items() if re.fullmatch(r"(19|20)\d{2}(0[1-9]|1[0-2])", str(v).strip())
          and not CODE_HEAD.search(n)]
    if ym:
        start = next((n for n in ym if START_NAME.search(n)), ym[0])
        end = next((n for n in ym if n != start), None)
        return {"kind": "yyyymm", "start": start, "end": end}
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y/%m/%d"):
        ds = []
        for n, v in names.items():
            try:
                dt.datetime.strptime(str(v).strip(), fmt)
                ds.append(n)
            except ValueError:
                continue
        if ds:
            start = next((n for n in ds if START_NAME.search(n)), ds[0])
            end = next((n for n in ds if n != start), None)
            return {"kind": "dates", "start": start, "end": end, "fmt": fmt}
    return None


def roles(flds, period) -> dict:
    """{имя поля: что это} — для окна настройки."""
    out = {}
    if period:
        for key, word in (("year", "год — хаб перебирает сам"), ("start", "начало периода — хаб перебирает сам"),
                          ("end", "конец периода — хаб перебирает сам")):
            if period.get(key):
                out[period[key]] = word
    for _w, n, v in flds:
        if PAGE_SIZE_NAME.match(n) and _int(v):
            out[n] = f"строк на странице — хаб ставит {PAGE_SIZE}"
    return out


def chunks(period, from_year, today: dt.date, by="year", since: dt.date | None = None) -> list[tuple[dict, dt.date, dt.date]]:
    """[(значения полей периода, начало, конец)] — по году (или по месяцу) от from_year по прошлый месяц.
    since — дозагрузка: только части, которые заканчиваются не раньше этой даты."""
    if period and period.get("kind") == "year_months" and not period.get("start") and not period.get("end"):
        by = "year"                     # в запросе только год (вкладки лет) — по месяцам спрашивать нечего
    if period and period.get("kind") == "dates":
        return day_chunks(period, from_year, today, by, since)
    out = []
    for y in range(int(from_year), today.year + 1):
        last_m = 12 if y < today.year else today.month - 1     # текущий месяц ещё не закончился — данных нет
        if last_m < 1:
            continue
        spans = [(1, last_m)] if by == "year" else [(m, m) for m in range(1, last_m + 1)]
        for m1, m2 in spans:
            a, b = dt.date(y, m1, 1), P.month_end(y, m2)
            if since and b < since:
                continue
            out.append((period_values(period, a, b), a, b))
    return out


def day_chunks(period, from_year, today: dt.date, by="year", since: dt.date | None = None):
    """Форма с датами «с … по …» (курсы валют, котировки): данные бывают за каждый день, поэтому — по сегодня
    включительно (не только законченные месяцы); дозагрузка — с даты since, а не с начала месяца."""
    start = max(since, dt.date(int(from_year), 1, 1)) if since else dt.date(int(from_year), 1, 1)
    out = []
    a = start
    while a <= today:
        b = dt.date(a.year, 12, 31) if by == "year" else P.month_end(a.year, a.month)
        b = min(b, today)
        out.append((period_values(period, a, b), a, b))
        a = b + dt.timedelta(days=1)
    return out


def period_values(period, a: dt.date, b: dt.date) -> dict:
    if not period:
        return {}
    k = period["kind"]
    vals = {}
    if k == "year_months":
        vals[period["year"]] = str(a.year)
        if period.get("start"):
            vals[period["start"]] = str(a.month)
        if period.get("end"):
            vals[period["end"]] = str(b.month)
    elif k == "yyyymm":
        vals[period["start"]] = f"{a:%Y%m}"
        if period.get("end"):
            vals[period["end"]] = f"{b:%Y%m}"
    elif k == "dates":
        vals[period["start"]] = a.strftime(period["fmt"])
        if period.get("end"):
            vals[period["end"]] = b.strftime(period["fmt"])
    return vals


def page_size_values(flds) -> dict:
    return {n: str(PAGE_SIZE) for _w, n, v in flds if PAGE_SIZE_NAME.match(n) and (_int(v) or 0) < PAGE_SIZE}


# --- ответ → таблица -----------------------------------------------------------------------
def _json_records(data, depth=0):
    """Самый большой список записей (словарей) где-нибудь внутри JSON."""
    best = []
    if depth > 6:
        return best
    if isinstance(data, list) and data and all(isinstance(x, dict) for x in data):
        best = data
    items = data.values() if isinstance(data, dict) else data if isinstance(data, list) else []
    for x in items:
        if isinstance(x, (dict, list)):
            cand = _json_records(x, depth + 1)
            if len(cand) > len(best):
                best = cand
    return best


def _x_value(x):
    """Значение оси X графика → дата текстом: миллисекунды/секунды с 1970 года → «2024-01-31»."""
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        if 1e11 <= x <= 4.1e12:
            return dt.datetime.fromtimestamp(x / 1000, dt.timezone.utc).date().isoformat()
        if 1e9 <= x < 4.1e9:
            return dt.datetime.fromtimestamp(x, dt.timezone.utc).date().isoformat()
        return None
    if isinstance(x, str) and x.strip():
        return x.strip()
    return None


def _num_value(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str) and T._is_num(v):
        return v
    return None


def _points(data):
    """Точки ряда графика: [[x, y], …] или [{x/date…, y/value…}, …] → [(дата, значение)]."""
    pts = []
    if not isinstance(data, list):
        return pts
    for p in data:
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            x, y = _x_value(p[0]), _num_value(p[1])
        elif isinstance(p, dict):
            xk = next((k for k in ("x", "date", "Date", "time", "t", "period", "week", "day") if k in p), None)
            yk = next((k for k in ("y", "value", "Value", "price", "close", "v") if k in p), None)
            x, y = (_x_value(p[xk]), _num_value(p[yk])) if xk and yk else (None, None)
        else:
            continue
        if x is not None and y is not None:
            pts.append((x, y))
    return pts


def _json_charts(data):
    """Ряды для графиков внутри JSON (так страницы-приложения отдают историю цен): [(название, [(дата, значение)])].
    Понимает {name, data:[[x,y]…]}, {labels:[…], datasets/series:[{label/name, data:[…]}]} и просто [[x,y]…]."""
    found = []

    def walk(node, key="", depth=0):
        if depth > 8:
            return
        if isinstance(node, dict):
            labels = next((node[k] for k in ("labels", "categories", "dates", "x", "xAxis") if
                           isinstance(node.get(k), list) and node[k]), None)
            groups = next((node[k] for k in ("datasets", "series", "data") if isinstance(node.get(k), list)), None)
            if labels and groups and all(isinstance(g, dict) for g in groups):
                xs = [_x_value(x) for x in labels]
                for i, g in enumerate(groups):
                    ys = g.get("data")
                    if isinstance(ys, list) and len(ys) == len(xs) and all(not isinstance(y, (list, dict)) for y in ys):
                        pts = [(x, _num_value(y)) for x, y in zip(xs, ys) if x is not None and _num_value(y) is not None]
                        if pts:
                            found.append((str(g.get("label") or g.get("name") or f"Ряд {i + 1}"), pts))
                return
            if isinstance(node.get("data"), list):
                pts = _points(node["data"])
                if len(pts) >= 3:
                    found.append((str(node.get("name") or node.get("label") or node.get("title") or key or "Значение"),
                                  pts))
                    return
            for k, v in node.items():
                walk(v, str(k), depth + 1)
        elif isinstance(node, list):
            pts = _points(node)
            if len(pts) >= 3 and len(pts) >= 0.8 * len(node):
                found.append((key or "Значение", pts))
                return
            for v in node:
                walk(v, key, depth + 1)
    walk(data)
    return found


def _json_tables(data) -> list[list[dict]]:
    """Записи из JSON, в том числе вложенные (годы → месяцы → недели): строки самого нижнего уровня получают
    поля родителей (год, месяц). Списки на одинаковом месте (недели каждого месяца) складываются вместе.
    Поле родителя, одинаковое во всех строках (название набора), не нужно — оно не различает ряды.
    → таблицы записей, самые большие сначала."""
    groups, inherited_keys = {}, {}

    def scalars(d):
        return {k: v for k, v in d.items() if not isinstance(v, (dict, list))}

    def walk(node, path, inherited, depth):
        if depth > 10:
            return
        if isinstance(node, list):
            if node and all(isinstance(x, dict) for x in node):
                for item in node:
                    own = scalars(item)
                    row = {**inherited, **own}
                    groups.setdefault(path, []).append(row)
                    inherited_keys.setdefault(path, set()).update(k for k in inherited if k not in own)
                    for k, v in item.items():
                        if isinstance(v, (list, dict)):
                            walk(v, f"{path}/{k}", row, depth + 1)
            else:
                for v in node:
                    if isinstance(v, (list, dict)):
                        walk(v, path, inherited, depth + 1)
        elif isinstance(node, dict):
            here = {**inherited, **scalars(node)}
            for k, v in node.items():
                if isinstance(v, (list, dict)):
                    walk(v, f"{path}/{k}", here, depth + 1)
    walk(data, "", {}, 0)
    for path, rows in groups.items():
        for k in inherited_keys.get(path, ()):
            vals = {str(r.get(k)) for r in rows}
            if len(vals) == 1 and _num_value(rows[0].get(k)) is None:
                for r in rows:
                    r.pop(k, None)

    def score(rows):
        nums = sum(1 for r in rows[:200] for v in r.values() if _num_value(v) is not None)
        return (len(rows) if nums else 0, nums)
    return [rows for rows in sorted(groups.values(), key=score, reverse=True) if score(rows)[0]]


YEAR_KEY = re.compile(r"year|^yr$|år|год", re.I)
WEEK_KEY = re.compile(r"week|^wk$|uke|нед", re.I)
MONTH_KEY = re.compile(r"month|^mnd$|måned|мес", re.I)


def _int_in(v, lo, hi):
    m = re.search(r"\d+", str(v or ""))
    n = int(m.group(0)) if m else None
    return n if n is not None and lo <= n <= hi else None


def _add_date(keys, rows):
    """Нет столбца с датой, но есть год и неделя (или месяц) — собрать дату: неделя → её воскресенье,
    месяц → последний день. Год/неделя/месяц тогда убираются (иначе стали бы «числами»)."""
    def col(rx, lo, hi):
        found = []
        for j, k in enumerate(keys):
            vals = [r[j] for r in rows if r[j]]
            if rx.search(k) and vals and all(_int_in(v, lo, hi) is not None for v in vals):
                found.append((not all(re.fullmatch(r"\d+", v) for v in vals), j))
        return min(found)[1] if found else None   # сначала столбец из одних чисел («32», а не «Weeks 32-35»)
    if any(month_of(r[j]) for j in range(len(keys)) for r in rows[:5] if r[j] and not re.fullmatch(r"\d{1,4}", r[j])):
        return keys, rows                       # дата уже есть
    y = col(YEAR_KEY, 1990, 2100)
    if y is None:
        return keys, rows
    w = col(WEEK_KEY, 1, 53)
    m = next((j for j, k in enumerate(keys) if MONTH_KEY.search(k) and all(
        _int_in(r[j], 1, 12) or P.month_from_word(str(r[j])[:3]) for r in rows if r[j])), None)
    if w is None and m is None:
        return keys, rows
    # подписи периода родителей («Aug», «Weeks 32-35») после сборки даты не нужны — иначе дробили бы ряды
    drop = {y, w, m} | {j for j, k in enumerate(keys) if (WEEK_KEY.search(k) or MONTH_KEY.search(k))
                        and not any(T._is_num(r[j]) for r in rows if r[j])}
    out = []
    for r in rows:
        year = _int_in(r[y], 1990, 2100)
        d = None
        try:
            if w is not None and _int_in(r[w], 1, 53):        # неделя важнее месяца (месяц — от родителя)
                d = dt.date.fromisocalendar(year, _int_in(r[w], 1, 53), 7).isoformat()
            elif m is not None and r[m]:
                mon = _int_in(r[m], 1, 12) or P.month_from_word(str(r[m])[:3])
                d = P.month_end(year, mon).isoformat()
        except (ValueError, TypeError):
            d = None
        out.append([d or ""] + [c for j, c in enumerate(r) if j not in drop])
    return ["Дата"] + [k for j, k in enumerate(keys) if j not in drop], out


def tables_in(text) -> list:
    """Таблицы с числами в ответе: HTML-таблицы, список записей из JSON или ряды графиков из JSON."""
    s = (text or "").lstrip()
    if s[:1] in "[{":
        try:
            data = json.loads(s)
        except ValueError:
            return []
        out = []
        for recs in _json_tables(data)[:3]:
            keys = []
            for r in recs:
                keys += [k for k, v in r.items() if k not in keys and not isinstance(v, (dict, list))]
            rows = [["" if r.get(k) is None else str(r.get(k)) for k in keys] for r in recs]
            keys, rows = _add_date(keys, rows)
            if any(T._is_num(c) for row in rows for c in row) and len(keys) >= 2:
                out.append(T.Table("html", rows, "", len(out), keys))
        charts = _json_charts(data)
        if charts:
            names, by_x = [], {}
            for name, pts in charts:
                while name in names:
                    name += " (2)"
                names.append(name)
                for x, y in pts:
                    by_x.setdefault(x, {})[name] = y
            rows = [[x] + [str(by_x[x].get(n, "")) for n in names] for x in sorted(by_x)]
            if len(rows) >= 3:
                out.append(T.Table("html", rows, "График", len(out), ["Дата"] + names))
        return out
    try:
        return T.html_tables(W.parse_html(text))
    except Exception:  # noqa: BLE001
        return []


def result_table(text, spec=None):
    """Таблица с данными в ответе: по подписи или номеру из spec, иначе — самая большая."""
    tables = [t for t in tables_in(text) if t.rows]
    if not tables:
        return None
    if spec and spec.get("title"):
        same = [t for t in tables if t.title == spec["title"]]
        if same:
            return same[0]
    if spec and spec.get("index") is not None and int(spec["index"]) < len(tables):
        return tables[int(spec["index"])]
    return max(tables, key=lambda t: len(t.rows) * max(len(r) for r in t.rows))


def best_candidate(cands):
    """Из записанных запросов — тот, что вернул таблицу с числами (последний такой, обычно это «поиск»)."""
    best = None
    for i, c in enumerate(cands):
        t = result_table(c.get("text"))
        if t is None:
            continue
        nums = sum(T._is_num(x) for r in t.rows for x in r)
        score = (nums > 0, i if c.get("resource_type") in ("xhr", "fetch", "document") else -1, nums)
        if best is None or score >= best[0]:
            best = (score, c, t)
    return (best[1], best[2]) if best else (None, None)


# --- таблица → ряды ------------------------------------------------------------------------
def month_of(text) -> dt.date | None:
    """Дата строки: «2024-01», «2024/01», «202401», «2024年1月», «Jan 2024», «31.01.2024»."""
    s = str(text or "").strip()
    m = re.fullmatch(r"(\d{4})-?W(\d{1,2})(?:-?\d)?", s, re.I)        # ISO-неделя «2024-W05» → её воскресенье
    if m and 1 <= int(m.group(2)) <= 53:
        try:
            return dt.date.fromisocalendar(int(m.group(1)), int(m.group(2)), 7)
        except ValueError:
            return None
    m = MONTH_ROW.match(s)
    if m and 1990 <= int(m.group(1)) <= 2100 and 1 <= int(m.group(2)) <= 12:
        return P.month_end(int(m.group(1)), int(m.group(2)))
    m = re.fullmatch(r"([A-Za-zА-Яа-яЁё]{3,})\.?\s+(\d{4})", s)
    if m and P.month_from_word(m.group(1)):
        return P.month_end(int(m.group(2)), P.month_from_word(m.group(1)))
    d = P.parse_date(s) if re.search(r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2}", s) else None
    return d


def layout(table) -> dict:
    """Какие столбцы — дата, подписи, единицы, числа (без столбцов с кодами и номерами строк)."""
    rows, width = T._align(table.rows, table.kind)
    head = list(table.header or []) + [""] * width
    head = [h or f"Столбец {j + 1}" for j, h in enumerate(head[:width])]
    prof = T.column_profile(table.rows, table.kind)
    date_col = None
    for j in range(width):
        col = [r[j] for r in rows if r[j]]
        if col and not CODE_HEAD.search(head[j]) and sum(1 for c in col if month_of(c)) >= 0.6 * len(col):
            date_col = j
            break
    unit_col = next((j for j in range(width) if UNIT_HEAD.search(head[j]) and j != date_col), None)
    def code_like(j):
        """Столбец с кодом: одинаковое целое без разделителей во всех строках («27111100»)."""
        col = {r[j].strip() for r in rows if r[j].strip()}
        return len(rows) > 1 and len(col) == 1 and re.fullmatch(r"\d{4,}", next(iter(col)))

    values = [j for j, p in enumerate(prof) if j != date_col and j != unit_col and p["num"] >= 0.6
              and not CODE_HEAD.search(head[j]) and not code_like(j)]
    labels = [j for j in range(width) if j not in values and j not in (date_col, unit_col)
              and any(r[j] for r in rows) and not re.fullmatch(r"(序号|No\.?|№|#)", head[j], re.I)]
    return {"head": head, "date": date_col, "unit": unit_col, "values": values, "labels": labels, "rows": rows}


def unit_for(header, row_unit=""):
    m = re.search(r"([A-Za-zА-Яа-я$€₽]{1,4})\s*/\s*(kg|кг|t|т|tonne|ton|lb|bbl|MWh|л|l)\b", header or "", re.I)
    if m:
        return f"{m.group(1)}/{m.group(2)}"           # «EUR/kg», «руб./т» — цена за единицу
    for rx, cur in CURRENCY:
        if rx.search(header or ""):
            return cur
    if QTY_HEAD.search(header or "") and row_unit:
        return row_unit
    return T.unit_from_header(header)
