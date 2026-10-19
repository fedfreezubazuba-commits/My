"""
Банк России, «Сервис получения данных» (cbr.ru/dataservice).

Обёртка над проверенным клиентом cbr_api.CBR — запросы, паузы и повторы не меняются.
Параметры показателя — те же, что в строке «Повторить» навигатора:

    {category: 18, indicators: [37], m1: [2], m2: [], y1: 2015}

Даты: ЦБ ставит в поле date «отчётную дату» — для месячных рядов 1-е число следующего
месяца, для остатков дату «на 1 число». Хаб приводит её к концу отчётного периода
(01.02.2025 → 31.01.2025), а исходную дату сохраняет в source_date.
Если для какого-то показателя это правило не подходит, в параметрах можно указать
date_rule: as_is (тогда дата приводится к концу периода, в который она попадает).
"""
import datetime as dt
import json

from ..core import periods as P
from ..core.errors import StructureError
from .base import FetchResult, Obs, SeriesInfo, Source
from .cbr_api import CBR, PERIODICITY, SITE

PER_EN = {v: k for k, v in PERIODICITY.items()}
SERVICE_URL = SITE + "/statistics/data-service/"


def cbr_period_end(date: dt.date, periodicity: str, rule: str = "next_period"):
    """(конец периода, подпись) по дате ЦБ."""
    if periodicity in (P.DAY, P.WEEK):
        return date, P.label(periodicity, date)
    if rule != "as_is" and date.day == 1:
        end = date - dt.timedelta(days=1)
    else:
        end = date
    end = P.end_of_period(periodicity, end)
    return end, P.label(periodicity, end)


def _ids(v):
    if v in (None, ""):
        return []
    if isinstance(v, (int, str)):
        v = [v]
    return [int(x) for x in v]


class CbrSource(Source):
    kind = "cbr"
    title = "Банк России"

    def client(self, ctx):
        return (ctx.clients.get("cbr") if ctx else None) or CBR()

    def host(self, params):
        return "www.cbr.ru"

    def describe(self, params):
        inds = ", ".join(map(str, _ids(params.get("indicators")))) or "все"
        return f"Банк России: категория {params.get('category')}, показатели {inds}"

    def open_url(self, params):
        return SERVICE_URL

    def check_updated(self, params, ctx):
        cbr = self.client(ctx)
        upd = cbr.updated(int(params["category"]))
        want = _ids(params.get("indicators"))
        dates = [d for k, d in upd.items() if d and (not want or k in want)]
        return max(dates) if dates else None

    def fetch(self, params, ctx) -> FetchResult:
        cbr = self.client(ctx)
        cat = int(params["category"])
        i_ids, m1, m2 = _ids(params.get("indicators")), _ids(params.get("m1")), _ids(params.get("m2"))
        rule = params.get("date_rule", "next_period")

        recorded = []
        orig = cbr._get

        def recording_get(path, prm=(), retries=4):
            data = orig(path, prm, retries)
            recorded.append({"path": path, "params": list(prm), "response": data})
            return data

        cbr._get = recording_get
        try:
            inds = cbr.indicators(cat)
            if not inds:
                raise StructureError(f"В каталоге ЦБ нет категории {cat}")
            sel = [c for c in inds if not i_ids or c["indicator_id"] in i_ids]
            if not sel:
                raise StructureError(f"В категории {cat} нет показателей {i_ids}")
            y1 = int(params.get("y1") or min(c["begin_dt"] for c in sel))
            y2 = max(max(c["end_dt"] for c in sel), y1)
            rows, _links = cbr.data(cat, y1, y2, i_ids, m1, m2)
            updated = None
            try:
                upd = cbr.updated(cat)
                dates = [d for k, d in upd.items() if d and (not i_ids or k in i_ids)]
                updated = max(dates) if dates else None
            except Exception:  # noqa: BLE001 — дата обновления не обязательна
                pass
        finally:
            cbr._get = orig

        res = FetchResult(url=SERVICE_URL, source_updated=updated, raw_ext="json",
                          raw=json.dumps(recorded, ensure_ascii=False, default=str).encode("utf-8"))
        units_per_key = {}
        for r in rows:
            units_per_key.setdefault((r["indicator_id"], r["measure1_id"], r["measure2_id"]), set()).add(r["unit"])
        seen = {}
        for r in rows:
            if r["value"] is None:
                continue
            per = PER_EN.get(r["periodicity"], r["periodicity"])
            if per not in P.ORDER:
                raise StructureError(f"Неизвестная периодичность ЦБ «{r['periodicity']}»")
            key = f"{r['indicator_id']}|{r['measure1_id']}|{r['measure2_id']}|{r['unit']}|{per}"
            if key not in seen:
                code = f"CBR_{cat}_{r['indicator_id']}"
                if r["measure1_id"] is not None:
                    code += f"_m1_{r['measure1_id']}"
                if r["measure2_id"] is not None:
                    code += f"_m2_{r['measure2_id']}"
                if len(units_per_key[(r["indicator_id"], r["measure1_id"], r["measure2_id"])]) > 1:
                    code += f"_u{len(seen) + 1}"
                name = " | ".join(p for p in (r["indicator"], r["measure1"], r["measure2"]) if p)
                rule_text = ("дата ЦБ «1-е число следующего периода» → конец периода"
                             if rule != "as_is" and per not in (P.DAY, P.WEEK) else "дата ЦБ")
                seen[key] = SeriesInfo(key=key, name=name, unit=r["unit"] or "", periodicity=per, code=code,
                                       url=SERVICE_URL, date_rule=rule_text)
                res.series.append(seen[key])
            end, label = cbr_period_end(r["date"], per, rule)
            try:
                value = float(r["value"])
            except (TypeError, ValueError):
                raise StructureError(f"ЦБ вернул не число: {r['value']!r}")
            res.obs.append(Obs(key=key, period_end=end, period_label=label, value=value,
                               source_date=r["date"].isoformat()))
        return res
