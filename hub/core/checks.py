"""
Проверки перед записью (раздел 12 ТЗ). Если хоть одна не прошла, новые данные
уходят в карантин: в базу с пометкой, но не в посылки моделей.
"""
import datetime as dt
import math

from .base_fmt import fmt


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
    future = [o for o in values if o.period_end > limit]
    if future:
        o = max(future, key=lambda x: x.period_end)
        problems.append(f"Дата из будущего: {o.period_end:%d.%m.%Y}")

    if known and checks.get("missing_series", True):
        got = set(codes.values())
        active_before = [r for r in known if store.last_values(r["code"], 1)]
        lost = [r for r in active_before if r["code"] not in got]
        if lost and not job.get("params", {}).get("allow_missing"):
            names = ", ".join(r["name"] or r["code"] for r in lost[:3])
            problems.append(f"Исчезли ряды ({len(lost)}): {names}" + ("…" if len(lost) > 3 else ""))

    by_code = {r["code"]: r for r in known}
    for s in result.series:
        code = codes.get(s.key)
        old = by_code.get(code)
        if old is not None and (old["unit"] or "") and (s.unit or "") and old["unit"].strip() != s.unit.strip():
            problems.append(f"Сменились единицы у «{s.name}»: было «{old['unit']}», стало «{s.unit}»")

    jump = checks.get("max_jump_pct")
    if jump:
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
                    problems.append(f"Скачок {pct:.0f}% у «{s.name}»: было {fmt(prev)}, стало {fmt(o.value)}"
                                    f" (порог {jump}%)")
                    break

    prev_rows = state.get("n_rows") or 0
    min_ratio = float(checks.get("min_rows_ratio", 0.5))
    if prev_rows >= 10 and len(values) < prev_rows * min_ratio and not job.get("params", {}).get("partial"):
        problems.append(f"Резко уменьшилось число строк: было {prev_rows}, стало {len(values)}")
    return problems
