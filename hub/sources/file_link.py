"""
«Файл по ссылке» (file_link): xlsx / xls / csv / pdf.

Параметры показателя:

    url: https://example.ru/stat/prices.xlsx      # прямая ссылка
    # или: страница + шаблон ссылки — для «свежего отчёта», у которого меняется адрес
    page_url: https://example.ru/reports
    link_pattern: 'weekly.*\\.pdf'
    table: {sheet: "Цены", after_text: "Freight rates", page: 2, index: 0}
    header_row: 3                  # строка заголовков (если не угадалась сама)
    mode: history                  # history (даты в строках) | snapshot
    value_cols: [1, 2]             # необязательно: номера столбцов со значениями (с 0)
    periodicity: month             # для history: day | week | month | quarter | year
    # mode: dataset — «набор данных»: дата, разрезы и числа в строках, с отбором значений (см. _dataset.py)
"""
import datetime as dt

from ..core import errors as E
from . import _files as F
from . import _tables as T
from . import _web as W
from .base import FetchResult, Source


def _check(status, ctype, data):
    if status >= 400:
        cat = W.detect_block(status, data[:20000].decode("utf-8", "ignore"))
        raise E.HubError(cat or (E.NETWORK if status >= 500 else E.STRUCTURE), f"Сайт ответил ошибкой {status}")
    if "text/html" in (ctype or "") and data[:4] != b"%PDF":
        text = W.decode(data, ctype)
        if W.looks_like_login(text):
            raise E.LoginRequired("Файл доступен только после входа на сайт")
        if W.detect_block(200, text) == E.BOT:
            raise E.HubError(E.BOT, "Сайт показывает проверку «я не робот»")


def download(url, ctx):
    """Файл по ссылке. Сначала обычная загрузка; не вышло (нет связи по мнению Python, защита сайта, 403) —
    через встроенный браузер, как в Chrome."""
    try:
        status, ctype, data, final = W.http_get(url, ctx)
        _check(status, ctype, data)
        return data, final
    except E.LoginRequired:
        raise
    except Exception as first:  # noqa: BLE001
        br = getattr(ctx, "browser", None) if ctx is not None else None
        if br is None or not br.available() or E.classify(first) not in (E.NETWORK, E.BOT, E.RATE_LIMIT, E.STRUCTURE):
            raise
        if ctx is not None:
            ctx.info(f"Обычная загрузка не удалась ({first}) — скачиваю встроенным браузером")
        data, final, ctype = br.download(url)
        _check(200, ctype, data)
        return data, final


def parse(data, name, params, url, today, prefix):
    fmt = params.get("format") or F.detect_format(data, name)
    tables = F.read_tables(data, fmt, params)
    table = F.pick_file_table(tables, params.get("table") or {})
    if params.get("mode") == "dataset":                  # строки: дата + разрезы + числа (как ответ API)
        from . import _dataset as D
        return D.to_result(table, params, prefix, url)
    mode = params.get("mode") or ("history" if any(p["date"] >= 0.6 for p in T.column_profile(table.rows))
                                  else "snapshot")
    spec = dict(params, mode=mode)
    return T.table_to_result(table, spec, prefix, url, today)


class FileLink(Source):
    kind = "file_link"
    title = "Файл по ссылке"

    def describe(self, params):
        if params.get("page_url"):
            return f"Свежий файл со страницы {params['page_url']} (ссылка по шаблону «{params.get('link_pattern')}»)"
        return f"Файл {params.get('url')}"

    def open_url(self, params):
        return params.get("page_url") or params.get("url") or ""

    def fetch(self, params, ctx) -> FetchResult:
        if params.get("page_url"):
            page = W.clean_url(params["page_url"])
            text, _via, final = W.fetch_page(page, ctx, params.get("fetch_mode", "auto"))
            url = F.find_link(text, final, params["link_pattern"])
        else:
            url = W.clean_url(params["url"])
        data, final = download(url, ctx)
        today = (ctx.now if ctx else dt.datetime.now()).date()
        prefix = params.get("code_prefix") or W.site_name(url).upper()
        res = parse(data, url.rsplit("/", 1)[-1], params, url, today, prefix)
        res.raw, res.raw_ext = data, F.detect_format(data, url)
        res.url = url
        return res
