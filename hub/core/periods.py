"""
Даты и периоды. «Дата» всегда означает конец отчётного периода (раздел 9.6 ТЗ):
месяц → последний день месяца, квартал → 31.03/30.06/30.09/31.12, год → 31.12,
неделя → дата окончания недели по источнику, снимок → дата наблюдения.

Подпись периода: 2025-01 (месяц), 2025-Q1 (квартал), 2025-H1 (полугодие),
2025 (год), 2025-W42 (неделя), 2025-01-15 (день или снимок).
"""
import calendar
import datetime as dt
import re

DAY, WEEK, MONTH, QUARTER, HALF, YEAR = "day", "week", "month", "quarter", "halfyear", "year"
DECADE = "decade"        # декада: 1–10, 11–20, 21–конец месяца; дата — 10-е, 20-е или последний день месяца
PERIODICITY_RU = {DAY: "дневная", WEEK: "недельная", DECADE: "декадная", MONTH: "месячная", QUARTER: "квартальная",
                  HALF: "полугодовая", YEAR: "годовая", "snapshot": "снимки"}
ORDER = [DAY, WEEK, DECADE, MONTH, QUARTER, HALF, YEAR]

MONTHS_RU = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август",
             "сентябрь", "октябрь", "ноябрь", "декабрь"]
# начала слов для родительного падежа и сокращений: «января», «янв», «мая»
RU_PREFIXES = [("янв", 1), ("фев", 2), ("мар", 3), ("апр", 4), ("май", 5), ("мая", 5), ("июн", 6),
               ("июл", 7), ("авг", 8), ("сен", 9), ("окт", 10), ("ноя", 11), ("дек", 12)]
MONTHS_EN = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def month_end(year, month) -> dt.date:
    return dt.date(year, month, calendar.monthrange(year, month)[1])


def quarter_end(year, q) -> dt.date:
    return month_end(year, q * 3)


def label(periodicity, d: dt.date) -> str:
    if periodicity == MONTH:
        return f"{d.year}-{d.month:02d}"
    if periodicity == QUARTER:
        return f"{d.year}-Q{(d.month - 1) // 3 + 1}"
    if periodicity == HALF:
        return f"{d.year}-H{1 if d.month <= 6 else 2}"
    if periodicity == YEAR:
        return str(d.year)
    if periodicity == WEEK:
        y, w, _ = d.isocalendar()
        return f"{y}-W{w:02d}"
    return d.isoformat()


def label_end(period_label) -> dt.date | None:
    """Обратно к label(): «2026-08» → 31.08.2026, «2026-W42» → воскресенье 42-й недели, «2026» → 31.12.2026."""
    p = str(period_label or "").strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p):
            return dt.date.fromisoformat(p)
        m = re.fullmatch(r"(\d{4})-(\d{2})", p)
        if m:
            return month_end(int(m.group(1)), int(m.group(2)))
        m = re.fullmatch(r"(\d{4})-Q([1-4])", p)
        if m:
            return quarter_end(int(m.group(1)), int(m.group(2)))
        m = re.fullmatch(r"(\d{4})-H([12])", p)
        if m:
            return month_end(int(m.group(1)), 6 if m.group(2) == "1" else 12)
        m = re.fullmatch(r"(\d{4})-W(\d{2})", p)
        if m:
            return dt.date.fromisocalendar(int(m.group(1)), int(m.group(2)), 7)
        if re.fullmatch(r"\d{4}", p):
            return dt.date(int(p), 12, 31)
    except ValueError:
        return None
    return None


ROMAN = {"1": "I", "2": "II", "3": "III", "4": "IV"}


def upto_text(period_label) -> str:
    """По какой период пришли данные, по-человечески: «по август 2026», «по неделю с 13.10.2026»,
    «по III кв. 2026», «по I полугодие 2026», «по 2026 год», «по 21.10.2026»."""
    p = str(period_label or "").strip()
    end = label_end(p)
    if end is None:
        return f"по {p}" if p else ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p):
        return f"по {end:%d.%m.%Y}"
    if "-W" in p:
        return f"по неделю с {end - dt.timedelta(days=6):%d.%m.%Y}"
    if "-Q" in p:
        return f"по {ROMAN[p[-1]]} кв. {p[:4]}"
    if "-H" in p:
        return f"по {ROMAN[p[-1]]} полугодие {p[:4]}"
    if len(p) == 4:
        return f"по {p} год"
    return f"по {MONTHS_RU[end.month - 1]} {end.year}"


def rank(period_label: str) -> int:
    """Чем меньше, тем подробнее период (так же, как функция Ранг в M-шаблоне)."""
    p = period_label or ""
    if len(p) == 10:
        return 0
    if "-W" in p:
        return 1
    if "-Q" in p:
        return 3
    if "-H" in p:
        return 4
    if len(p) == 7:
        return 2
    return 5


def end_of_period(periodicity, d: dt.date) -> dt.date:
    """Конец периода, в который попадает дата d."""
    if periodicity == MONTH:
        return month_end(d.year, d.month)
    if periodicity == QUARTER:
        return quarter_end(d.year, (d.month - 1) // 3 + 1)
    if periodicity == HALF:
        return month_end(d.year, 6 if d.month <= 6 else 12)
    if periodicity == YEAR:
        return dt.date(d.year, 12, 31)
    if periodicity == WEEK:
        return d + dt.timedelta(days=6 - d.weekday())
    if periodicity == DECADE:
        return d.replace(day=10) if d.day <= 10 else d.replace(day=20) if d.day <= 20 else month_end(d.year, d.month)
    return d


def decade_end(year, month, n) -> dt.date:
    """Конец n-й декады месяца (1 → 10-е, 2 → 20-е, 3 → последний день)."""
    return dt.date(year, month, 10) if n == 1 else dt.date(year, month, 20) if n == 2 else month_end(year, month)


DECADE_RE = re.compile(r"^\s*(III|II|I|[123])\s*(?:дек\.?|декада)?\s*[./\- ]\s*(\d{1,2})\s*[./\- ]\s*(\d{4})\s*$", re.I)


def parse_decade(text) -> dt.date | None:
    """«II.10.2026», «I.10.2026», «III.09.2026» (номер декады римской цифрой) → конец декады."""
    m = DECADE_RE.match(str(text or ""))
    if not m or not re.fullmatch(r"I{1,3}", m.group(1), re.I):
        return None
    month, year = int(m.group(2)), int(m.group(3))
    if not 1 <= month <= 12:
        return None
    return decade_end(year, month, len(m.group(1)))


def next_period_end(periodicity, d: dt.date) -> dt.date:
    """Конец периода, следующего за периодом с концом d."""
    nxt = d + dt.timedelta(days=1)
    if periodicity in (DAY, "snapshot"):
        return nxt
    if periodicity == WEEK:
        return d + dt.timedelta(days=7)
    return end_of_period(periodicity, nxt)


def typical_days(periodicity) -> int:
    return {DAY: 1, WEEK: 7, DECADE: 10, MONTH: 30, QUARTER: 91, HALF: 182, YEAR: 365}.get(periodicity, 1)


def parse_date(text) -> dt.date | None:
    """Разные записи даты: 2025-01-31, 31.01.2025, 31/01/2025, «14 октября 2026», «October 14, 2026»."""
    if text is None:
        return None
    if isinstance(text, dt.datetime):
        return text.date()
    if isinstance(text, dt.date):
        return text
    dec = parse_decade(text)
    if dec:
        return dec
    s = str(text).strip().lower()
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4}|\d{2})\b", s)
    if m:
        y = int(m.group(3))
        return _safe_date(y + 2000 if y < 100 else y, int(m.group(2)), int(m.group(1)))
    m = re.search(r"\b(\d{1,2})\s+([а-яё]+)\.?\s+(\d{4})", s)
    if m:
        mon = month_from_word(m.group(2))
        if mon:
            return _safe_date(int(m.group(3)), mon, int(m.group(1)))
    m = re.search(r"\b([a-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})", s)
    if m and m.group(1)[:3] in MONTHS_EN:
        return _safe_date(int(m.group(3)), MONTHS_EN.index(m.group(1)[:3]) + 1, int(m.group(2)))
    m = re.search(r"\b(\d{1,2})\s+([a-z]{3,9})\.?\s+(\d{4})", s)
    if m and m.group(2)[:3] in MONTHS_EN:
        return _safe_date(int(m.group(3)), MONTHS_EN.index(m.group(2)[:3]) + 1, int(m.group(1)))
    return None


def _safe_date(y, m, d):
    try:
        return dt.date(y, m, d)
    except ValueError:
        return None


def month_from_word(word) -> int | None:
    """«январь», «января», «янв.», «May», «october» → номер месяца."""
    w = (word or "").strip().lower().rstrip(".")
    if len(w) < 3:
        return None
    for prefix, num in RU_PREFIXES:
        if w.startswith(prefix):
            return num
    if w[:3] in MONTHS_EN:
        return MONTHS_EN.index(w[:3]) + 1
    return None


def week_end_from_label(text, default_year=None, weekday_end=6) -> dt.date | None:
    """«Week 42 - October 14, 2026» → дата из текста, иначе конец ISO-недели 42."""
    d = parse_date(text)
    if d:
        return d
    m = re.search(r"(?:week|неделя|нед\.?)\s*(\d{1,2})", str(text).lower())
    if m and default_year:
        return dt.date.fromisocalendar(default_year, int(m.group(1)), 1) + dt.timedelta(days=weekday_end)
    return None


def parse_number(text):
    """Число из текста: «1 979,50 ₽», «$71,778», «-0,6%», «12863.5», «280'». None, если числа нет."""
    if text is None:
        return None
    if isinstance(text, bool):
        return float(text)
    if isinstance(text, (int, float)):
        return float(text)
    s = str(text).replace(" ", " ").replace(" ", " ").replace(" ", " ").strip()
    s = s.replace("−", "-").replace("–", "-")
    m = re.search(r"\d[\d ]*(?:[.,]\d+)*", s)
    if not m:
        return None
    num = m.group(0).strip().replace(" ", "")
    # минус перед числом, в том числе перед знаком валюты: «-$134», «- 5», «−0,6%»
    neg = bool(re.search(r"-\s*[$€£¥₽]?\s*$", s[:m.start()]))
    # разделители: последняя точка/запятая — десятичная, если после неё не ровно 3 цифры
    # или разделители разные; «1,250» и «1.250» считаем тысячами.
    seps = [c for c in num if c in ".,"]
    if seps:
        last = max(num.rfind("."), num.rfind(","))
        tail = num[last + 1:]
        if len(set(seps)) > 1 or len(seps) == 1 and len(tail) != 3:
            intpart = re.sub(r"[.,]", "", num[:last])
            num = intpart + "." + tail
        elif len(seps) == 1 and len(tail) == 3 and num[last] == ",":
            num = num.replace(",", "")
        elif len(seps) == 1 and len(tail) == 3 and num[last] == ".":
            # «12.863» — неоднозначно; в русских и английских ценах чаще дробь не из 3 знаков,
            # поэтому трактуем как десятичную дробь только при ведущем нуле («0.125»)
            num = num if num.startswith("0") else num.replace(".", "")
        else:
            num = re.sub(r"[.,]", "", num)
    try:
        v = float(num)
    except ValueError:
        return None
    return -v if neg else v
