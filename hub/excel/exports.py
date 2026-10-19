"""
Дополнительные выгрузки (раздел 9.5 ТЗ) — не основной путь доставки в модели.

  «Открыть в Excel»  — разовый файл в Выгрузки/ с листами «Данные» (даты × ряды),
                       «Последние», «Длинная», «Паспорт»; файл сразу открывается.
  Файлы по показателям (настройка, по умолчанию выключена) — постоянный
                       Выгрузки/Показатели/<показатель>.xlsx, обновляется после загрузки.
"""
import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

from ..core import periods as P
from ..core.errors import FileBusy
from . import packet


def _book(store, codes, passport):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    bold = Font(bold=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Данные"
    meta = {c: store.series(c) for c in codes}
    ws.append(["Дата", "Период"] + [meta[c]["name"] for c in codes])
    ws.append(["", "код →"] + codes)
    for d, label, vals in packet.wide(store, codes):
        ws.append([d, label] + [vals.get(c) for c in codes])
    for c in ws[1]:
        c.font = bold
        c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.row_dimensions[1].height = 60
    ws.column_dimensions["A"].width = 12
    for i in range(3, len(codes) + 3):
        ws.column_dimensions[get_column_letter(i)].width = 20
    for row in ws.iter_rows(min_row=3, max_col=1):
        row[0].number_format = "DD.MM.YYYY"
    ws.freeze_panes = "C3"

    ws = wb.create_sheet("Последние")
    ws.append(["Код", "Название", "Единицы", "Периодичность", "Последний период", "Последнее значение",
               "Предыдущее значение"])
    for c in codes:
        last = store.last_values(c, 2)
        ws.append([c, meta[c]["name"], meta[c]["unit"], P.PERIODICITY_RU.get(meta[c]["periodicity"], ""),
                   last[0]["period_label"] if last else "", last[0]["value"] if last else None,
                   last[1]["value"] if len(last) > 1 else None])
    for c in ws[1]:
        c.font = bold
    ws.column_dimensions["B"].width = 60

    ws = wb.create_sheet("Длинная")
    ws.append(packet.PACKET_COLS)
    for c in codes:
        for o in store.current_rows(c):
            ws.append([dt.date.fromisoformat(o["period_end"]), o["period_label"], c, meta[c]["name"],
                       meta[c]["unit"], o["value"]])
    for c in ws[1]:
        c.font = bold
    for row in ws.iter_rows(min_row=2, max_col=1):
        row[0].number_format = "DD.MM.YYYY"
    ws.auto_filter.ref = ws.dimensions

    ws = wb.create_sheet("Паспорт")
    for k, v in passport:
        ws.append([k, v])
        ws.cell(ws.max_row, 1).font = bold
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 100
    return wb


def _passport(cfg, store, job, codes):
    from ..sources import get
    src = None
    try:
        src = get(job["source"])
    except KeyError:
        pass
    st = store.state(job["id"])
    rows = [("Показатель", job["name"]), ("Источник", src.title if src else job["source"]),
            ("Ссылка", src.open_url(job.get("params", {})) if src else ""),
            ("Параметры", str(job.get("params", {}))), ("Последняя загрузка", st.get("last_success") or ""),
            ("Выгружено", dt.datetime.now().strftime("%Y-%m-%d %H:%M")),
            ("Дата", "Конец отчётного периода (для снимков — дата наблюдения)")]
    for c in codes:
        s = store.series(c)
        rows.append((c, f"{s['name']} [{s['unit']}]; правило даты: {s['date_rule'] or '—'}"))
    return rows


def _safe_save(wb, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        wb.save(path)
    except PermissionError:
        raise FileBusy(path)
    return path


def export_job(cfg, store, paths, job_id, codes=None) -> Path:
    job = cfg.job(job_id) or {"id": job_id, "name": job_id, "source": "", "params": {}}
    codes = codes or [r["code"] for r in store.series_of_job(job_id)]
    wb = _book(store, codes, _passport(cfg, store, job, codes))
    name = packet.safe_name(job["name"])[:60]
    return _safe_save(wb, paths.exports / f"{name} {dt.datetime.now():%Y-%m-%d_%H%M%S}.xlsx")


def export_codes(cfg, store, paths, codes, title) -> Path:
    job_id = store.series(codes[0])["job_id"]
    job = cfg.job(job_id) or {"id": job_id, "name": title, "source": "", "params": {}}
    wb = _book(store, codes, _passport(cfg, store, job, codes))
    return _safe_save(wb, paths.exports / f"{packet.safe_name(title)[:60]} {dt.datetime.now():%Y-%m-%d_%H%M%S}.xlsx")


def update_job_files(cfg, store, paths, jobs) -> dict:
    out = {}
    for job in jobs:
        codes = [r["code"] for r in store.series_of_job(job["id"])]
        if not codes:
            continue
        wb = _book(store, codes, _passport(cfg, store, job, codes))
        try:
            out[job["id"]] = str(_safe_save(wb, paths.job_files / f"{packet.safe_name(job['name'])}.xlsx"))
        except FileBusy as e:
            out[job["id"]] = f"busy: {e}"
    return out


def open_file(path) -> None:
    path = str(path)
    if sys.platform.startswith("win"):
        os.startfile(path)  # noqa: S606 — открыть файл программой по умолчанию
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
