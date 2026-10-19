"""
«Таблица на странице» (web_table).

Режим «снимок» (по умолчанию): каждая строка таблицы — свой ряд (например, «Алюминий»,
«Медь»), значение берётся из числового столбца, дата — из строки таблицы, с самой страницы
(«Week 42 - October 14, 2026») или дата получения. Режим «история»: в строках таблицы даты,
каждый числовой столбец — ряд.

Параметры показателя:

    url: https://www.metaltorg.ru/
    table: {kind: html, title: "Цветные металлы: цены ON-LINE", index: 2}
    mode: snapshot                  # snapshot | history
    date: auto                      # auto | row | page | today
    rows: [Al, Cu]                  # необязательно: только эти строки
    code_prefix: METALTORG
    fetch_mode: auto

Таблица ищется сначала по подписи (title), затем по номеру — так перестановка блоков на
странице не ломает показатель. Страницы, которые строятся скриптом (Fearnleys), читаются
встроенным браузером; закрытые страницы — с входом пользователя в том же браузере.
"""
from ..core import errors as E
from . import _tables as T
from . import _web as W
from .base import FetchResult, Source


class WebTable(Source):
    kind = "web_table"
    title = "Таблица на сайте"
    snapshot = True

    def describe(self, params):
        t = params.get("table", {})
        return f"Таблица «{t.get('title') or t.get('index')}» на странице {params.get('url')}"

    def default_checks(self, params):
        return {"max_jump_pct": 50} if params.get("mode", "snapshot") == "snapshot" else {}

    def fetch(self, params, ctx) -> FetchResult:
        url = W.clean_url(params["url"])
        spec = params.get("table") or {}

        def need(text):
            try:
                T.pick(T.all_tables(W.parse_html(text)), spec)
                return True
            except E.StructureError:
                return False

        text, via, _final = W.fetch_page(url, ctx, params.get("fetch_mode", "auto"), need=need)
        doc = W.parse_html(text)
        table = T.pick(T.all_tables(doc), spec)
        pdate, is_week = W.page_date(doc)
        today = (ctx.now if ctx else __import__("datetime").datetime.now()).date()
        prefix = params.get("code_prefix") or W.site_name(url).upper()
        res = T.table_to_result(table, params, prefix, url, today, pdate, is_week)
        res.raw, res.raw_ext = text.encode("utf-8"), "html"
        if pdate and (today - pdate).days > 21 and params.get("date", "auto") in ("auto", "page"):
            res.warnings.append(f"Дата отчёта на странице давно не менялась: {pdate:%d.%m.%Y}")
        return res
