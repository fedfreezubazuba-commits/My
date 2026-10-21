"""Данные для экрана «Обзор»: светофор, проблемы с кнопками действий, новые данные, модели."""
import datetime as dt

from . import errors as E
from . import status as ST


class Problem:
    def __init__(self, job, state, action, action_label, text, qid=None):
        self.job = job
        self.state = state
        self.action = action            # retry | claude | login | browser | review | None
        self.action_label = action_label
        self.text = text
        self.qid = qid


def short(text, limit=300) -> str:
    """Длинный текст (например, замечания по тысячам рядов) не должен вешать окно."""
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _host_paused(store, job) -> bool:
    from .. import sources
    from . import hostblock
    try:
        return bool(hostblock.until(store, sources.get(job["source"]).host(job.get("params") or {})))
    except KeyError:
        return False


def problems(cfg, store) -> list[Problem]:
    out = []
    states = store.states()
    for job in cfg.jobs():
        st = states.get(job["id"], {})
        s = ST.effective(job, st)
        if s not in ST.PROBLEM:
            continue
        if s == "blocked" and _host_paused(store, job):
            continue                 # про сайт на паузе — отдельный блок на «Обзоре»
        if s == "quarantine":
            pend = store.pending_quarantine(job["id"])
            if pend:       # одна строка на показатель, даже если отложенных загрузок несколько
                text = short(st.get("message") or "Данные отложены")
                if len(pend) > 1:
                    text += f" (отложено загрузок: {len(pend)})"
                out.append(Problem(job, st, "review", "Посмотреть", text, pend[-1]["id"]))
            continue
        if s == "delayed":
            out.append(Problem(job, st, None, "", "Источник опаздывает с публикацией. Хаб проверяет каждый день."))
            continue
        cat = st.get("category") or E.INTERNAL
        what, todo, action = E.CATEGORIES.get(cat, E.CATEGORIES[E.INTERNAL])
        label = {"retry": "Повторить", "claude": "Отправить Claude", "login": "Войти на сайт",
                 "browser": "Открыть сайт", "review": "Посмотреть",
                 "reselect": "Выбрать заново"}.get(action, "")
        if action == "reselect" and job.get("source") != "fedstat":
            label, action = "", None
        if s == "retry":
            label, action = "Повторить сейчас", "retry"
        out.append(Problem(job, st, action, label, short(f"{st.get('message') or what}. {todo}.")))
    return out


def recent_updates(cfg, store, days=7, limit=30):
    since = (dt.datetime.now() - dt.timedelta(days=days)).isoformat(timespec="seconds")
    jobs = {j["id"]: j for j in cfg.jobs()}
    rows = store.q("SELECT job_id, MAX(finished) f, SUM(new_periods) n, SUM(revisions) r FROM runs "
                   "WHERE finished>? AND (new_periods>0 OR revisions>0) AND status='updated' "
                   "GROUP BY job_id ORDER BY f DESC LIMIT ?", (since, limit))
    out = []
    for r in rows:
        if r["job_id"] in jobs:
            st = store.state(r["job_id"])
            out.append({"job": jobs[r["job_id"]], "when": r["f"], "new": r["n"], "revisions": r["r"],
                        "last_label": st.get("last_label"), "last_value": st.get("last_value"),
                        "unit": st.get("last_unit")})
    return out


def runner_stale(store, days=2) -> str | None:
    last = store.meta_get("runner_heartbeat")
    if not last:
        return "Фоновое обновление ещё ни разу не запускалось"
    try:
        age = dt.datetime.now() - dt.datetime.fromisoformat(last)
    except ValueError:
        return None
    if age > dt.timedelta(days=days):
        return f"Фоновое обновление не запускалось {age.days} дн. — проверьте, включено ли автообновление"
    return None
