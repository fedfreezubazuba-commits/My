"""
Таблицы на страницах и в файлах: поиск, выбор нужной, превращение в ряды.

Виды таблиц:
  html   — обычная <table>;
  cards  — «плитки»: повторяющиеся одинаковые блоки с подписью и числами
           (так сделаны ставки на Fearnleys);
  file   — лист Excel, CSV или таблица из PDF.

Режимы:
  snapshot — каждая строка таблицы становится рядом; дата — из столбца с датой в строке,
             с самой страницы (дата отчёта, «Week 42 - October 14, 2026») или дата получения;
  history  — в таблице есть даты: каждая строка — дата, каждый числовой столбец — ряд.
"""
import datetime as dt
import hashlib
import re

from ..core import periods as P
from ..core.config import slug
from ..core.errors import StructureError
from .base import FetchResult, Obs, SeriesInfo

DATE_RE = re.compile(r"^\s*(\d{1,2}[./]\d{1,2}([./]\d{2,4})?|\d{4}-\d{2}-\d{2}|\d{1,2}\s+[а-яёa-z]{3,}\.?(\s+\d{4})?"
                     r"(\s+\d{1,2}:\d{2})?|\d{1,2}:\d{2})\s*$", re.I)
SHORT_DATE_RE = re.compile(r"\s*(\d{1,2})[./](\d{1,2})\s*")
PCT_RE = re.compile(r"%")


def _is_date(s):
    """Ячейка — дата или время. «12.63» и «14.61» — числа (доходность), а не «день.месяц»."""
    if not DATE_RE.match(s or ""):
        return False
    m = SHORT_DATE_RE.fullmatch(s)
    return not m or 1 <= int(m.group(1)) <= 31 and 1 <= int(m.group(2)) <= 12
SIGNED_RE = re.compile(r"^\s*[-+−–]")


class Table:
    def __init__(self, kind, rows, title="", index=0, header=None, locator=""):
        self.kind = kind            # html | cards | file
        self.rows = rows            # [[текст ячейки, …], …] — только строки данных
        self.title = title          # подпись: заголовок над таблицей / строка-заголовок
        self.index = index          # номер среди таблиц того же вида
        self.header = header or []  # заголовки столбцов, если есть
        self.locator = locator

    def __repr__(self):
        return f"<Table {self.kind}#{self.index} «{self.title[:40]}» {len(self.rows)} строк>"

    def preview(self, n=5):
        return [self.header] + self.rows[:n] if self.header else self.rows[:n]


def norm_text(el) -> str:
    """Текст элемента; соседние куски через пробел («Биржевые цены<br>на сталь» → «… цены на сталь»)."""
    return " ".join(" ".join(el.itertext()).split())


def _cells(tr):
    return [norm_text(c) for c in tr.xpath("./td|./th")]


NUM_TOKEN = re.compile(r"[-+−–]?\d[\d\s\u00a0]*(?:[.,]\d+)*")


def _is_num(s):
    """Ячейка — число (с валютой, %, штрихом): «$71,778», «-0,6%», «280'». «5 yr old» — не число."""
    s = (s or "").strip()
    m = NUM_TOKEN.search(s)
    if not m:
        return False
    core = re.sub(r"\s", "", s)
    digits = re.sub(r"\s", "", m.group(0))
    return len(digits) >= 0.5 * len(core)


def _span(cell, name):
    try:
        return max(1, min(int(cell.get(name) or 1), 50))
    except ValueError:
        return 1


def _is_head_row(tr, cells):
    """Строка шапки по разметке: внутри <thead> или целиком из <th> (подписи «0.25 0.5 1 …» — тоже шапка)."""
    parent = tr.getparent()
    if parent is not None and parent.tag == "thead":
        return True
    return bool(cells) and all(c.tag == "th" for c in cells)


def _grid(trs):
    """Строки таблицы с учётом colspan/rowspan:
    [(тексты по столбцам, число исходных ячеек, тексты для шапки, шапка по разметке)].
    Для шапки объединённая ячейка повторяется во всех своих столбцах («Цена, руб.» над «Рыночная/Первая/…»),
    для данных — только в первом (число не размножается по соседним столбцам)."""
    out, carry = [], {}          # carry: столбец -> (оставшихся строк, текст)
    for tr in trs:
        cells = tr.xpath("./td|./th")
        is_head = _is_head_row(tr, cells)
        data, head, j = [], [], 0
        queue = list(cells)
        while queue or any(c >= j for c in carry):
            if j in carry:
                left, text = carry[j]
                data.append(text)
                head.append(text)
                carry[j] = (left - 1, text)
                if left - 1 <= 0:
                    del carry[j]
                j += 1
                continue
            if not queue:
                break
            c = queue.pop(0)
            text = norm_text(c)
            cs, rs = _span(c, "colspan"), _span(c, "rowspan")
            for k in range(cs):
                data.append(text if k == 0 else "")
                head.append(text)
                if rs > 1:
                    carry[j + k] = (rs - 1, text)
            j += cs
        out.append((data, len(cells), head, is_head))
    return out


def _join_header(parts):
    """Многоуровневая шапка столбца → «Цена, руб. — Рыночная» (повторы и пустые уровни выкидываются)."""
    seen = []
    for x in parts:
        if x and x not in seen:
            seen.append(x)
    return " — ".join(seen)


def html_tables(doc):
    """Все <table> с числами (без вложенных таблиц-раскладок)."""
    out = []
    for t in doc.xpath("//table"):
        inner = t.xpath(".//table")
        if len(inner) > 2 or inner and len(t.text_content()) > 20000:
            continue          # таблица-раскладка страницы (длинная таблица с данными — не раскладка: архивы)
        trs = t.xpath("./tr|./thead/tr|./tbody/tr|./tfoot/tr")
        grid = [g for g in _grid(trs) if any(c for c in g[0])]
        if len(grid) < 2:
            continue
        width = max(len(r) for r, _n, _h, _m in grid)
        marked = any(m for *_x, m in grid) and not all(m for *_x, m in grid)
        title, heads, data = "", [], []
        for r, n_cells, head, is_head in grid:
            if n_cells == 1 and width > 1 and len({c for c in head if c}) <= 1:
                if not data and not title and not heads:
                    title = r[0]
                continue      # строки-заголовки и подвалы на всю ширину
            carried = bool(heads) and r[0] and r[0] == heads[-1][0]     # «Дата» с rowspan над второй строкой шапки
            if not data and (not any(_is_num(c) for c in r[1:]) or marked and is_head or carried):
                heads.append(head)          # строк шапки может быть несколько (двухуровневая)
                continue
            data.append(r)
        header = []
        if heads:
            hw = max(len(h) for h in heads)
            header = [_join_header([h[j] if j < len(h) else "" for h in heads]) for j in range(hw)]
        if not data or not any(_is_num(c) for r in data for c in r[1:]):
            continue
        cap = t.xpath("./caption")
        if cap:
            title = norm_text(cap[0]) or title
        if not title:
            title = _heading_before(t)
        out.append(Table("html", data, title, len(out), header))
    return out


def _signature(el):
    return (el.tag, el.get("class") or "")


def _leaf_texts(el):
    out = []
    for node in el.iter():
        if node.tag in ("script", "style", "svg") or not isinstance(node.tag, str):
            continue
        txt = (node.text or "").strip()
        if txt and not len(node):
            out.append(" ".join(txt.split()))
        elif txt and len(node):
            out.append(" ".join(txt.split()))
    return out


def _heading_before(el, max_levels=3):
    """Подпись над блоком: ближайшие предыдущие короткие тексты (заголовок, пояснение)."""
    for _ in range(max_levels):
        parts = []
        for sib in el.itersiblings(preceding=True):
            if not isinstance(sib.tag, str) or sib.tag in ("script", "style"):
                continue
            if sib.xpath(".//table") or sib.tag == "table":
                break
            txt = norm_text(sib)
            if not txt:
                continue
            if len(txt) > 120:
                break
            if re.search(r"click|нажмите", txt, re.I):
                continue
            parts.insert(0, txt)
            if len(parts) >= 2 or sib.tag in ("h1", "h2", "h3", "h4", "h5"):
                break
        if parts:
            return " ".join(parts)
        el = el.getparent()
        if el is None:
            break
    return ""


def card_grids(doc, min_cards=2):
    """«Плитки»: элемент, у которого ≥2 одинаковых дочерних блока с подписью и числами."""
    out = []
    for el in doc.iter():
        if not isinstance(el.tag, str) or el.tag in ("table", "tr", "tbody", "thead", "ul", "select", "script",
                                                      "style", "svg", "head"):
            continue
        kids = [k for k in el if isinstance(k.tag, str)]
        if len(kids) < min_cards:
            continue
        sigs = {}
        for k in kids:
            sigs.setdefault(_signature(k), []).append(k)
        sig, group = max(sigs.items(), key=lambda kv: len(kv[1]))
        if len(group) < min_cards or len(group) < 0.6 * len(kids) or not sig[1]:
            continue
        rows = [_leaf_texts(k) for k in group]
        good = [r for r in rows if 2 <= len(r) <= 8 and sum(_is_num(c) for c in r) >= 1
                and not _is_num(r[0])]
        if len(good) < max(min_cards, 0.7 * len(rows)) or len(group) < 3 and len(good) < len(group):
            continue
        out.append(Table("cards", good, _heading_before(el), len(out)))
    return out


def all_tables(doc):
    return html_tables(doc) + card_grids(doc)


def pick(tables, spec):
    """Найти таблицу по описанию {kind, title, index}. Сначала по подписи, затем по номеру."""
    kind = spec.get("kind")
    cands = [t for t in tables if not kind or t.kind == kind]
    title = (spec.get("title") or "").strip().lower()
    if title:
        exact = [t for t in cands if t.title.strip().lower() == title]
        if exact:
            return exact[0]
        part = [t for t in cands if title in t.title.lower() or t.title.lower() and t.title.lower() in title]
        if len(part) == 1 or part and spec.get("index") is None:
            return part[0]
        if part:
            return min(part, key=lambda t: abs(t.index - int(spec.get("index") or 0)))
    idx = spec.get("index")
    if idx is not None:
        for t in cands:
            if t.index == int(idx):
                return t
    raise StructureError(f"На странице не найдена таблица «{spec.get('title') or spec.get('index')}»")


# --- разбор столбцов ---------------------------------------------------------
def _align(rows, kind):
    """Для плиток выравниваем строки по правому краю (подпись может занимать разное число ячеек)."""
    width = max(len(r) for r in rows)
    if kind == "cards":
        return [[""] * (width - len(r)) + r for r in rows], width
    return [r + [""] * (width - len(r)) for r in rows], width


def column_profile(rows, kind="html"):
    rows, width = _align(rows, kind)
    prof = []
    for j in range(width):
        col = [r[j] for r in rows if r[j]]
        n = max(len(col), 1)
        prof.append({
            "num": sum(_is_num(c) for c in col) / n,
            "pct": sum(bool(PCT_RE.search(c)) for c in col) / n,
            "date": sum(_is_date(c) for c in col) / n,
            "signed": sum(bool(SIGNED_RE.match(c)) for c in col) / n,
            "filled": len(col) / max(len(rows), 1),
        })
    return prof


def guess_columns(table):
    """(столбец подписи, [столбцы значений], столбец даты или None) — индексы в выровненных строках.

    Последний числовой столбец со знаками «+/-» (или последний в плитке) — это изменение, его не берём.
    В обычной таблице с заголовками берём все числовые столбцы (например, «5 лет» и «10 лет»),
    в плитках и таблицах без заголовков — один."""
    prof = column_profile(table.rows, table.kind)
    cands = [j for j, p in enumerate(prof) if p["num"] >= 0.6 and p["pct"] < 0.5 and p["date"] < 0.5]
    if len(cands) >= 2 and (prof[cands[-1]]["signed"] >= 0.3 or table.kind == "cards" and cands[-1] == len(prof) - 1):
        cands = cands[:-1]
    if not cands:
        raise StructureError("В таблице не найден столбец с числами")
    values = cands if table.header and table.kind != "cards" else cands[-1:]
    date = next((j for j, p in enumerate(prof) if p["date"] >= 0.6 and j not in values), None)
    label = next((j for j, p in enumerate(prof) if p["num"] < 0.5 and p["date"] < 0.5 and j < values[0]), 0)
    return label, values, date


def unit_from_header(name):
    """«Цена, руб. — Рыночная» → «руб.»; «Объем, тонн» → «тонн»; без единиц → "".
    «Срок, лет — 0.25»: «лет» — единицы срока (подписи столбца), а не значений → ""."""
    parts = str(name or "").split(" — ")
    if len(parts) > 1 and _is_num(parts[-1]):
        return ""
    for part in parts:
        m = re.search(r",\s*([^,\d]{1,15})$", part.strip())
        if m:
            return m.group(1).strip()
    return ""


def rows_are_dates(table) -> bool:
    """В каждой строке — дата, а подписей нет: таблица вида «дата | значения» — это история."""
    prof = column_profile(table.rows, table.kind)
    dates = [j for j, p in enumerate(prof) if p["date"] >= 0.6]
    labels = [j for j, p in enumerate(prof) if p["num"] < 0.5 and p["date"] < 0.5 and p["filled"] >= 0.5]
    return bool(dates) and not labels


def unit_from_title(title):
    """«…цены по трехмесячному контракту, $/т» → $/т; «(USD/Day, Weekly Change)» → USD/Day."""
    m = re.search(r"\(([^)]*)\)", title or "")
    if m:
        first = m.group(1).split(",")[0].strip()
        if re.search(r"usd|\$|руб|eur|€|/|%|ws|(?<![а-яё])т\b", first, re.I) and len(first) <= 25:
            return first
    if re.search(r"доходност|бескупон|\bКБД\b|yield", title or "", re.I):
        return "%"                  # кривая доходности, доходность облигаций — проценты
    m = re.search(r"(?:,|в)\s*((?:US)?\$/[а-яa-z.]+|руб\.?/[а-яa-z.]+|US\$/[а-яa-z.]+|USD/[a-z]+|\$/т)", title or "", re.I)
    if m:
        return m.group(1)
    return ""


def row_date(text, today):
    """Дата из ячейки: «19.10», «19 окт 11:30», «13:48», «2026-10-19»."""
    s = (text or "").strip()
    if re.fullmatch(r"\d{1,2}:\d{2}", s):
        return today
    m = re.fullmatch(r"(\d{1,2})[./](\d{1,2})", s)
    if m:
        try:
            d = dt.date(today.year, int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
        return d if d <= today + dt.timedelta(days=1) else d.replace(year=today.year - 1)
    m = re.match(r"(\d{1,2})\s+([а-яёa-z]{3,})\.?(?:\s+(\d{4}))?", s, re.I)
    if m and P.month_from_word(m.group(2)):
        y = int(m.group(3)) if m.group(3) else today.year
        try:
            d = dt.date(y, P.month_from_word(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
        if not m.group(3) and d > today + dt.timedelta(days=1):
            d = d.replace(year=y - 1)
        return d
    return P.parse_date(s)


def table_to_result(table, spec, prefix, url, today, page_dt=None, page_is_week=False, unit=None):
    """Ряды и наблюдения из таблицы по описанию spec (см. web_table / file_link)."""
    mode = spec.get("mode", "snapshot")
    rows, width = _align(table.rows, table.kind)
    res = FetchResult(url=url)
    title_slug = slug(spec.get("code_part") or table.title or f"t{table.index}", 14)
    unit = unit if unit is not None else (spec.get("unit") or unit_from_title(table.title))
    if mode == "history" or rows_are_dates(table):
        return _history(table, spec, prefix, url, res, unit)       # строки — даты: каждый столбец — ряд
    label_col, value_cols, date_col = guess_columns(table)
    label_col = int(spec.get("label_col", label_col))
    if spec.get("value_cols"):
        value_cols = [int(v) for v in spec["value_cols"]]
    elif spec.get("value_col") is not None:
        value_cols = [int(spec["value_col"])]
    if spec.get("date_col") is not None:
        date_col = int(spec["date_col"])
    date_rule = spec.get("date", "auto")      # auto | row | page | today
    per = "week" if (spec.get("periodicity") == "week" or page_is_week and date_rule in ("auto", "page")) else "snapshot"
    keep = set(spec.get("rows") or [])
    header = table.header
    if header and table.kind != "cards":
        header = header + [""] * (width - len(header))
    seen = set()
    for r in rows:
        if table.kind == "cards" and "label_col" not in spec:
            label = " ".join(c for c in r[:value_cols[0]] if c and not DATE_RE.match(c))
        else:
            label = r[label_col] if label_col < len(r) else ""
        label = label.strip()
        if not label or keep and label not in keep:
            continue
        d = None
        if date_rule in ("auto", "row") and date_col is not None and date_col < len(r):
            d = row_date(r[date_col], today)
        if d is None and date_rule in ("auto", "page") and page_dt:
            d = page_dt
        if d is None:
            d = today
        for vc in value_cols:
            value = P.parse_number(r[vc]) if vc < len(r) and _is_num(r[vc]) else None
            if value is None:
                continue
            col = header[vc] if len(value_cols) > 1 and header and vc < len(header) else ""
            full = f"{label} | {col}" if col else label
            key = f"{table.kind}|{(table.title or '').strip()[:80]}|{full}"
            code = f"{prefix}_{title_slug}_{slug(full, 18)}"
            if len(code) > 40:
                code = f"{prefix}_{slug(full, 22)}_{hashlib.sha1(key.encode()).hexdigest()[:4]}"
            if key in seen:
                continue
            seen.add(key)
            name = f"{table.title} | {full}" if table.title else full
            dims = ([("Таблица", table.title)] if table.title else []) + [("Строка", label)]
            if col:
                dims.append(("Столбец", col))
            res.series.append(SeriesInfo(
                key=key, name=name[:200], unit=unit, periodicity="week" if per == "week" else "snapshot",
                code=code, url=url, dims=dims,
                date_rule=("дата отчёта (неделя)" if per == "week" else
                           "дата в строке таблицы" if date_col is not None and date_rule in ("auto", "row")
                           else "дата отчёта на странице" if page_dt else "дата получения")))
            res.obs.append(Obs(key=key, period_end=d,
                               period_label=P.label(P.WEEK, d) if per == "week" else d.isoformat(),
                               value=value, source_date=r[date_col] if date_col is not None and date_col < len(r) else None))
    if not res.obs:
        raise StructureError(f"В таблице «{table.title}» не нашлось ни одного числа")
    return res


def _history(table, spec, prefix, url, res, unit):
    """Строки — даты, числовые столбцы — ряды."""
    rows, width = _align(table.rows, table.kind)
    prof = column_profile(table.rows, table.kind)
    date_col = int(spec.get("date_col", next((j for j, p in enumerate(prof) if p["date"] >= 0.6), 0)))
    header = table.header or [f"Столбец {j + 1}" for j in range(width)]
    value_cols = spec.get("value_cols") or [j for j, p in enumerate(prof) if j != date_col and p["num"] >= 0.6]
    periodicity = spec.get("periodicity") or "day"
    for j in value_cols:
        j = int(j)
        col_name = header[j] if j < len(header) and header[j] else f"Столбец {j + 1}"
        key = f"col|{col_name}"
        code = f"{prefix}_{slug(table.title or 't', 10)}_{slug(col_name, 16)}"
        res.series.append(SeriesInfo(key=key, name=(f"{table.title} | {col_name}" if table.title else col_name)[:200],
                                     unit=spec.get("unit") or unit_from_header(col_name) or unit,
                                     periodicity=periodicity, code=code, url=url,
                                     dims=([("Таблица", table.title)] if table.title else []) + [("Столбец", col_name)],
                                     date_rule="дата из таблицы → конец периода"))
        for r in rows:
            d = P.parse_date(r[date_col]) if date_col < len(r) else None
            if d is None:
                continue
            v = P.parse_number(r[j]) if j < len(r) else None
            if v is None:
                continue
            end = P.end_of_period(periodicity, d) if periodicity in P.ORDER else d
            res.obs.append(Obs(key=key, period_end=end, period_label=P.label(periodicity, end), value=v,
                               source_date=r[date_col]))
    if not res.obs:
        raise StructureError("В таблице не найдено ни одной строки с датой и числом")
    return res
