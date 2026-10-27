"""
«Листы файла в модель» (file_sheets): листы Excel/CSV по ссылке переносятся в модель как есть, на постоянные
имена листов (см. core/sheets.py). В базу значения не разбираются; хаб следит, изменился ли файл, и тогда
обновляет листы: заменяет (replace) или дописывает обновление под предыдущими (append).

Параметры показателя:

    url: https://www.cbr.ru/vfs/statistics/credit_statistics/bop/bop_est_6.xlsx
    # или: страница + шаблон ссылки (адрес файла каждый раз новый)
    page_url: …
    link_pattern: '…'
    mode: replace | append
    sheets: [{title: "Оценка ПБ (15.10.2026)", index: 0, count: 1, name: "ПБ_оценка", cells: true}]
"""
from ..core import errors as E
from ..core import sheets as SH
from . import _files as F
from . import _web as W
from .base import FetchResult, Source
from .file_link import download


class FileSheets(Source):
    kind = "file_sheets"
    title = "Листы файла в модель"
    snapshot = True

    def describe(self, params):
        names = ", ".join(f"«{s['name']}»" for s in params.get("sheets") or [])
        where = (params.get("path") or {}).get("start") or params.get("page_url") or params.get("url") or ""
        mode = "копить обновления" if params.get("mode") == "append" else "заменять"
        return f"Листы {names} из файла {where} ({mode})"

    def open_url(self, params):
        return (params.get("path") or {}).get("start") or params.get("page_url") or params.get("url") or ""

    def fetch(self, params, ctx) -> FetchResult:
        if params.get("path"):                  # путь к файлу: шаги по ссылкам от стартовой страницы
            from . import _path
            url = _path.follow(params["path"], ctx)
        elif params.get("page_url"):
            text, _via, final = W.fetch_page(W.clean_url(params["page_url"]), ctx, params.get("fetch_mode", "auto"))
            url = F.find_link(text, final, params["link_pattern"])
        else:
            url = W.clean_url(params["url"])
        data, _final = download(url, ctx)
        fmt = F.detect_format(data, url)
        sheets = SH.read_sheets(data, "xlsx" if fmt == "xlsx" else fmt)
        got = []
        for spec in params.get("sheets") or []:
            sh = SH.match_sheet(sheets, spec)
            if sh is None:
                raise E.StructureError(f"В файле нет листа «{spec.get('title')}» (листы сейчас: "
                                       + ", ".join(f"«{s.title}»" for s in sheets[:8]) + ")")
            got.append((spec, sh))
        if not got:
            raise E.StructureError("Не выбрано ни одного листа")
        res = FetchResult(url=url)
        res.sheets = got
        res.raw, res.raw_ext = data, fmt
        return res
