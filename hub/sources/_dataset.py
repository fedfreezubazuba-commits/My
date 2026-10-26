"""
«Набор данных» в длинном виде — как ответ API, только файлом (CSV/Excel): каждая строка — дата, разрезы
(регион, категория, …) и числа. Так устроены, например, выгрузки СберИндекса: при каждом обновлении в файл
просто добавляется новый период.

Ряд = сочетание значений разрезов × числовой столбец. Отбор — как в навигаторе ЕМИСС: в `filters` только
разрезы, где отмечено не всё; новые значения разреза без отбора подхватываются сами.

Параметры (в показателе file_link / folder_inbox, mode: dataset):

    date_col: "Дата"
    dims: ["Регион", "Категория"]
    values: ["Значение"]
    unit_col: null
    filters: {"Регион": ["Россия", "Москва"]}
    periodicity: auto                     # auto | day | week | month | quarter | year
"""
import datetime as dt
import statistics

from ..core import periods as P
from ..core.config import slug
from ..core.errors import StructureError
from . import _forms as Fm
from . import _tables as T
from .base import FetchResult, Obs, SeriesInfo

MAX_DISTINCT = 5000          # разрез с бо́льшим числом значений — скорее идентификатор, а не разрез


def analyze(table) -> dict | None:
    """Похожа ли таблица на набор данных; если да — что в ней: дата, разрезы со значениями, числа."""
    if len(table.rows) < 3:
        return None
    lay = Fm.layout(table)
    if lay["date"] is None or not lay["values"]:
        return None
    head, rows = lay["head"], lay["rows"]
    dims = {}
    for j in lay["labels"]:
        vals = sorted({r[j] for r in rows if r[j]})
        if 1 <= len(vals) <= MAX_DISTINCT:
            dims[head[j]] = vals
    dates = sorted({d for d in (Fm.month_of(r[lay["date"]]) for r in rows) if d})
    return {"date": head[lay["date"]], "dims": dims, "values": [head[j] for j in lay["values"]],
            "unit": head[lay["unit"]] if lay["unit"] is not None else None,
            "periodicity": guess_periodicity(dates), "first": dates[0] if dates else None,
            "last": dates[-1] if dates else None}


def guess_periodicity(dates) -> str:
    """По датам строк: шаг около дня — дневные, недели — недельные, месяца — месячные…"""
    ds = sorted(set(dates))
    if len(ds) < 2:
        return P.MONTH
    gap = statistics.median((b - a).days for a, b in zip(ds, ds[1:]))
    if 7 <= gap <= 12 and all(d.day in (10, 20) or d == P.month_end(d.year, d.month) for d in ds):
        return P.DECADE                    # 10-е, 20-е и последние дни месяцев — декады
    for limit, per in ((2, P.DAY), (9, P.WEEK), (40, P.MONTH), (100, P.QUARTER), (200, P.HALF)):
        if gap <= limit:
            return per
    return P.YEAR


def params_for(info, filters=None, values=None, periodicity="auto") -> dict:
    return {"mode": "dataset", "date_col": info["date"], "dims": list(info["dims"]),
            "values": values or info["values"], "unit_col": info["unit"],
            "filters": {k: v for k, v in (filters or {}).items() if set(v) != set(info["dims"].get(k, []))},
            "periodicity": periodicity}


def count_series(table, params) -> int:
    """Сколько рядов получится при таком отборе (сочетания, которые реально есть в файле)."""
    idx = _columns(table, params)
    combos = {tuple(r[idx[d]] for d in params["dims"]) for r in _rows(table) if _keep(r, idx, params)}
    return len(combos) * len(params["values"])


def _rows(table):
    rows, _w = T._align(table.rows, table.kind)
    return rows


def _columns(table, params) -> dict:
    head = [h or f"Столбец {j + 1}" for j, h in enumerate(table.header or [])]
    idx = {h: j for j, h in enumerate(head)}
    need = [params["date_col"], *params["dims"], *params["values"]] + ([params["unit_col"]] if params.get("unit_col") else [])
    missing = [c for c in need if c not in idx]
    if missing:
        raise StructureError("В файле больше нет столбцов: " + ", ".join(missing) + " — возможно, источник изменил формат")
    return idx


def _keep(row, idx, params) -> bool:
    for col, allowed in (params.get("filters") or {}).items():
        if col in idx and row[idx[col]] not in allowed:
            return False
    return True


def to_result(table, params, prefix, url) -> FetchResult:
    idx = _columns(table, params)
    rows = [r for r in _rows(table) if _keep(r, idx, params)]
    per = params.get("periodicity") or "auto"
    dated = [(Fm.month_of(r[idx[params["date_col"]]]), r) for r in rows]
    if per == "auto":
        per = guess_periodicity([d for d, _r in dated if d])
    res = FetchResult(url=url)
    seen = {}
    many_values = len(params["values"]) > 1
    for d, r in dated:
        if d is None:
            continue
        end = P.end_of_period(per, d)
        parts = [r[idx[c]] for c in params["dims"]]
        for col in params["values"]:
            v = P.parse_number(r[idx[col]]) if r[idx[col]] else None
            if v is None:
                continue
            key = "ds|" + "|".join(f"{c}={r[idx[c]]}" for c in params["dims"]) + f"|{col}"
            if key not in seen:
                unit = r[idx[params["unit_col"]]] if params.get("unit_col") else T.unit_from_header(col)
                name = " | ".join([p for p in parts if p] + ([col] if many_values or not parts else []))
                seen[key] = SeriesInfo(
                    key=key, name=name[:200], unit=unit, periodicity=per, url=url,
                    code=f"{prefix}_{slug('_'.join(parts), 22)}_{slug(col, 10)}".upper(),
                    dims=[(c, r[idx[c]]) for c in params["dims"]] + [("Показатель", col)],
                    date_rule="дата строки файла → конец периода")
                res.series.append(seen[key])
            res.obs.append(Obs(key=key, period_end=end, period_label=P.label(per, end), value=v,
                               source_date=r[idx[params["date_col"]]]))
    if not res.obs:
        raise StructureError("По выбранным значениям в файле нет ни одного числа")
    return res
