"""
Что изменится, если поменять набор данных показателя («✎ Изменить набор данных»): какие ряды перестанут
приходить и в какие модели они сейчас отдаются. Хаб предупреждает об этом до сохранения, а сами ряды
больше не ждёт — так при следующей загрузке не будет тревоги «исчезли ряды».

Предсказание по параметрам: Банк России — по номерам показателей и разрезов в ключе ряда; ЕМИСС — по
названиям значений в разрезах ряда (номера значений в ключе ряда у ЕМИСС другие, чем в форме выбора).
Если что-то не угадано, первая полная загрузка после изменения сама перестанет ждать пропавшие ряды
(meta edited:<показатель>), без отложенных данных.
"""
import json


def _key(text) -> str:
    return " ".join(str(text or "").replace("^", " ").split()).lower().strip(" .")


def dropped_series(store, job, new_params, info=None) -> list:
    """Ряды показателя, которых с новыми параметрами не будет (строки таблицы series)."""
    rows = [r for r in store.series_of_job(job["id"]) if not r["retired"]]
    src = job.get("source")
    if src == "cbr":
        def ids(name):
            return {str(x) for x in (new_params.get(name) or [])}
        inds, m1, m2 = ids("indicators"), ids("m1"), ids("m2")
        out = []
        for r in rows:
            parts = (r["ident"] or "").split("|")
            if len(parts) < 3:
                continue
            i, a, b = parts[0], parts[1], parts[2]
            if inds and i not in inds or m1 and a not in ("None", "") and a not in m1 \
                    or m2 and b not in ("None", "") and b not in m2:
                out.append(r)
        return out
    if src == "fedstat" and info:
        old = {str(k): {str(v) for v in vals} for k, vals in ((job.get("params") or {}).get("select") or {}).items()}
        keep = {}                                   # название разреза → названия оставшихся значений
        for fid, vals in (new_params.get("select") or {}).items():
            f = info.get("filters", {}).get(str(fid))
            if not f or old.get(str(fid)) == {str(v) for v in vals}:
                continue                            # разрез не меняли
            keep[_key(f["title"])] = {_key(f["values"].get(str(v), "")) for v in vals}
        if not keep:
            return []
        out = []
        for r in rows:
            try:
                dims = json.loads(r["dims"] or "[]")
            except ValueError:
                continue
            for name, value in dims:
                allowed = keep.get(_key(name))
                if allowed is not None and _key(value) not in allowed:
                    out.append(r)
                    break
        return out
    return []


def affected_models(cfg, store, codes) -> list[tuple[str, int]]:
    """[(модель, сколько из этих рядов она получает)]."""
    from ..excel import packet
    want = set(codes)
    out = []
    for m in cfg.models():
        if not m.get("active", True):
            continue
        try:
            got = set(packet.model_plan(cfg, store, m)[0]) & want
        except Exception:  # noqa: BLE001 — модель с повреждёнными настройками не должна мешать
            continue
        if got:
            out.append((m["name"], len(got)))
    return out


def mark_edited(store, job_id, now_iso):
    """После изменения набора данных: следующая загрузка — полная (у новых значений будет вся история),
    и пропавшие в ней ряды хаб перестанет ждать сам, без «отложенных данных»."""
    store.meta_set(f"full:{job_id}", "")
    store.meta_set(f"edited:{job_id}", now_iso)
