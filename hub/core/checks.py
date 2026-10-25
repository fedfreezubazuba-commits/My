"""
Проверки перед записью (раздел 12 ТЗ). Если хоть одна не прошла, новые данные
уходят в карантин: в базу с пометкой, но не в посылки моделей.
"""
import datetime as dt
import math

from .base_fmt import fmt


SHOW = 3      # сколько примеров показывать, если замечание касается многих рядов


def unit_key(unit) -> str:
    """Единицы без оформления: «Тонна;^метрическая» и «Тонна; метрическая» — одно и то же."""
    u = " ".join(str(unit or "").replace("^", " ").replace(";", "; ").lower().split())
    return "" if u.strip(" -–—.") == "" else u      # «-» — единицы не были указаны, это не смена


def summary(what, items) -> str:
    """Одно замечание на много рядов: «Сменились единицы у 1250 рядов, например: …»."""
    if len(items) == 1:
        return f"{what} у {items[0]}"
    head = "; ".join(items[:SHOW])
    return f"{what} у {len(items)} рядов, например: {head}" + ("…" if len(items) > SHOW else "")


def run_checks(job, result, store, codes, today=None) -> list[str]:
    """Список проблем (понятным языком). Пустой список — всё в порядке.

    codes — {SeriesInfo.key: код ряда} для результата."""
    today = today or dt.date.today()
    checks = job.get("checks") or {}
    problems = []
    known = store.series_of_job(job["id"])
    had_history = any(store.last_values(r["code"], 1) for r in known)
    state = store.state(job["id"])

    values = [o for o in result.obs if o.value is not None]
    if not values:
        if had_history:
            problems.append("Источник вернул пустой результат, хотя раньше данные были")
        return problems

    bad = [o for o in values if not isinstance(o.value, (int, float)) or math.isnan(o.value) or math.isinf(o.value)]
    if bad:
        problems.append(f"Значение не число: {len(bad)} шт.")

    limit = today + dt.timedelta(days=int(checks.get("future_days", 1)))
    future = [] if checks.get("future_ok") else [o for o in values if o.period_end > limit]
    if future:
        o = max(future, key=lambda x: x.period_end)
        problems.append(f"Дата из будущего: {o.period_end:%d.%m.%Y}")

    partial = getattr(result, "partial", False)    # дозагрузка свежих лет: старые ряды могли не попасть
    if known and checks.get("missing_series", True) and not partial:
        got = set(codes.values())
        active_before = [r for r in known if not r["retired"] and store.last_values(r["code"], 1)]
        lost = [r for r in active_before if r["code"] not in got]
        try:
            result.lost_codes = [r["code"] for r in lost]
        except AttributeError:
            pass
        if lost and store.meta_get(f"edited:{job['id']}"):
            # набор данных недавно меняли — пропавшие ряды сняты пользователем: не тревога, просто больше не ждём
            result.retire_codes = [r["code"] for r in lost]
        elif lost and not job.get("params", {}).get("allow_missing"):
            names = ", ".join(r["name"] or r["code"] for r in lost[:3])
            problems.append(f"Исчезли ряды ({len(lost)}): {names}" + ("…" if len(lost) > 3 else ""))

    by_code = {r["code"]: r for r in known}
    changed = []
    for s in result.series:
        code = codes.get(s.key)
        old = by_code.get(code)
        if old is not None and unit_key(old["unit"]) and unit_key(s.unit) and unit_key(old["unit"]) != unit_key(s.unit):
            changed.append(f"«{s.name}»: было «{old['unit']}», стало «{s.unit}»")
    if changed and checks.get("units", True):
        problems.append(summary("Сменились единицы", changed))

    jump = checks.get("max_jump_pct")
    if jump:
        jumps = []
        for s in result.series:
            code = codes.get(s.key)
            if not code:
                continue
            last = store.last_values(code, 1)
            if not last or last[0]["value"] in (None, 0):
                continue
            prev = last[0]["value"]
            newer = [o for o in values if o.key == s.key and o.period_end.isoformat() >= last[0]["period_end"]]
            for o in newer:
                pct = abs(o.value - prev) / abs(prev) * 100
                if pct > float(jump):
                    jumps.append(f"«{s.name}»: было {fmt(prev)}, стало {fmt(o.value)} (на {pct:.0f}%)")
                    break
        if jumps:
            problems.append(summary(f"Скачок больше {jump}%", jumps))

    prev_rows = state.get("fetch_rows") or 0       # сколько значений вернула прошлая загрузка
    min_ratio = float(checks.get("min_rows_ratio", 0.5))
    if min_ratio and prev_rows >= 10 and len(values) < prev_rows * min_ratio and not partial:
        problems.append(f"Резко уменьшилось число строк: было {prev_rows}, стало {len(values)}")
    return problems


# вид замечания → (пояснение простыми словами, как больше не откладывать из-за него)
KINDS = [
    ("Сменились единицы", "Источник поменял единицы измерения. Чаще всего это просто уточнение подписи "
                          "(«-» → «Тонна»), но если сменился масштаб (рубли → тысячи рублей), числа в моделях "
                          "изменятся в разы.", "units", False),
    ("Скачок", "Значение резко изменилось по сравнению с прошлым. Это может быть реальное событие на рынке — "
               "или ошибка источника.", "max_jump_pct", None),
    ("Исчезли ряды", "В ответе источника не оказалось части рядов, которые раньше были.", "missing_series", False),
    ("Резко уменьшилось", "Источник прислал гораздо меньше данных, чем обычно.", "min_rows_ratio", 0),
    ("Значение не число", "Часть значений не похожа на числа.", None, None),
    ("Дата из будущего", "У части значений дата в будущем: так бывает у прогнозов и у данных за текущий месяц, "
                         "которые выходят до его конца (дата — конец месяца). Если это так — примите: хаб запомнит, "
                         "что показатель содержит прогнозные данные, и больше не будет спрашивать.", "future_ok", True),
]


def explain(reason) -> tuple[str, str | None, object]:
    """(пояснение, ключ проверки, значение «не проверять») для замечания проверки."""
    for prefix, text, key, off in KINDS:
        if reason.startswith(prefix):
            return text, key, off
    return "", None, None


def switch_off(job_checks: dict, key, off) -> dict:
    """Настройки проверок показателя с выключенной проверкой key."""
    out = dict(job_checks or {})
    if off is None:
        out.pop(key, None)
    else:
        out[key] = off
    return out
