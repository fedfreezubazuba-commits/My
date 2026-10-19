"""
«Посылки» для моделей (раздел 9.2 ТЗ).

Для каждой подключённой модели хаб держит два файла с постоянными путями:

  Для моделей/<модель>.csv          Дата;Период;Код;Название;Единицы;Значение
  Для моделей/<модель> — ряды.csv    справочник: код, название, единицы, периодичность,
                                     последняя дата и значение, предыдущее значение,
                                     источник, дата обновления, правило даты

UTF-8 с BOM, разделитель «;», точка — десятичный разделитель, даты ISO.
Строки идут в порядке подписки (по ним Power Query строит порядок столбцов),
внутри ряда — по возрастанию даты. Файл перезаписывается только если содержимое
изменилось; запись атомарная (временный файл → замена). Если файл занят, поднимается
FileBusy: данные остаются в базе, запись повторится при следующем обновлении.
"""
import csv
import datetime as dt
import hashlib
import io
import os
import re
import tempfile
from decimal import Decimal
from pathlib import Path

from ..core import periods as P
from ..core.errors import FileBusy
from ..core.store import series_dims

PACKET_COLS = ["Дата", "Период", "Код", "Название", "Единицы", "Значение"]
SERIES_COLS = ["Код", "Название", "Единицы", "Периодичность", "Последняя дата", "Последнее значение",
               "Предыдущее значение", "Источник", "Обновлено", "Правило даты", "Порядок",
               # разрезы отдельными полями (добавлены в конец, чтобы не сдвигать прежние столбцы)
               "Показатель", "Разрез 1", "Разрез 2", "Разрез 3", "Разрез 4"]
N_CUTS = 4
SOURCE_TITLES = {"cbr": "Банк России", "fedstat": "ЕМИСС", "web_value": "Сайт (значение)",
                 "web_table": "Сайт (таблица)", "file_link": "Файл по ссылке", "folder_inbox": "Папка «Входящие»"}


def safe_name(name) -> str:
    """Имя модели → имя файла (без символов, запрещённых в Windows)."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(name)).strip(" .")
    return s or "модель"


def packet_path(paths, model_name) -> Path:
    return paths.packets / f"{safe_name(model_name)}.csv"


def series_path(packet: Path) -> Path:
    """Путь справочника рядов по пути посылки (так же вычисляет M-шаблон)."""
    return packet.with_name(packet.stem + " — ряды.csv")


def num(v) -> str:
    """Число без экспоненты, с точкой: 0.00001, 1250000, 15.8."""
    if v is None:
        return ""
    d = Decimal(repr(float(v)))
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _csv(rows, cols) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    w.writerow(cols)
    w.writerows(rows)
    return ("﻿" + buf.getvalue()).encode("utf-8")


def model_codes(cfg, store, model) -> list[str]:
    """Ряды подписки в постоянном порядке столбцов (новые — справа)."""
    jobs = {j["id"] for j in cfg.jobs()}
    wanted = []
    for job_id in model.get("jobs", []):
        if job_id in jobs:
            wanted += [r["code"] for r in store.series_of_job(job_id)]
    wanted += [c for c in model.get("series", []) if store.series(c)]
    excl = set(model.get("exclude", []))
    wanted = [c for c in dict.fromkeys(wanted) if c not in excl]
    return store.sync_subscriptions(model["name"], wanted)


def build(store, codes, jobs_by_id=None):
    """(байты посылки, байты справочника) для списка кодов в порядке столбцов."""
    jobs_by_id = jobs_by_id or {}
    rows, srows = [], []
    for pos, code in enumerate(codes, 1):
        s = store.series(code)
        if not s:
            continue
        obs = store.current_rows(code)
        for o in obs:
            rows.append([o["period_end"], o["period_label"] or "", code, s["name"] or "", s["unit"] or "", num(o["value"])])
        last = obs[-1] if obs else None
        prev = obs[-2] if len(obs) > 1 else None
        updated = max((o["fetched_at"] for o in obs), default="")[:10]
        src = SOURCE_TITLES.get(s["source"], s["source"] or "")
        srows.append([code, s["name"] or "", s["unit"] or "", P.PERIODICITY_RU.get(s["periodicity"], s["periodicity"] or ""),
                      last["period_end"] if last else "", num(last["value"]) if last else "",
                      num(prev["value"]) if prev else "", src, updated, s["date_rule"] or "", str(pos)]
                     + cut_values(series_dims(s)))
    return _csv(rows, PACKET_COLS), _csv(srows, SERIES_COLS)


def cut_values(dims) -> list:
    """[показатель, разрез 1…4] для справочника; лишние разрезы складываются в последний."""
    vals = [v for _k, v in dims]
    head, cuts = (vals[0] if vals else ""), vals[1:]
    if len(cuts) > N_CUTS:
        cuts = cuts[:N_CUTS - 1] + ["; ".join(cuts[N_CUTS - 1:])]
    return [head] + cuts + [""] * (N_CUTS - len(cuts))


def write_if_changed(path: Path, data: bytes) -> bool:
    """True, если файл переписан. Атомарно: временный файл в той же папке → замена."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.exists() and path.read_bytes() == data:
            return False
    except PermissionError:
        raise FileBusy(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp_", suffix=path.suffix)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        try:
            os.replace(tmp, path)
        except PermissionError:
            raise FileBusy(path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return True


def rebuild(cfg, store, paths, models=None, log=None) -> dict:
    """Пересобрать посылки всех активных моделей. {модель: "changed" | "same" | "busy: …"}."""
    out = {}
    for m in models if models is not None else cfg.models():
        if not m.get("active", True):
            continue
        codes = model_codes(cfg, store, m)
        packet, series = build(store, codes)
        path = packet_path(paths, m["name"])
        try:
            changed = write_if_changed(path, packet)
            changed_s = write_if_changed(series_path(path), series)
        except FileBusy as e:
            out[m["name"]] = f"busy: {e}"
            store.set_model_state(m["name"], last_error=str(e))
            if log:
                log.warning("Посылка %s занята: %s", m["name"], e)
            continue
        now = dt.datetime.now().isoformat(timespec="seconds")
        kw = {"last_build": now, "last_error": None,
              "packet_hash": hashlib.sha1(packet).hexdigest()}
        if changed:
            kw["packet_changed_at"] = now
        store.set_model_state(m["name"], **kw)
        out[m["name"]] = "changed" if (changed or changed_s) else "same"
    return out


def wide(store, codes):
    """Широкая таблица так же, как её строит Power Query (для проверок и «Открыть в Excel»):
    [(дата, период, {код: значение})] по возрастанию даты; период — самый подробный на дату."""
    table = {}
    for code in codes:
        for o in store.current_rows(code):
            d = table.setdefault(o["period_end"], {"labels": [], "values": {}})
            d["labels"].append(o["period_label"] or o["period_end"])
            d["values"][code] = o["value"]
    out = []
    for date in sorted(table):
        labels = sorted(table[date]["labels"], key=P.rank)
        out.append((dt.date.fromisoformat(date), labels[0], table[date]["values"]))
    return out
