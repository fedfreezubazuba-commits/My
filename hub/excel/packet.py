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
                 "web_table": "Сайт (таблица)", "file_link": "Файл по ссылке", "folder_inbox": "Папка «Входящие»",
                 "web_form": "Форма на сайте", "comtrade": "ООН (UN Comtrade)",
                 "fred": "FRED (ФРС Сент-Луиса)"}


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


MISSING = "(не указано)"      # поле есть у других рядов показателя, а у этого нет


def series_fields(row) -> dict:
    """Поля ряда для выборок: разрезы + «Периодичность»."""
    d = dict(series_dims(row))
    d["Периодичность"] = P.PERIODICITY_RU.get(row["periodicity"], row["periodicity"] or "")
    return d


def field_value(fields: dict, name):
    """Значение поля; у рядов ЕМИСС, загруженных до версии 0.5.4, «Вид значения» был только у
    «нарастающим итогом» — остальные считаем обычными значениями за период."""
    if name in fields:
        return fields[name]
    from ..sources.fedstat import PLAIN_KIND, VALUE_KIND
    return PLAIN_KIND if name == VALUE_KIND else MISSING


def matches(fields: dict, filters: dict) -> bool:
    return all(field_value(fields, k) in set(v) for k, v in (filters or {}).items())


def selection_codes(store, sel) -> list[str]:
    """Ряды показателя, подходящие под выборку (в порядке появления)."""
    return [r["code"] for r in store.series_of_job(sel.get("job"))
            if matches(series_fields(r), sel.get("filters") or {})]


def _merge_window(old, new):
    """Объединение периодов: None — без ограничений."""
    if old is None or new is None:
        return None
    a = min(x for x in (old[0], new[0])) if old[0] and new[0] else ""
    b = max(x for x in (old[1], new[1])) if old[1] and new[1] else ""
    return (a, b)


def model_plan(cfg, store, model):
    """(ряды подписки в постоянном порядке столбцов, {код: (с, по) или None})."""
    jobs = {j["id"] for j in cfg.jobs()}
    wanted, windows = [], {}

    def add(codes, window):
        for c in codes:
            windows[c] = _merge_window(windows[c], window) if c in windows else window
            wanted.append(c)

    for job_id in model.get("jobs", []):
        if job_id in jobs:
            add([r["code"] for r in store.series_of_job(job_id)], None)
    for sel in model.get("selections", []):
        if sel.get("job") in jobs:
            win = (sel.get("from") or "", sel.get("to") or "")
            add(selection_codes(store, sel), None if win == ("", "") else win)
    add([c for c in model.get("series", []) if store.series(c)], None)
    excl = set(model.get("exclude", []))
    if excl and not model.get("fix_054"):
        # в 0.5.3 «Убрать» у «Отдельных рядов» заносил ряды в исключения, и они пропадали из выборок
        chosen = {c for sel in model.get("selections", []) for c in selection_codes(store, sel)}
        cfg.unexclude(model["name"], chosen)
        cfg.update_model(model["name"], fix_054=True)
        excl -= chosen
    wanted = [c for c in dict.fromkeys(wanted) if c not in excl]
    return store.sync_subscriptions(model["name"], wanted), windows


def critical_codes(cfg, store, model) -> set | None:
    """Ряды, отмеченные в модели как критичные (показатель целиком, выборка или отдельный ряд); None — ничего не
    отмечено (тогда важны все данные модели)."""
    keys = model.get("critical") or []
    if not keys:
        return None
    jobs = {j["id"] for j in cfg.jobs()}
    out = set()
    for k in keys:
        kind, _sep, ident = str(k).partition(":")
        if kind == "job" and ident in jobs:
            out.update(r["code"] for r in store.series_of_job(ident))
        elif kind == "sel":
            sel = next((x for x in model.get("selections") or [] if x.get("id") == ident), None)
            if sel and sel.get("job") in jobs:
                out.update(selection_codes(store, sel))
        elif kind == "code":
            out.add(ident)
    return out - set(model.get("exclude") or [])


def model_codes(cfg, store, model) -> list[str]:
    """Ряды подписки в постоянном порядке столбцов (новые — справа)."""
    return model_plan(cfg, store, model)[0]


def in_window(period_end: str, window) -> bool:
    if not window:
        return True
    a, b = window
    return (not a or period_end >= a) and (not b or period_end <= b)


FLAT_HEAD = ["Дата", "Период", "Показатель"]
FLAT_TAIL = ["Периодичность", "Единицы", "Источник", "Код", "Обновлено", "Значение"]
EXCEL_ROWS = 1_048_575       # больше строк лист Excel не вмещает


def table_path(packet: Path) -> Path:
    """Плоская таблица (строка на значение со всеми полями) — рядом с посылкой."""
    return packet.with_name(packet.stem + " — таблица.csv")


def flat_columns(rows_dims) -> list:
    """Столбцы полей плоской таблицы: все поля рядов модели по порядку появления (без «Показатель»).
    Поле с именем служебного столбца получает приписку «(поле)»."""
    names = []
    for dims in rows_dims:
        for k, _v in dims[1:] if dims and dims[0][0] == "Показатель" else dims:
            k = k if k not in FLAT_HEAD + FLAT_TAIL else f"{k} (поле)"
            if k not in names:
                names.append(k)
    return names


def build(store, codes, windows=None, flat=False):
    """(байты посылки, байты справочника[, байты плоской таблицы]) для списка кодов в порядке столбцов.

    windows — {код: (с, по)}: модель получает только этот период ряда (выжимка)."""
    windows = windows or {}
    rows, srows, picked = [], [], []
    for pos, code in enumerate(codes, 1):
        s = store.series(code)
        if not s:
            continue
        obs = [o for o in store.current_rows(code) if in_window(o["period_end"], windows.get(code))]
        picked.append((s, obs))
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
    if not flat:
        return _csv(rows, PACKET_COLS), _csv(srows, SERIES_COLS)
    return _csv(rows, PACKET_COLS), _csv(srows, SERIES_COLS), flat_table(picked)


def flat_table(picked) -> bytes:
    """Строка на значение: Дата, Период, Показатель, поля ряда, Периодичность, Единицы, Источник, Код,
    Обновлено, Значение."""
    dims_of = [series_dims(s) for s, _obs in picked]
    cols = flat_columns(dims_of)
    out = []
    for (s, obs), dims in zip(picked, dims_of):
        head = dims[0][1] if dims and dims[0][0] == "Показатель" else (s["name"] or "")
        vals = {(k if k not in FLAT_HEAD + FLAT_TAIL else f"{k} (поле)"): v
                for k, v in (dims[1:] if dims and dims[0][0] == "Показатель" else dims)}
        fields = [vals.get(c, "") for c in cols]
        per = P.PERIODICITY_RU.get(s["periodicity"], s["periodicity"] or "")
        src = SOURCE_TITLES.get(s["source"], s["source"] or "")
        for o in obs:
            out.append([o["period_end"], o["period_label"] or "", head] + fields
                       + [per, s["unit"] or "", src, s["code"], (o["fetched_at"] or "")[:10], num(o["value"])])
    return _csv(out, FLAT_HEAD + cols + FLAT_TAIL)


def cut_values(dims) -> list:
    """[показатель, разрез 1…4] для справочника; лишние разрезы складываются в последний."""
    vals = [v for _k, v in dims]
    head, cuts = (vals[0] if vals else ""), vals[1:]
    if len(cuts) > N_CUTS:
        cuts = cuts[:N_CUTS - 1] + ["; ".join(cuts[N_CUTS - 1:])]
    return [head] + cuts + [""] * (N_CUTS - len(cuts))


UPDATES_COLS = ["Обновлено", "Дата обновления", "Что", "Новых значений", "Изменено значений", "Период с", "Период по",
                "Код", "Название", "Показатель", "Периодичность", "Источник"]
MAX_UPDATE_ROWS = 200_000


def updates_path(packet: Path) -> Path:
    """История обновлений рядов модели — рядом с посылкой (так же вычисляет M-шаблон)."""
    return packet.with_name(packet.stem + " — обновления.csv")


def updates_table(store, codes) -> bytes:
    """Когда какие ряды модели обновлялись: строка на «загрузка × ряд» — сколько новых значений, сколько
    изменённых (источник уточнил прошлые периоды), за какие периоды. Свежие сверху.
    Удобно сравнивать с датой последнего пересчёта модели: всё, что «Обновлено» позже, — новое."""
    rows = []
    codes = list(dict.fromkeys(codes))
    info = {}
    for i in range(0, len(codes), 500):
        part = codes[i:i + 500]
        marks = ",".join("?" * len(part))
        for s in store.q(f"SELECT code, name, periodicity, source, dims FROM series WHERE code IN ({marks})", part):
            info[s["code"]] = s
        rows += store.q(
            "SELECT code, run_id, MAX(fetched_at) AS f, COUNT(*) AS n, SUM(was_new) AS nn, MIN(period_end) AS a, "
            "MAX(period_end) AS b, MIN(period_label) AS la, MAX(period_label) AS lb FROM observations "
            f"WHERE code IN ({marks}) AND state IN ('current', 'old') AND run_id IS NOT NULL "
            "GROUP BY code, run_id", part)
    first = {}
    for r in rows:
        first[r["code"]] = min(first.get(r["code"], r["run_id"]), r["run_id"])
    out = []
    for r in sorted(rows, key=lambda x: (x["f"] or "", x["run_id"], x["code"]), reverse=True)[:MAX_UPDATE_ROWS]:
        s = info.get(r["code"])
        if s is None:
            continue
        initial = r["run_id"] == first[r["code"]]
        new, changed = (r["n"], 0) if initial else (r["nn"] or 0, r["n"] - (r["nn"] or 0))
        what = "первая загрузка" if initial else ("новые данные" if new and not changed else
                                                  "уточнение прошлых периодов" if changed and not new else
                                                  "новые данные и уточнение")
        dims = series_dims(s)
        head = dims[0][1] if dims and dims[0][0] == "Показатель" else (s["name"] or "")
        when = (r["f"] or "").replace("T", " ")[:19]
        out.append([when, when[:10], what, str(new), str(changed), r["a"], r["b"], r["code"], s["name"] or "",
                    head, P.PERIODICITY_RU.get(s["periodicity"], s["periodicity"] or ""),
                    SOURCE_TITLES.get(s["source"], s["source"] or "")])
    return _csv(out, UPDATES_COLS)


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
        codes, windows = model_plan(cfg, store, m)
        packet, series, table = build(store, codes, windows, flat=True)
        path = packet_path(paths, m["name"])
        try:
            changed = write_if_changed(path, packet)
            changed_s = write_if_changed(series_path(path), series)
            changed_t = write_if_changed(table_path(path), table)
            changed_u = write_if_changed(updates_path(path), updates_table(store, codes))
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
        n_rows = table.count(b"\n") - 1
        if n_rows > EXCEL_ROWS and layout(cfg, m) == "long":
            kw["last_error"] = (f"В модели «{m['name']}» {n_rows:,} строк — больше, чем вмещает лист Excel. "
                                "Сузьте выборку (меньше рядов или период короче).").replace(",", " ")
        store.set_model_state(m["name"], **kw)
        out[m["name"]] = "changed" if (changed or changed_s or changed_t or changed_u) else "same"
    return out


LAYOUTS = {"long": "строки: каждое значение — отдельная строка со всеми полями",
           "wide": "столбцы: каждый ряд — отдельный столбец, даты по строкам"}


def layout(cfg, model) -> str:
    """Вид таблицы «Хаб» в модели: long (плоская, по умолчанию) или wide (ряды по столбцам)."""
    model = model or {}
    if not model.get("layout") and model.get("connected_method"):
        return "wide"          # подключена до версии 0.5.4 — в книге таблица по столбцам
    lay = model.get("layout") or cfg.settings().get("table_layout") or "long"
    return lay if lay in LAYOUTS else "long"


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
