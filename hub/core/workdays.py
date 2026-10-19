"""
Производственный календарь России: какие дни рабочие (для расписания «N-й рабочий день месяца»
и для графиков выхода данных, которые сдвигаются из-за праздников).

Правила:
  • суббота и воскресенье — выходные;
  • праздники ТК РФ (ст. 112): 1–8 января, 23 февраля, 8 марта, 1 и 9 мая, 12 июня, 4 ноября;
  • праздник на выходном переносится на следующий рабочий день (кроме январских);
  • переносы, которые правительство утверждает постановлением на каждый год, — в YEARS;
  • свои поправки — файл «Праздники.txt» в рабочей папке, строки вида
        2027-01-11 выходной
        2027-11-01 рабочий
    (записи из файла важнее всего остального).
"""
import datetime as dt
import re

FIXED = [(1, d) for d in range(1, 9)] + [(2, 23), (3, 8), (5, 1), (5, 9), (6, 12), (11, 4)]

# Утверждённые календари: нерабочие будни и рабочие выходные. Для этих лет правило переноса не применяется.
YEARS = {
    2025: {"off": ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07", "2025-01-08",
                   "2025-05-01", "2025-05-02", "2025-05-08", "2025-05-09", "2025-06-12", "2025-06-13",
                   "2025-11-03", "2025-11-04", "2025-12-31"],
           "work": ["2025-11-01"]},
    2026: {"off": ["2026-01-01", "2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08",
                   "2026-01-09", "2026-02-23", "2026-03-09", "2026-05-01", "2026-05-11", "2026-06-12",
                   "2026-11-04", "2026-12-31"],
           "work": []},
}

_user = {"mtime": None, "path": None, "days": {}}


def _user_days(path=None) -> dict:
    """{дата: True — рабочий / False — выходной} из «Праздники.txt»."""
    if path is None:
        try:
            from .paths import workdir
            path = workdir() / "Праздники.txt"
        except Exception:  # noqa: BLE001
            return {}
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    if _user["path"] == str(path) and _user["mtime"] == mtime:
        return _user["days"]
    days = {}
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        m = re.match(r"\s*(\d{4})-(\d{1,2})-(\d{1,2})\s+(\S+)", line)
        if m:
            try:
                d = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                continue
            days[d] = m.group(4).lower().startswith("раб")
    _user.update(mtime=mtime, path=str(path), days=days)
    return days


def _generic_off(year) -> set:
    """Нерабочие будни года по общему правилу (для лет, которых нет в YEARS)."""
    off = set()
    for m, d in FIXED:
        day = dt.date(year, m, d)
        off.add(day)
    for m, d in FIXED:
        day = dt.date(year, m, d)
        if m == 1 or day.weekday() < 5:
            continue
        nxt = day + dt.timedelta(days=1)
        while nxt.weekday() >= 5 or nxt in off:
            nxt += dt.timedelta(days=1)
        off.add(nxt)
    return off


_cache = {}


def _year(year):
    if year not in _cache:
        if year in YEARS:
            y = YEARS[year]
            _cache[year] = ({dt.date.fromisoformat(s) for s in y["off"]}, {dt.date.fromisoformat(s) for s in y["work"]})
        else:
            _cache[year] = (_generic_off(year), set())
    return _cache[year]


def is_workday(day: dt.date, user_path=None) -> bool:
    user = _user_days(user_path)
    if day in user:
        return user[day]
    off, work = _year(day.year)
    if day in work:
        return True
    return day.weekday() < 5 and day not in off


def nth_workday(year, month, n, user_path=None) -> dt.date:
    """N-й рабочий день месяца (если столько нет — последний рабочий)."""
    day = dt.date(year, month, 1)
    last = None
    count = 0
    while day.month == month:
        if is_workday(day, user_path):
            count += 1
            last = day
            if count == n:
                return day
        day += dt.timedelta(days=1)
    return last


def add_workdays(day: dt.date, n, user_path=None) -> dt.date:
    """Через N рабочих дней после day."""
    while n > 0:
        day += dt.timedelta(days=1)
        if is_workday(day, user_path):
            n -= 1
    return day


def known_until() -> int:
    return max(YEARS)
