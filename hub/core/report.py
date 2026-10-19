"""
Отчёт о запуске: reports/ГГГГ-ММ-ДД_ЧЧММ.html (раздел 12 ТЗ).

  1. Что обновилось (новые периоды, пересмотры: было → стало).
  2. В каких моделях новые данные.
  3. Ошибки и задержки с причинами и рекомендациями.
  4. Общее здоровье системы.
"""
import datetime as dt
import html

from . import errors as E
from . import status as ST
from .base_fmt import fmt, fmt_date

CSS = """
body{font-family:'Segoe UI',Arial,sans-serif;max-width:980px;margin:24px auto;padding:0 16px;color:#1d2733;background:#fff}
h1{font-size:24px;margin-bottom:4px} h2{font-size:18px;margin-top:28px;border-bottom:1px solid #e3e7ec;padding-bottom:4px}
.muted{color:#6b7785} .ok{color:#2e7d32} .warn{color:#c77700} .bad{color:#c62828}
table{border-collapse:collapse;width:100%;font-size:14px} td,th{padding:6px 8px;border-bottom:1px solid #eef1f4;text-align:left;vertical-align:top}
th{background:#f6f8fa;font-weight:600} .big{font-size:20px;font-weight:600;padding:12px 16px;border-radius:8px;margin:12px 0}
.big.ok{background:#e8f5e9} .big.warn{background:#fff4e0} .big.bad{background:#fdecea}
"""


def esc(x):
    return html.escape(str(x if x is not None else ""))


def names(store):
    return {r["code"]: r["name"] for r in store.all_series()}


def render(runner, report) -> str:
    store = runner.store
    nm = names(store)
    outs = report.outcomes
    updated = [o for o in outs if o.changed and o.status != "quarantine"]
    problems = [o for o in outs if o.status in ("error", "retry", "login", "quarantine", "delayed")]
    head = "bad" if any(o.status in ("error", "login") for o in problems) else "warn" if problems else "ok"
    title = {"ok": "Всё в порядке", "warn": f"Требует внимания: {len(problems)}",
             "bad": f"Требует внимания: {len(problems)}"}[head]
    p = [f"<!doctype html><html lang='ru'><head><meta charset='utf-8'><title>Отчёт {report.started:%d.%m.%Y %H:%M}</title>"
         f"<style>{CSS}</style></head><body>",
         f"<h1>Отчёт об обновлении</h1><div class='muted'>{report.started:%d.%m.%Y %H:%M} — "
         f"{(report.finished or dt.datetime.now()):%H:%M}, показателей проверено: {len(outs)}</div>",
         f"<div class='big {head}'>{esc(title)}</div>"]

    p.append("<h2>1. Что обновилось</h2>")
    if not updated:
        p.append("<p class='muted'>Новых данных нет.</p>")
    for o in updated:
        p.append(f"<h3>{esc(o.job['name'])}</h3><p class='muted'>{esc(o.summary)}</p><table>"
                 "<tr><th>Ряд</th><th>Период</th><th>Было</th><th>Стало</th></tr>")
        for code, label, value in o.new_periods[-30:]:
            p.append(f"<tr><td>{esc(nm.get(code, code))}<br><span class='muted'>{esc(code)}</span></td>"
                     f"<td>{esc(label)}</td><td class='muted'>новый период</td><td>{esc(fmt(value))}</td></tr>")
        if len(o.new_periods) > 30:
            p.append(f"<tr><td colspan='4' class='muted'>… и ещё {len(o.new_periods) - 30} новых значений"
                     f" (первая загрузка или длинная история)</td></tr>")
        for code, label, old, new in o.revisions[-30:]:
            p.append(f"<tr><td>{esc(nm.get(code, code))}<br><span class='muted'>{esc(code)}</span></td>"
                     f"<td>{esc(label)}</td><td>{esc(fmt(old))}</td><td>{esc(fmt(new))} "
                     f"<span class='warn'>пересмотр</span></td></tr>")
        p.append("</table>")

    p.append("<h2>2. В каких моделях новые данные</h2>")
    if report.models_new:
        p.append("<ul>" + "".join(f"<li>Откройте модель <b>{esc(n)}</b> — там новые данные по {k} "
                                  f"{'ряду' if k == 1 else 'рядам'}.</li>" for n, k in report.models_new) + "</ul>")
    else:
        p.append("<p class='muted'>Нет моделей с непросмотренными новыми данными.</p>")
    busy = {k: v for k, v in report.packets.items() if str(v).startswith("busy")}
    if busy:
        p.append("<p class='warn'>Не удалось записать файл для моделей: " + ", ".join(map(esc, busy)) +
                 ". Вероятно, он открыт в Excel. Данные сохранены в базе, запись повторится автоматически.</p>")

    p.append("<h2>3. Ошибки и задержки</h2>")
    if not problems:
        p.append("<p class='ok'>Ошибок нет.</p>")
    else:
        p.append("<table><tr><th>Показатель</th><th>Статус</th><th>Что случилось</th><th>Что делать</th></tr>")
        for o in problems:
            what, todo, _ = E.CATEGORIES.get(o.category or E.INTERNAL, E.CATEGORIES[E.INTERNAL])
            if o.status == "delayed":
                todo = "Это не ошибка: источник опаздывает с публикацией. Хаб продолжает проверять каждый день."
            cls = "bad" if o.status in ("error", "login") else "warn"
            p.append(f"<tr><td>{esc(o.job['name'])}</td><td class='{cls}'>{esc(ST.word(o.status))}</td>"
                     f"<td>{esc(o.message or what)}</td><td>{esc(todo)}</td></tr>")
        p.append("</table>")

    p.append("<h2>4. Здоровье системы</h2><ul>")
    jobs = runner.cfg.jobs()
    states = store.states()
    counts = {}
    for j in jobs:
        s = ST.effective(j, states.get(j["id"]))
        counts[s] = counts.get(s, 0) + 1
    p.append(f"<li>Показателей в обновлении: {len(jobs)} — " +
             ", ".join(f"{ST.word(k).lower()}: {v}" for k, v in sorted(counts.items())) + "</li>")
    p.append(f"<li>Подключённых моделей: {len([m for m in runner.cfg.models() if m.get('active', True)])}</li>")
    q = store.pending_quarantine()
    if q:
        p.append(f"<li class='warn'>В карантине ждут решения: {len(q)}</li>")
    for n in report.notes:
        p.append(f"<li>{esc(n)}</li>")
    if report.purged:
        p.append(f"<li class='muted'>Окончательно удалены из корзины: {esc(', '.join(report.purged))}</li>")
    p.append("</ul><p class='muted'>Хаб данных. Даты — конец отчётного периода.</p></body></html>")
    return "\n".join(p)


def write(runner, report):
    runner.paths.reports.mkdir(parents=True, exist_ok=True)
    path = runner.paths.reports / f"{report.started:%Y-%m-%d_%H%M}.html"
    n = 2
    while path.exists():
        path = runner.paths.reports / f"{report.started:%Y-%m-%d_%H%M}_{n}.html"
        n += 1
    path.write_text(render(runner, report), encoding="utf-8")
    return path


def list_reports(paths, limit=100):
    if not paths.reports.exists():
        return []
    files = sorted(paths.reports.glob("*.html"), reverse=True)
    return files[:limit]


def short_title(path) -> str:
    stem = path.stem
    return fmt_date(f"{stem[:10]}T{stem[11:13]}:{stem[13:15]}") if len(stem) >= 15 else stem
