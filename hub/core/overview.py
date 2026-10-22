"""Данные для экрана «Обзор»: светофор, проблемы с кнопками действий, новые данные, модели."""
import datetime as dt
import json

from . import errors as E
from . import periods as P
from .base_fmt import fmt
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


GROUPS = [("fedstat", "Росстат"), ("cbr", "ЦБ РФ"), ("sites", "Сайты"), ("other", "Прочее")]
SITE_SOURCES = {"web_value", "web_table", "file_link"}


def source_group(source) -> str:
    """Кнопки-фильтры «Обзора»: Росстат (ЕМИСС), ЦБ РФ, Сайты (страницы и файлы по ссылке), Прочее."""
    if source in ("fedstat", "cbr"):
        return source
    return "sites" if source in SITE_SOURCES else "other"


def _upto(details) -> str | None:
    """По какой период пришли данные в запуске: запись «upto» (с 0.6.0) или самый поздний из сохранённых."""
    try:
        d = json.loads(details) if details else {}
    except (TypeError, ValueError):
        return None
    if d.get("upto"):
        return d["upto"]
    labels = [x[1] for x in (d.get("new") or d.get("revisions") or []) if isinstance(x, list) and len(x) > 1]
    dated = [(P.label_end(lab), lab) for lab in labels if P.label_end(lab)]
    return max(dated)[1] if dated else (max(labels) if labels else None)


def _later(a, b):
    if not a or not b:
        return a or b
    return a if (P.label_end(a) or dt.date.min) >= (P.label_end(b) or dt.date.min) else b


def update_events(cfg, store, since, until=None, groups=None) -> list[dict]:
    """Удачные запуски с новыми данными: [{job, when, new, revisions, upto}] по времени.
    groups — какие кнопки-фильтры нажаты (пусто — все источники)."""
    jobs = {j["id"]: j for j in cfg.jobs()}
    rows = store.q("SELECT job_id, finished, new_periods n, revisions r, details FROM runs "
                   "WHERE finished>=? AND finished<? AND status='updated' AND (new_periods>0 OR revisions>0) "
                   "ORDER BY finished", (since, until or "9999"))
    out = []
    for r in rows:
        job = jobs.get(r["job_id"])
        if not job or groups and source_group(job.get("source")) not in groups:
            continue
        out.append({"job": job, "when": r["finished"], "new": r["n"] or 0, "revisions": r["r"] or 0,
                    "upto": _upto(r["details"])})
    return out


def _merge(events) -> list[dict]:
    """По одному показателю — одна строка: сколько всего записей и по какой самый поздний период."""
    by = {}
    for e in events:
        m = by.setdefault(e["job"]["id"], {**e, "new": 0, "revisions": 0, "upto": None})
        m["new"] += e["new"]
        m["revisions"] += e["revisions"]
        m["upto"] = _later(m["upto"], e["upto"])
        m["when"] = max(m["when"], e["when"])
    return sorted(by.values(), key=lambda x: x["when"], reverse=True)


def recent_updates(cfg, store, days=7, groups=None, limit=40):
    since = (dt.datetime.now() - dt.timedelta(days=days)).isoformat(timespec="seconds")
    return _merge(update_events(cfg, store, since, groups=groups))[:limit]


def update_text(u) -> str:
    """«по август 2026, +16 000 записей (21.10.26 в 14:05)»."""
    parts = [P.upto_text(u["upto"])] if u.get("upto") else []
    if u["new"]:
        parts.append(f"+{fmt(u['new'])} {plural(u['new'], 'запись', 'записи', 'записей')}")
    if u["revisions"]:
        parts.append(f"пересмотрено {fmt(u['revisions'])}")
    when = u["when"] or ""
    stamp = f"{when[8:10]}.{when[5:7]}.{when[2:4]} в {when[11:16]}" if len(when) >= 16 else when
    return ", ".join(parts) + (f" ({stamp})" if stamp else "")


def month_updates(cfg, store, year, month, groups=None) -> dict:
    """Для календаря: {дата: [строки по показателям]} за месяц."""
    first = dt.date(year, month, 1)
    nxt = dt.date(year + (month == 12), month % 12 + 1, 1)
    by_day = {}
    for e in update_events(cfg, store, first.isoformat(), nxt.isoformat(), groups):
        by_day.setdefault(dt.date.fromisoformat(e["when"][:10]), []).append(e)
    return {d: _merge(evs) for d, evs in by_day.items()}


def plural(n, one, few, many) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    return few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


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
