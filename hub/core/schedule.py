"""
Расписание показателей (раздел 10 ТЗ).

Строка расписания в jobs.yaml:
  auto        — по мере выхода данных (по умолчанию)
  daily       — раз в день
  weekly:Wed  — раз в неделю в указанный день (Mon..Sun или пн..вс)
  monthly:15  — раз в месяц в указанное число
  every:6h    — каждые N часов

Режим auto для статистики: ожидаемая дата новых данных = конец следующего периода +
типичный лаг публикации (медиана по истории, пока её нет — значение по умолчанию).
До ожидаемой даты проверяем редко, после — ежедневно; сильное опоздание даёт статус
«Задерживается». Снимки (цены на сайтах) в режиме auto проверяются ежедневно.
"""
import datetime as dt
import re
import statistics

from . import periods as P

SLOT_HOUR = 6           # «день» начинается в 6 утра — к этому времени данные обычно уже опубликованы
WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
            "пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6}
WEEKDAY_RU = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]

# типичный лаг публикации (дни от конца периода до появления данных)
DEFAULT_LAG = {
    ("cbr", P.DAY): 2, ("cbr", P.WEEK): 7, ("cbr", P.MONTH): 35, ("cbr", P.QUARTER): 70,
    ("cbr", P.HALF): 80, ("cbr", P.YEAR): 100,
    ("fedstat", P.DAY): 3, ("fedstat", P.WEEK): 10, ("fedstat", P.MONTH): 45, ("fedstat", P.QUARTER): 80,
    ("fedstat", P.HALF): 90, ("fedstat", P.YEAR): 150,
}
GENERIC_LAG = {P.DAY: 1, P.WEEK: 4, P.MONTH: 30, P.QUARTER: 60, P.HALF: 90, P.YEAR: 120}
# как часто проверять до ожидаемой даты
RARE_DAYS = {P.DAY: 1, P.WEEK: 2, P.MONTH: 7, P.QUARTER: 14, P.HALF: 14, P.YEAR: 30}

CHOICES = [  # что показываем в выпадающем списке «Как часто проверять»
    ("auto", "Автоматически (по мере выхода данных)"),
    ("daily", "Каждый день"),
    ("weekly:Mon", "Раз в неделю (понедельник)"),
    ("monthly:1", "Раз в месяц (1-го числа)"),
    ("every:6h", "Каждые 6 часов"),
]


def parse(schedule):
    s = (schedule or "auto").strip().lower()
    if s in ("auto", "daily"):
        return s, None
    m = re.fullmatch(r"weekly:(\w+)", s)
    if m and m.group(1)[:3] in WEEKDAYS or m and m.group(1)[:2] in WEEKDAYS:
        k = m.group(1)[:3] if m.group(1)[:3] in WEEKDAYS else m.group(1)[:2]
        return "weekly", WEEKDAYS[k]
    m = re.fullmatch(r"monthly:(\d{1,2})", s)
    if m and 1 <= int(m.group(1)) <= 31:
        return "monthly", int(m.group(1))
    m = re.fullmatch(r"every:(\d+)\s*h", s)
    if m and int(m.group(1)) >= 1:
        return "every", int(m.group(1))
    raise ValueError(f"Непонятное расписание «{schedule}»")


def human(schedule) -> str:
    kind, arg = parse(schedule)
    if kind == "auto":
        return "автоматически"
    if kind == "daily":
        return "каждый день"
    if kind == "weekly":
        return f"раз в неделю, в {WEEKDAY_RU[arg]}"
    if kind == "monthly":
        return f"раз в месяц, {arg}-го"
    return f"каждые {arg} ч"


def last_slot(kind, arg, now: dt.datetime) -> dt.datetime:
    """Последний момент «пора проверять», не позже now."""
    today = now.replace(hour=SLOT_HOUR, minute=0, second=0, microsecond=0)
    if kind == "daily" or kind == "auto":
        return today if now >= today else today - dt.timedelta(days=1)
    if kind == "weekly":
        back = (now.weekday() - arg) % 7
        slot = today - dt.timedelta(days=back)
        return slot if slot <= now else slot - dt.timedelta(days=7)
    if kind == "monthly":
        def slot_in(y, m):
            day = min(arg, P.month_end(y, m).day)
            return dt.datetime(y, m, day, SLOT_HOUR)
        slot = slot_in(now.year, now.month)
        if slot > now:
            y, m = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
            slot = slot_in(y, m)
        return slot
    raise ValueError(kind)


def backoff_minutes(fail_count) -> int:
    """Пауза перед повтором после сбоя: 15 мин, 30, 60, 120, 240, затем раз в 6 часов."""
    if fail_count <= 0:
        return 0
    return min(15 * 2 ** (fail_count - 1), 360)


def _dt(s):
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s)
    except ValueError:
        return None


def is_due(job, state, now: dt.datetime, snapshot=False, periodicity=None) -> bool:
    if not job.get("enabled", True):
        return False
    last_attempt = _dt(state.get("last_attempt"))
    if last_attempt is None:
        return True
    fails = state.get("fail_count") or 0
    if fails:
        return now >= last_attempt + dt.timedelta(minutes=backoff_minutes(fails))
    kind, arg = parse(job.get("schedule"))
    if kind == "every":
        return now - last_attempt >= dt.timedelta(hours=arg)
    if kind in ("daily", "weekly", "monthly"):
        return last_attempt < last_slot(kind, arg, now)
    # auto
    if snapshot:
        return last_attempt < last_slot("daily", None, now)
    expected = _dt(state.get("expected_next"))
    if expected is None or now >= expected:
        return last_attempt < last_slot("daily", None, now)
    rare = RARE_DAYS.get(periodicity or P.MONTH, 7)
    return now - last_attempt >= dt.timedelta(days=rare)


def lag_days(source, periodicity, observed_lags):
    if len(observed_lags) >= 2:
        return int(statistics.median(observed_lags))
    return DEFAULT_LAG.get((source, periodicity), GENERIC_LAG.get(periodicity, 30))


def expected_next(store, job) -> tuple[dt.datetime | None, str | None]:
    """(ожидаемая дата новых данных, периодичность самого частого ряда)."""
    best, best_per = None, None
    for s in store.series_of_job(job["id"]):
        per = s["periodicity"]
        if per not in P.ORDER:
            continue
        last = store.last_values(s["code"], 1)
        if not last:
            continue
        last_end = dt.date.fromisoformat(last[0]["period_end"])
        nxt = P.next_period_end(per, last_end)
        lag = lag_days(job["source"], per, store.new_period_lags(s["code"]))
        exp = dt.datetime.combine(nxt + dt.timedelta(days=lag), dt.time(SLOT_HOUR))
        if best is None or exp < best:
            best, best_per = exp, per
    return best, best_per


def delay_grace_days(periodicity) -> int:
    return max(5, int(0.3 * GENERIC_LAG.get(periodicity, 30)))


def is_delayed(expected: dt.datetime | None, periodicity, now: dt.datetime) -> bool:
    if not expected:
        return False
    return now > expected + dt.timedelta(days=delay_grace_days(periodicity))
