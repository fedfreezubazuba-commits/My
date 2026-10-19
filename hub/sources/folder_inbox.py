"""
«Папка „Входящие“» (folder_inbox): файлы, выгруженные вручную (например, из платного терминала).

Пользователь кладёт файл в `Входящие/<папка показателя>/`. При обновлении хаб читает все новые
файлы по порядку, объединяет данные с историей (как обычно: новые периоды и пересмотры) и
переносит файлы в `Входящие/<папка>/обработано/`. Если новых файлов нет — «новых данных нет».

Параметры: те же, что у «Файла по ссылке» (table, header_row, mode, value_cols, periodicity),
плюс folder — имя папки внутри «Входящие».
"""
import datetime as dt
import shutil

from ..core.errors import StructureError
from . import _files as F
from .base import FetchResult, Source
from .file_link import parse

DONE = "обработано"


class FolderInbox(Source):
    kind = "folder_inbox"
    title = "Папка «Входящие»"

    def host(self, params):
        return "local"

    def describe(self, params):
        return f"Файлы из папки «Входящие/{params.get('folder')}»"

    def folder(self, params, ctx):
        if ctx is None or ctx.paths is None:
            raise StructureError("Не задана рабочая папка")
        d = ctx.paths.inbox / params["folder"]
        d.mkdir(parents=True, exist_ok=True)
        return d

    def open_url(self, params):
        return ""

    def pending(self, params, ctx):
        d = self.folder(params, ctx)
        return sorted((p for p in d.iterdir() if p.is_file() and not p.name.startswith(("~$", "."))),
                      key=lambda p: p.stat().st_mtime)

    def fetch(self, params, ctx) -> FetchResult:
        files = self.pending(params, ctx)
        res = FetchResult(url="")
        if not files:
            res.unchanged = True
            return res
        today = (ctx.now if ctx else dt.datetime.now()).date()
        prefix = params.get("code_prefix") or "INBOX_" + params["folder"].upper()[:12]
        merged = {}
        for f in files:
            data = f.read_bytes()
            part = parse(data, f.name, params, f.name, today, prefix)
            for s in part.series:
                if s.key not in {x.key for x in res.series}:
                    res.series.append(s)
            for o in part.obs:
                merged[(o.key, o.period_end)] = o          # более свежий файл важнее
            res.raw, res.raw_ext = data, F.detect_format(data, f.name)
        res.obs = list(merged.values())
        res.warnings.append("Обработаны файлы: " + ", ".join(f.name for f in files))
        if not ctx.dry_run:
            done = self.folder(params, ctx) / DONE
            done.mkdir(exist_ok=True)
            stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
            for f in files:
                shutil.move(str(f), str(done / f"{stamp} {f.name}"))
        return res
