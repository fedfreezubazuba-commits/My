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


def tables_in(text) -> list:
    """Таблицы с числами в ответе: HTML-таблицы или список записей из JSON."""
    s = (text or "").lstrip()
    if s[:1] in "[{":
        try:
            recs = _json_records(json.loads(s))
        except ValueError:
            recs = []
        if recs:
            keys = []
            for r in recs:
                keys += [k for k, v in r.items() if k not in keys and not isinstance(v, (dict, list))]
            rows = [["" if r.get(k) is None else str(r.get(k)) for k in keys] for r in recs]
            if any(T._is_num(c) for row in rows for c in row):
                return [T.Table("html", rows, "", 0, keys)]
        return []
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
    for rx, cur in CURRENCY:
        if rx.search(header or ""):
            return cur
    if QTY_HEAD.search(header or "") and row_unit:
        return row_unit
    return T.unit_from_header(header)
