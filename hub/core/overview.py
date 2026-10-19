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


def problems(cfg, store) -> list[Problem]:
    out = []
    states = store.states()
    for job in cfg.jobs():
        st = states.get(job["id"], {})
        s = ST.effective(job, st)
        if s not in ST.PROBLEM:
            continue
        if s == "quarantine":
            for q in store.pending_quarantine(job["id"]):
                out.append(Problem(job, st, "review", "Посмотреть", st.get("message") or "Данные отложены", q["id"]))
            continue
        if s == "delayed":
            out.append(Problem(job, st, None, "", "Источник опаздывает с публикацией. Хаб проверяет каждый день."))
            continue
        cat = st.get("category") or E.INTERNAL
        what, todo, action = E.CATEGORIES.get(cat, E.CATEGORIES[E.INTERNAL])
        label = {"retry": "Повторить", "claude": "Отправить Claude", "login": "Войти на сайт",
                 "browser": "Открыть в браузере", "review": "Посмотреть"}.get(action, "")
        if s == "retry":
            label, action = "Повторить сейчас", "retry"
        out.append(Problem(job, st, action, label, f"{st.get('message') or what}. {todo}."))
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
