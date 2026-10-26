"""
Расписание показателей (раздел 10 ТЗ).

Строка расписания в jobs.yaml — одно или несколько правил через «;» (время после «@» — московское,
необязательно, по умолчанию 06:00):
  auto                — по мере выхода данных (по умолчанию)
  daily@09:30         — каждый день
  weekly:Wed          — по дням недели: weekly:Mon,Wed,Fri@10:00 (Mon..Sun или пн..вс)
  monthly:15@12:00    — раз в месяц в указанное число
  monthly:last        — в последний день месяца; workday:last — в последний рабочий день
  workday:5@10:00     — в N-й рабочий день месяца (по производственному календарю, workdays.py)
  quarterly:wd10@10:00 — 10-й рабочий день после окончания квартала (quarterly:d25 — 25-й календарный)
  yearly:03-20@19:00  — раз в год, 20 марта
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
    ("comtrade", P.MONTH): 75, ("web_form", P.MONTH): 20,
}
GENERIC_LAG = {P.DAY: 1, P.WEEK: 4, P.DECADE: 5, P.MONTH: 30, P.QUARTER: 60, P.HALF: 90, P.YEAR: 120}
# как часто проверять до ожидаемой даты
RARE_DAYS = {P.DAY: 1, P.WEEK: 2, P.DECADE: 3, P.MONTH: 7, P.QUARTER: 14, P.HALF: 14, P.YEAR: 30}

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
    """Одно правило → (вид, параметр, время (часы, минуты) по Москве или None).
    Несколько правил через «;» разбирает parse_rules."""
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
    m = re.fullmatch(r"(monthly|workday):(\d{1,2}|last)", s)
    if m:
        kind, n = m.group(1), m.group(2)
        if n == "last":
            return kind, "last", at
        if 1 <= int(n) <= (31 if kind == "monthly" else 23):
            return kind, int(n), at
    m = re.fullmatch(r"quarterly:(wd|d)(\d{1,3})", s)
    if m and 1 <= int(m.group(2)) <= 90:
        return "quarterly", (m.group(1), int(m.group(2))), at
    m = re.fullmatch(r"yearly:(\d{1,2})-(\d{1,2})", s)
    if m and 1 <= int(m.group(1)) <= 12 and 1 <= int(m.group(2)) <= 31:
        return "yearly", (int(m.group(1)), int(m.group(2))), at
    m = re.fullmatch(r"every:(\d+)h", s)
    if m and int(m.group(1)) >= 1:
        if at:
            raise ValueError("Для «каждые N часов» время не задаётся")
        return "every", int(m.group(1)), None
    raise ValueError(f"Непонятное расписание «{schedule}»")


def parse_rules(schedule) -> list:
    """«weekly:Mon@10:00;monthly:15@12:00» → [(вид, параметр, время), …]; «auto» → [("auto", None, None)]."""
    parts = [x for x in str(schedule or "auto").split(";") if x.strip()] or ["auto"]
    rules = [parse(x) for x in parts]
    if len(rules) > 1 and any(r[0] == "auto" for r in rules):
        raise ValueError("«Автоматически» нельзя сочетать с другими правилами")
    return rules


def make(kind, arg=None, at=None) -> str:
    """Правило обратно в строку для jobs.yaml."""
    if kind == "weekly":
        s = "weekly:" + ",".join(WEEKDAY_EN[d] for d in sorted(arg))
    elif kind in ("monthly", "workday"):
        s = f"{kind}:{arg}"
    elif kind == "quarterly":
        s = f"quarterly:{arg[0]}{arg[1]}"
    elif kind == "yearly":
        s = f"yearly:{arg[0]:02d}-{arg[1]:02d}"
    elif kind == "every":
        return f"every:{arg}h"
    else:
        s = kind
    return s + (f"@{at[0]:02d}:{at[1]:02d}" if at and kind != "auto" else "")


def join_rules(rules) -> str:
    return ";".join(make(*r) for r in rules) or "auto"


MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
              "октября", "ноября", "декабря"]


def human_rule(kind, arg, at) -> str:
    t = f" в {at[0]:02d}:{at[1]:02d}" if at else ""
    if kind == "auto":
        return "автоматически"
    if kind == "daily":
        return "каждый день" + t
    if kind == "weekly":
        if len(arg) == 1:
            return f"раз в неделю, в {WEEKDAY_RU[arg[0]]}" + t
        if tuple(arg) == (0, 1, 2, 3, 4):
            return "по будням" + t
        return "по дням: " + ", ".join(WEEKDAY_SHORT[d] for d in arg) + t
    if kind == "monthly":
        return ("раз в месяц, в последний день" if arg == "last" else f"раз в месяц, {arg}-го") + t
    if kind == "workday":
        return ("в последний рабочий день месяца" if arg == "last" else f"{arg}-й рабочий день месяца") + t
    if kind == "quarterly":
        what = "рабочий день" if arg[0] == "wd" else "день"
        return f"раз в квартал, {arg[1]}-й {what} после окончания квартала" + t
    if kind == "yearly":
        return f"раз в год, {arg[1]} {MONTHS_GEN[arg[0] - 1]}" + t
    return f"каждые {arg} ч"


def human(schedule) -> str:
    return "; ".join(human_rule(*r) for r in parse_rules(schedule))


def _at(day: dt.date, at) -> dt.datetime:
    h, m = at or (SLOT_HOUR, 0)
    return dt.datetime(day.year, day.month, day.day, h, m)


def _prev_month(y, m):
    return (y, m - 1) if m > 1 else (y - 1, 12)


def _quarter_slot(year, q, arg):
    """N-й (рабочий) день после окончания квартала q (1..4) года year."""
    end = P.quarter_end(year, q)
    kind, n = arg
    return workdays.add_workdays(end, n) if kind == "wd" else end + dt.timedelta(days=n)


def last_slot(kind, arg, now: dt.datetime, at=None) -> dt.datetime:
    """Последний момент «пора проверять», не позже now (все времена — в одном поясе)."""
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
    if kind in ("monthly", "workday"):
        def slot_in(y, m):
            if kind == "monthly":
                day = P.month_end(y, m).day if arg == "last" else min(arg, P.month_end(y, m).day)
                return _at(dt.date(y, m, day), at)
            if arg == "last":
                d = P.month_end(y, m)
                while not workdays.is_workday(d):
                    d -= dt.timedelta(days=1)
                return _at(d, at)
            return _at(workdays.nth_workday(y, m, arg), at)
        slot = slot_in(now.year, now.month)
        return slot if slot <= now else slot_in(*_prev_month(now.year, now.month))
    if kind == "quarterly":
        cands = [_at(_quarter_slot(y, q, arg), at) for y in (now.year - 1, now.year) for q in (1, 2, 3, 4)]
        return max(c for c in cands if c <= now)
    if kind == "yearly":
        def slot_in(y):
            mon, day = arg
            return _at(dt.date(y, mon, min(day, P.month_end(y, mon).day)), at)
        slot = slot_in(now.year)
        return slot if slot <= now else slot_in(now.year - 1)
    raise ValueError(kind)


# --- время по Москве ----------------------------------------------------------
MSK = dt.timedelta(hours=3)       # Москва — UTC+3 круглый год


def _local_offset(moment: dt.datetime) -> dt.timedelta:
    try:
        return moment.astimezone().utcoffset() or dt.timedelta(0)
    except (OverflowError, OSError, ValueError):
        return dt.timedelta(0)


def to_msk(local: dt.datetime) -> dt.datetime:
    return local - _local_offset(local) + MSK


def from_msk(msk: dt.datetime) -> dt.datetime:
    return msk - MSK + _local_offset(msk)


def last_rule_slot(rule, now_local: dt.datetime) -> dt.datetime:
    """Последний момент проверки по правилу (время правила — московское), в местном времени компьютера."""
    kind, arg, at = rule
    return from_msk(last_slot(kind, arg, to_msk(now_local), at))


def next_slot(schedule, now: dt.datetime, count=1):
    """Ближайшие плановые проверки по местному времени (для подсказок); None/[] для auto и every."""
    rules = [r for r in parse_rules(schedule) if r[0] not in ("auto", "every")]
    out = []
    probe = now
    for _ in range(count):
        best = None
        for kind, arg, at in rules:
            for days in range(0, 400):
                cand_msk = _at(to_msk(probe).date() + dt.timedelta(days=days), at)
                cand = from_msk(cand_msk)
                if cand > probe and last_slot(kind, arg, cand_msk, at) == cand_msk:
                    best = cand if best is None or cand < best else best
                    break
        if best is None:
            break
        out.append(best)
        probe = best
    if count == 1:
        return out[0] if out else None
    return out


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
    """Моменты выхода данных по местному времени (в графике время московское)."""
    out = [parse_moment(x) for x in job.get("release_dates") or []]
    if state and state.get("release_next"):
        out.append(parse_moment(state["release_next"]))
    return sorted({from_msk(x) for x in out if x})


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


QUIET = (23, 7)          # частые повторы (раз в час, раз в 3 часа) ночью по Москве не делаем


def slots_per_week(rules) -> float:
    """Сколько плановых сроков в неделю дают правила (без «каждые N часов» и «автоматически»)."""
    per = 0.0
    for kind, arg, _t in rules:
        if kind == "daily":
            per += 7
        elif kind == "weekly":
            per += len(arg)
        elif kind in ("monthly", "workday"):
            per += 7 / 30.4
        elif kind == "quarterly":
            per += 7 / 91
        elif kind == "yearly":
            per += 7 / 365
    return per


def retry_interval(rules) -> dt.timedelta:
    """Пауза между повторами, пока после срока не пришли новые данные: сроки несколько раз в неделю —
    раз в час; раз в неделю — раз в 3 часа; раз в месяц и реже — раз в сутки."""
    per = slots_per_week(rules)
    if not per:
        return dt.timedelta(days=1)
    gap_days = 7 / per
    if gap_days <= 3.5:
        return dt.timedelta(hours=1)
    if gap_days <= 8:
        return dt.timedelta(hours=3)
    return dt.timedelta(days=1)


def quiet(now_local: dt.datetime) -> bool:
    h = to_msk(now_local).hour
    return h >= QUIET[0] or h < QUIET[1]


def due_slot(rules, now: dt.datetime) -> dt.datetime | None:
    """Последний наступивший плановый срок по правилам (в местном времени)."""
    slots = [last_rule_slot(r, now) for r in rules if r[0] not in ("auto", "every")]
    return max(slots) if slots else None


def is_due(job, state, now: dt.datetime, snapshot=False, periodicity=None) -> bool:
    """Пора ли проверять показатель.

    Свой график (дни и время): срок наступил, а попытки после него не было (компьютер был выключен,
    не было связи) — проверка при первой возможности. Если после срока новых данных ещё нет — повторы
    (retry_interval), пока данные не придут; пришли — ждём следующий срок по графику."""
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
    rules = parse_rules(job.get("schedule"))
    if rules[0][0] != "auto":
        for kind, arg, _t in rules:
            if kind == "every" and now - last_attempt >= dt.timedelta(hours=arg):
                return True
        slot = due_slot(rules, now)
        if slot is None:
            return False
        if last_attempt < slot:
            return True                       # первая возможность после срока
        last_change = _dt(state.get("last_change"))
        last_success = _dt(state.get("last_success"))
        if last_change is not None and last_change >= slot or snapshot and last_success and last_success >= slot:
            return False                      # данные после срока получены — ждём следующий срок
        gap = retry_interval(rules)
        if gap < dt.timedelta(days=1) and quiet(now):
            return False
        return now - last_attempt >= gap
    if snapshot:
        return last_attempt < last_slot("daily", None, now)
    expected = _dt(state.get("expected_next"))
    if expected is None or now >= expected:
        return last_attempt < last_slot("daily", None, now)
    rare = RARE_DAYS.get(periodicity or P.MONTH, 7)
    return now - last_attempt >= dt.timedelta(days=rare)


def next_check(job, state, now: dt.datetime, snapshot=False, periodicity=None) -> dt.datetime | None:
    """Когда хаб в следующий раз проверит показатель (для подсказок; точность — до запуска планировщика)."""
    if not job.get("enabled", True):
        return None
    st = state or {}
    if is_due(job, st, now, snapshot, periodicity):
        return now
    last = _dt(st.get("last_attempt")) or now
    cands = []
    fails = st.get("fail_count") or 0
    if fails:
        cands.append(last + dt.timedelta(minutes=backoff_minutes(fails)))
    try:
        rules = parse_rules(job.get("schedule"))
    except ValueError:
        return None
    if rules[0][0] != "auto":
        gap = retry_interval(rules)
        cands.append(last + gap)
        for kind, arg, _t in rules:
            if kind == "every":
                cands.append(last + dt.timedelta(hours=arg))
        nxt = next_slot(job.get("schedule"), now, count=2) or []
        cands += nxt
    else:
        exp = _dt(st.get("expected_next"))
        if exp:
            cands.append(exp)
        rare = RARE_DAYS.get(periodicity or P.MONTH, 7)
        cands.append(last + dt.timedelta(days=rare))
        for d in range(0, 3):
            cands.append(_at(now.date() + dt.timedelta(days=d), None))
    cands += [r for r in release_moments(job, st) if r > now]
    cands.append(last + RECHECK)
    # конец «тихих часов»: 7 утра по Москве в ближайшие дни
    for d in range(0, 3):
        cands.append(from_msk(_at(to_msk(now).date() + dt.timedelta(days=d), (QUIET[1], 0))))
    for c in sorted({c for c in cands if c and c > now}):
        if is_due(job, st, c + dt.timedelta(seconds=1), snapshot, periodicity):
            return c
    return None


RETRY_WORDS = {dt.timedelta(hours=1): "каждый час", dt.timedelta(hours=3): "каждые 3 часа",
               dt.timedelta(days=1): "раз в сутки"}


def check_text(job, state, now: dt.datetime, snapshot=False, periodicity=None) -> str:
    """«Новых данных после срока 22.10 16:00 ещё нет — хаб повторяет каждый час… Следующая проверка: …»."""
    st = state or {}
    if not job.get("enabled", True):
        return ""
    parts = []
    try:
        rules = parse_rules(job.get("schedule"))
    except ValueError:
        return ""
    slot = due_slot(rules, now) if rules[0][0] != "auto" else None
    last_change = _dt(st.get("last_change"))
    last_attempt = _dt(st.get("last_attempt"))
    if slot and last_attempt and last_attempt >= slot and not (last_change and last_change >= slot) \
            and not (snapshot and _dt(st.get("last_success")) and _dt(st["last_success"]) >= slot) \
            and not st.get("fail_count"):
        word = RETRY_WORDS.get(retry_interval(rules), "")
        parts.append(f"Новых данных после срока {slot:%d.%m %H:%M} ещё нет — хаб повторяет проверку {word}"
                     + (" (ночью с 23 до 7 — нет)" if retry_interval(rules) < dt.timedelta(days=1) else "")
                     + ", пока они не появятся, затем ждёт следующий срок по графику.")
    nc = next_check(job, st, now, snapshot, periodicity)
    if nc is not None:
        if nc <= now + dt.timedelta(minutes=1):
            parts.append("Следующая проверка — при ближайшем запуске фонового обновления (в течение 15 минут"
                         " после включения компьютера).")
        else:
            parts.append(f"Следующая проверка: {nc:%d.%m.%Y %H:%M}. Если компьютер в это время будет выключен — "
                         "сразу после включения.")
    return " ".join(parts)


def lag_days(source, periodicity, observed_lags):
    if len(observed_lags) >= 2:
        return int(statistics.median(observed_lags))
    return DEFAULT_LAG.get((source, periodicity), GENERIC_LAG.get(periodicity, 30))


def expected_next(store, job) -> tuple[dt.datetime | None, str | None]:
    """(ожидаемая дата новых данных, периодичность самого частого ряда).
    Если есть график выхода — ближайшая дата из него после последних новых данных."""
    best, best_per = None, None
    ends = store.last_period_ends(job["id"])
    by_per = {}
    for s in store.series_of_job(job["id"]):
        if s["periodicity"] in P.ORDER and ends.get(s["code"]):
            by_per.setdefault(s["periodicity"], []).append((dt.date.fromisoformat(ends[s["code"]]), s["code"]))
    for per, items in by_per.items():
        # ряды, которые давно перестали обновляться (товар исключили из наблюдения), срок не определяют:
        # берём самый свежий период этой периодичности и опоздания нескольких «живых» рядов
        newest = max(e for e, _c in items)
        live = [c for e, c in items if e >= newest - dt.timedelta(days=P.typical_days(per))]
        lags = [x for c in live[:20] for x in store.new_period_lags(c)]
        lag = lag_days(job["source"], per, lags)
        exp = dt.datetime.combine(P.next_period_end(per, newest) + dt.timedelta(days=lag), dt.time(SLOT_HOUR))
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
