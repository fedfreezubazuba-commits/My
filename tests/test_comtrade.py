"""ООН — UN Comtrade: открытый доступ без ключа, по году за запрос, вес / стоимость / средняя цена."""
import datetime as dt
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from hub.sources import Context, _web as W, comtrade as CT, get

NOW = dt.datetime(2026, 10, 22, 12, 0)
PARAMS = {"reporter": 156, "partner": 643, "flow": "M", "cmd": "271111", "from_year": 2024}


@pytest.fixture
def api(monkeypatch):
    """Подмена UN Comtrade: kg = 1e6·(месяц + 100·(год−2000)), $ = 0,6 $/кг; лишние строки разбивок —
    по видам транспорта и таможенным режимам — должны отбрасываться."""
    calls = []
    state = {"one_period_only": False}

    def fake_get(url, ctx=None, timeout=60):
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        periods = q["period"].split(",")
        calls.append(periods)
        if state["one_period_only"] and len(periods) > 1:
            return 200, "application/json", json.dumps({"error": "Only one period allowed", "data": []}).encode(), url
        data = []
        for per in periods:
            y, m = int(per[:4]), int(per[4:])
            kg = 1e6 * (m + 100 * (y - 2000))
            row = {"refYear": y, "refMonth": m, "period": per, "reporterCode": 156, "flowCode": "M",
                   "partnerCode": 643, "partner2Code": 0, "cmdCode": "271111", "cmdDesc": "Natural gas, liquefied",
                   "customsCode": "C00", "motCode": 0, "qtyUnitAbbr": "kg", "qty": kg, "netWgt": kg,
                   "primaryValue": 0.6 * kg}
            data += [row, dict(row, motCode=2100, netWgt=1.0), dict(row, customsCode="C01", netWgt=2.0)]
        return 200, "application/json", json.dumps({"count": len(data), "data": data, "error": ""}).encode(), url
    monkeypatch.setattr(W, "http_get", fake_get)
    monkeypatch.setattr(CT, "PAUSE_S", 0)
    return calls, state


def test_comtrade_history_by_year(api):
    calls, _state = api
    res = get("comtrade").fetch(PARAMS, Context(now=NOW))
    assert [len(c) for c in calls] == [12, 12, 9] and calls[0][0] == "202401" and calls[-1][-1] == "202609"
    by = {s.name.split(" | ")[-1]: s for s in res.series}
    assert set(by) == {"Вес нетто", "Стоимость", "Средняя цена"}
    assert by["Вес нетто"].unit == "кг" and by["Средняя цена"].unit == "USD/т"
    assert by["Стоимость"].code == "COMTRADE_M_156_643_271111_USD"
    assert ("Страна-партнёр", "Россия") in by["Стоимость"].dims and ("Поток", "импорт") in by["Стоимость"].dims
    val = {(o.key.split("|")[-1], o.period_end): o.value for o in res.obs}
    d = dt.date(2025, 3, 31)
    assert val[("netWgt", d)] == 2503e6 and val[("primaryValue", d)] == pytest.approx(0.6 * 2503e6)
    assert val[("_price", d)] == pytest.approx(600.0)
    assert len([o for o in res.obs if o.key.endswith("netWgt")]) == 33       # без строк разбивок


def test_comtrade_one_period_fallback_and_refresh(api):
    calls, state = api
    state["one_period_only"] = True
    ctx = Context(now=NOW)
    ctx.last_period, ctx.full_reload = dt.date(2026, 8, 31), False
    res = get("comtrade").fetch(PARAMS, ctx)
    assert calls[0] == ["202605", "202606", "202607", "202608", "202609"]   # дозагрузка: 3 месяца назад
    assert calls[1:] == [["202605"], ["202606"], ["202607"], ["202608"], ["202609"]]
    assert res.partial and len({o.period_end for o in res.obs}) == 5
