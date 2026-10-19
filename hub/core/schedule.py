"""
Расписание показателей (раздел 10 ТЗ).

Строка расписания в jobs.yaml (время после «@» необязательно, по умолчанию 06:00):
  auto                — по мере выхода данных (по умолчанию)
  daily@09:30         — каждый день
  weekly:Wed          — по дням недели: weekly:Mon,Wed,Fri@10:00 (Mon..Sun или пн..вс)
  monthly:15@12:00    — раз в месяц в указанное число
  workday:5@10:00     — в N-й рабочий день месяца (по производственному календарю, workdays.py)
  every:6h            — каждые N часов

График выхода (необязательно, в дополнение к расписанию): job["release_dates"] — даты и время,
когда источник обещает новые данные (вставляются из календаря публикаций), и job_state.release_next —
дата, которую сообщил сам источник. В этот момент хаб проверяет показатель, а если данных ещё нет —
перепроверяет каждые 2 часа до конца дня.

Режим auto для статистики: ожидаемая дата новых данных = ближайшая дата из графика выхода, а если её нет —
конец следующего периода + типичный лаг публикации (медиана по истории, пока её нет — значение по
умолчанию). До ожидаемой даты проверяем редко, после — ежедневно; сильное опоздание даёт статус
«Задерживается». Снимки (цены на сайтах) в режиме auto проверяются ежедневно.
"""
import datetime as dt
import re
import statistics

from . import periods as P
from . import workdays

SLOT_HOUR = 6           # «день» начинается в 6 утра — к этому времени данные обычно уже опубликованы
RELEASE_HOUR = 9        # дата выхода без времени — первая проверка в 9 утра
RECHECK = dt.timedelta(hours=2)   # в день выхода: повтор, пока данные не появились
WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
            "пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6}
WEEKDAY_EN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WEEKDAY_RU = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]
WEEKDAY_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

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

CHOICES = [  # готовые варианты (в окнах есть и свой выбор дней и времени)
    ("auto", "Автоматически (по мере выхода данных)"),
    ("daily", "Каждый день"),
    ("weekly:Mon", "Раз в неделю (понедельник)"),
    ("monthly:1", "Раз в месяц (1-го числа)"),
    ("every:6h", "Каждые 6 часов"),
]


def _time(text):
    m = re.fullmatch(r"(\d{1,2})(?:[:.](\d{2}))?", text)
    if not m or int(m.group(1)) > 23 or int(m.group(2) or 0) > 59:
        raise ValueError(f"Непонятное время «{text}». Пишите, например, 10:30")
    return int(m.group(1)), int(m.group(2) or 0)


def _weekday(word):
    w = word.strip().lower()
    for k in (w[:3], w[:2]):
        if k in WEEKDAYS:
            return WEEKDAYS[k]
    raise ValueError(f"Непонятный день недели «{word}». Пишите пн, вт… или Mon, Tue…")


def parse(schedule):
    """→ (вид, параметр, время (часы, минуты) или None)."""
    s = (schedule or "auto").strip().lower().replace(" ", "")
    at = None
    if "@" in s:
        s, t = s.split("@", 1)
        at = _time(t)
    if s in ("auto", "daily", ""):
        if s != "daily" and at:
            raise ValueError("Для режима «автоматически» время не задаётся — выберите дни или «каждый день»")
        return s or "auto", None, at
    m = re.fullmatch(r"weekly:([\wа-яё,]+)", s)
    if m:
        days = tuple(sorted({_weekday(w) for w in m.group(1).split(",") if w}))
        if not days:
            raise ValueError("Не выбраны дни недели")
        return "weekly", days, at
    m = re.fullmatch(r"monthly:(\d{1,2})", s)
    if m and 1 <= int(m.group(1)) <= 31:
        return "monthly", int(m.group(1)), at
    m = re.fullmatch(r"workday:(\d{1,2})", s)
    if m and 1 <= int(m.group(1)) <= 23:
        return "workday", int(m.group(1)), at
    m = re.fullmatch(r"every:(\d+)h", s)
    if m and int(m.group(1)) >= 1:
        if at:
            raise ValueError("Для «каждые N часов» время не задаётся")
        return "every", int(m.group(1)), None
    raise ValueError(f"Непонятное расписание «{schedule}»")


def make(kind, arg=None, at=None) -> str:
    """Обратно в строку для jobs.yaml."""
    if kind == "weekly":
        s = "weekly:" + ",".join(WEEKDAY_EN[d] for d in sorted(arg))
    elif kind in ("monthly", "workday"):
        s = f"{kind}:{arg}"
    elif kind == "every":
        return f"every:{arg}h"
    else:
        s = kind
    return s + (f"@{at[0]:02d}:{at[1]:02d}" if at and kind != "auto" else "")


def human(schedule) -> str:
    kind, arg, at = parse(schedule)
    t = f" в {at[0]:02d}:{at[1]:02d}" if at else ""
    if kind == "auto":
        return "автоматически"
    if kind == "daily":
        return "каждый день" + t
    if kind == "weekly":
        if len(arg) == 1:
            return f"раз в неделю, в {WEEKDAY_RU[arg[0]]}" + t
        if arg == (0, 1, 2, 3, 4):
            return "по будням" + t
        return "по дням: " + ", ".join(WEEKDAY_SHORT[d] for d in arg) + t
    if kind == "monthly":
        return f"раз в месяц, {arg}-го" + t
    if kind == "workday":
        return f"{arg}-й рабочий день месяца" + t
    return f"каждые {arg} ч"


def _at(day: dt.date, at) -> dt.datetime:
    h, m = at or (SLOT_HOUR, 0)
    return dt.datetime(day.year, day.month, day.day, h, m)


def _prev_month(y, m):
    return (y, m - 1) if m > 1 else (y - 1, 12)


def last_slot(kind, arg, now: dt.datetime, at=None) -> dt.datetime:
    """Последний момент «пора проверять», не позже now."""
    today = _at(now.date(), at)
    if kind in ("daily", "auto"):
        return today if now >= today else today - dt.timedelta(days=1)
    if kind == "weekly":
        days = arg if isinstance(arg, (tuple, list)) else (arg,)
        for back in range(0, 8):
            slot = today - dt.timedelta(days=back)
            if slot.weekday() in days and slot <= now:
                return slot
        raise ValueError(arg)
    if kind == "monthly":
        def slot_in(y, m):
            return _at(dt.date(y, m, min(arg, P.month_end(y, m).day)), at)
        slot = slot_in(now.year, now.month)
        return slot if slot <= now else slot_in(*_prev_month(now.year, now.month))
    if kind == "workday":
        slot = _at(workdays.nth_workday(now.year, now.month, arg), at)
        return slot if slot <= now else _at(workdays.nth_workday(*_prev_month(now.year, now.month), arg), at)
    raise ValueError(kind)


def next_slot(schedule, now: dt.datetime) -> dt.datetime | None:
    """Следующая плановая проверка (для подсказки в окне); None для auto и every."""
    kind, arg, at = parse(schedule)
    if kind in ("auto", "every"):
        return None
    for days in range(0, 70):
        cand = _at(now.date() + dt.timedelta(days=days), at)
        if cand > now and last_slot(kind, arg, cand, at) == cand:
            return cand
    return None


# --- график выхода -----------------------------------------------------------
def parse_moment(text) -> dt.datetime | None:
    """«2026-10-21» / «2026-10-21T10:00» / «2026-10-21 10:00» → дата и время."""
    s = str(text or "").strip()
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(?:[T ](\d{1,2}):(\d{2}))?", s)
    if not m:
        return None
    try:
        d = dt.date.fromisoformat(m.group(1))
    except ValueError:
        return None
    return _at(d, (int(m.group(2)), int(m.group(3))) if m.group(2) else (RELEASE_HOUR, 0))


def release_moments(job, state=None) -> list[dt.datetime]:
    out = [parse_moment(x) for x in job.get("release_dates") or []]
    if state and state.get("release_next"):
        out.append(parse_moment(state["release_next"]))
    return sorted({x for x in out if x})


TIME_RE = re.compile(r"(?<![\d.:])([01]?\d|2[0-3])[:.]([0-5]\d)(?![\d.])")
DATE_RES = [
    re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)"),
    re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})(?![\d])"),
    re.compile(r"(?<!\d)(\d{1,2})\s+([а-яё]{3,9})\.?(?:\s+(\d{4}))?", re.I),
]


def parse_release_text(text, today: dt.date | None = None) -> list[str]:
    """Даты из вставленного текста (календарь публикаций, скопированный с сайта, список дат):
    «21.10.2026», «2026-10-21», «21 октября 2026», «21 октября» (год — текущий; если дата уже прошла
    больше чем на полгода — следующий). Время рядом с датой («21.10.2026 19:00») сохраняется.
    → ["2026-10-21T19:00", "2026-11-18", …] по возрастанию, без повторов."""
    today = today or dt.date.today()
    found = {}
    for line in str(text or "").splitlines():
        hits = []          # (начало, конец, дата)
        for i, rx in enumerate(DATE_RES):
            for m in rx.finditer(line):
                if any(a < m.end() and m.start() < b for a, b, _d in hits):
                    continue
                try:
                    if i == 0:
                        d = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                    elif i == 1:
                        y = int(m.group(3))
                        d = dt.date(y + 2000 if y < 100 else y, int(m.group(2)), int(m.group(1)))
                    else:
                        mon = P.month_from_word(m.group(2))
                        if not mon:
                            continue
                        y = int(m.group(3)) if m.group(3) else today.year
                        d = dt.date(y, mon, int(m.group(1)))
                        if not m.group(3) and d < today - dt.timedelta(days=183):
                            d = dt.date(y + 1, mon, int(m.group(1)))
                except ValueError:
                    continue
                hits.append((m.start(), m.end(), d))
        hits.sort()
        for n, (_a, b, d) in enumerate(hits):
            stop = hits[n + 1][0] if n + 1 < len(hits) else len(line)
            t = TIME_RE.search(line[b:min(b + 25, stop)])
            if d.isoformat() in found and not t:
                continue
            found[d.isoformat()] = d.isoformat() + (f"T{int(t.group(1)):02d}:{t.group(2)}" if t else "")
    return [found[k] for k in sorted(found)]


def fmt_moment(text) -> str:
    m = parse_moment(text)
    if not m:
        return str(text)
    return m.strftime("%d.%m.%Y") + (m.strftime(" %H:%M") if "T" in str(text) or " " in str(text).strip() else "")


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


def _release_due(job, state, now, last_attempt) -> bool:
    last_change = _dt(state.get("last_change"))
    for r in release_moments(job, state):
        if r > now:
            break
        if last_attempt < r:
            return True
        # в день выхода данных ещё нет — проверяем снова каждые 2 часа (до 23:00)
        if now.date() == r.date() and now.hour < 23 and (last_change is None or last_change < r) \
                and now - last_attempt >= RECHECK:
            return True
    return False


def is_due(job, state, now: dt.datetime, snapshot=False, periodicity=None) -> bool:
    if not job.get("enabled", True):
        return False
    last_attempt = _dt(state.get("last_attempt"))
    if last_attempt is None:
        return True
    fails = state.get("fail_count") or 0
    if fails:
        return now >= last_attempt + dt.timedelta(minutes=backoff_minutes(fails))
    if _release_due(job, state, now, last_attempt):
        return True
    kind, arg, at = parse(job.get("schedule"))
    if kind == "every":
        return now - last_attempt >= dt.timedelta(hours=arg)
    if kind != "auto":
        return last_attempt < last_slot(kind, arg, now, at)
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
    """(ожидаемая дата новых данных, периодичность самого частого ряда).
    Если есть график выхода — ближайшая дата из него после последних новых данных."""
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
    state = store.state(job["id"])
    # ближайшая дата графика после последних новых данных; давно прошедшие (в график могли попасть даты
    # других показателей) не учитываем — тогда работает оценка по истории
    now = dt.datetime.now()
    since = max(_dt(state.get("last_change")) or now, now - dt.timedelta(days=2))
    planned = [r for r in release_moments(job, state) if r > since]
    if planned:
        best = planned[0]
    return best, best_per


def delay_grace_days(periodicity) -> int:
    return max(5, int(0.3 * GENERIC_LAG.get(periodicity, 30)))


def is_delayed(expected: dt.datetime | None, periodicity, now: dt.datetime) -> bool:
    if not expected:
        return False
    return now > expected + dt.timedelta(days=delay_grace_days(periodicity))
