"""
Столбцы списка «Мои показатели»: рядов, периодичность, история, последнее, обновлено, дельта к графику,
частота проверок, дней до следующей проверки, модели (целиком или частью — через выборки).

Дельта — насколько раньше (−) или позже (+) планового срока пришли последние новые данные:
- свой график (дни и время) — от ближайшего срока графика (прошлого или следующего);
- «автоматически» — от ожидаемой даты: конец последнего периода + обычная задержка выхода данных
  (по прошлым обновлениям, как в schedule.expected_next).
Большая «+» — график проверок раньше, чем данные выходят (лишние повторы); большая «−» — данные лежали
до срока, график можно сдвинуть раньше.
"""
import datetime as dt

from . import periods as P
from . import schedule as S

PER_WORD = {P.DAY: "День", P.WEEK: "Неделя", P.MONTH: "Месяц", P.QUARTER: "Квартал", P.YEAR: "Год"}


def period_word(periodicities) -> str:
    """Периодичность рядов показателя: одна — её название, разные или особые — «Иное»."""
    words = {PER_WORD.get(p, "Иное") for p in periodicities if p}
    if not words:
        return "—"
    return words.pop() if len(words) == 1 else "Иное"


def frequency_word(job, periodicity=None) -> str:
    """Как часто хаб проверяет показатель по графику."""
    try:
        rules = S.parse_rules(job.get("schedule"))
    except ValueError:
        return "—"
    if rules[0][0] == "auto":
        return f"{PER_WORD.get(periodicity, 'Иное')} (авто)" if periodicity else "Авто"
    hours = [arg for kind, arg, _t in rules if kind == "every"]
    per = S.slots_per_week(rules) + sum(7 * 24 / h for h in hours)
    if per >= 4:
        return "День"
    if per >= 0.9:
        return "Неделя"
    if per >= 0.2:
        return "Месяц"
    if per >= 0.07:
        return "Квартал"
    if per > 0:
        return "Год"
    return "Иное"


def history_text(state) -> str:
    a, b = (state.get("first_period") or "")[:4], (state.get("last_period") or "")[:4]
    if not a:
        return "—"
    return a if a == b or not b else f"{a}–{b}"


def delta_text(td: dt.timedelta | None) -> str:
    """«+5 ч», «−2 дн», «0»."""
    if td is None:
        return "—"
    sec = td.total_seconds()
    sign = "+" if sec > 0 else "−" if sec < 0 else ""
    sec = abs(sec)
    if sec < 3600:
        return "0"
    if sec < 86400:
        return f"{sign}{round(sec / 3600)} ч"
    return f"{sign}{round(sec / 86400)} дн"


def lag_text(state, now: dt.datetime) -> str:
    """«Задержка»: сколько дней от конца последнего периода в данных до сегодня («−» — период ещё не кончился,
    например прогноз или данные за текущий месяц)."""
    last = state.get("last_period")
    if not last:
        return "—"
    try:
        days = (now.date() - dt.date.fromisoformat(last[:10])).days
    except ValueError:
        return "—"
    return f"{days} дн" if days >= 0 else f"−{-days} дн"


def planned_moment(store, job, state, when: dt.datetime) -> dt.datetime | None:
    """Плановый срок, с которым сравнивать момент when (приход последних новых данных)."""
    try:
        rules = S.parse_rules(job.get("schedule"))
    except ValueError:
        return None
    if rules[0][0] != "auto" and any(k not in ("auto", "every") for k, _a, _t in rules):
        prev = S.due_slot(rules, when)
        nxt = S.next_slot(job.get("schedule"), when, count=1)
        cands = [x for x in (prev, nxt) if x is not None]
        return min(cands, key=lambda x: abs((when - x).total_seconds())) if cands else None
    # автоматически: конец последнего периода + обычная задержка выхода данных
    ends = store.last_period_ends(job["id"])
    by_per = {}
    for s in store.series_of_job(job["id"]):
        if s["periodicity"] in P.ORDER and ends.get(s["code"]) and not s["retired"]:
            by_per.setdefault(s["periodicity"], []).append((dt.date.fromisoformat(ends[s["code"]]), s["code"]))
    best = None
    for per, items in by_per.items():
        newest = max(e for e, _c in items)
        live = [c for e, c in items if e >= newest][:5]
        lags = [x for c in live for x in store.new_period_lags(c)]
        exp = dt.datetime.combine(newest + dt.timedelta(days=S.lag_days(job["source"], per, lags)),
                                  dt.time(S.SLOT_HOUR))
        if best is None or exp < best:
            best = exp
    return best


def next_days(job, state, now, snapshot=False, periodicity=None) -> str:
    """Сколько дней до следующей проверки: «0» — сегодня (или при ближайшем запуске), «—» — на паузе."""
    nc = S.next_check(job, state, now, snapshot, periodicity)
    if nc is None:
        return "—"
    return str(max(0, (nc.date() - now.date()).days))


def model_links(cfg, store) -> dict:
    """{показатель: «Модель, Другая (часть)»} — по тому, что реально уходит в посылки моделей (подписки
    на весь показатель, выборки, отдельные ряды)."""
    from ..excel import packet
    codes_of = {}
    for s in store.all_series():
        if not s["retired"]:
            codes_of.setdefault(s["job_id"], set()).add(s["code"])
    out = {}
    for m in cfg.models():
        if not m.get("active", True):
            continue
        try:
            plan = set(packet.model_plan(cfg, store, m)[0])
        except Exception:  # noqa: BLE001 — модель с повреждёнными настройками не должна ломать список
            continue
        for job_id, codes in codes_of.items():
            got = codes & plan
            if got:
                out.setdefault(job_id, []).append(m["name"] + ("" if got == codes else " (часть)"))
    return {k: ", ".join(v) for k, v in out.items()}


def row(store, job, state, now, snapshot=False) -> dict:
    """Значения столбцов (кроме названия, источника и моделей)."""
    series = [s for s in store.series_of_job(job["id"]) if not s["retired"]]
    pers = [s["periodicity"] for s in series]
    main = max(set(p for p in pers if p in P.ORDER), key=pers.count, default=None)
    last_change = S._dt(state.get("last_change"))
    planned = planned_moment(store, job, state, last_change) if last_change else None
    return {"count": str(len(series)) if series else "—", "period": period_word(pers),
            "history": history_text(state), "last": state.get("last_label") or "—", "lag": lag_text(state, now),
            "delta": delta_text(last_change - planned if planned else None),
            "freq": frequency_word(job, main), "next": next_days(job, state, now, snapshot, main)}
