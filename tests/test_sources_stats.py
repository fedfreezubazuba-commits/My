"""Адаптеры ЕМИСС и ЦБ на образцах."""
import datetime as dt

import pytest

from hub.core.errors import StructureError
from hub.sources import Context, get
from hub.sources.cbr import cbr_period_end
from hub.sources.fedstat import fedstat_period


def test_cbr_fetch_dates_and_codes(fakes):
    res = get("cbr").fetch({"category": 18, "indicators": [37], "m1": [2]}, Context(clients=fakes))
    assert [s.code for s in res.series] == ["CBR_18_37_m1_2"]
    obs = sorted(res.obs, key=lambda o: o.period_end)
    # январь 2024: дата ЦБ 01.02.2024 → конец периода 31.01.2024, исходная дата сохранена
    assert obs[0].period_end == dt.date(2024, 1, 31)
    assert obs[0].period_label == "2024-01"
    assert obs[0].source_date == "2024-02-01"
    assert obs[-1].period_end == dt.date(2025, 9, 30)
    assert res.series[0].unit == "% годовых"
    assert res.source_updated == "2026-10-10"
    assert res.raw and b"/dataNew" in res.raw
    assert "1 ряд" in res.describe() and "30.09.2025" in res.describe()


def test_cbr_request_params_like_repeat_string(fakes):
    get("cbr").fetch({"category": 18, "indicators": [37], "m1": [2], "y1": 2024}, Context(clients=fakes))
    data_calls = [p for path, p in fakes["cbr"].calls if path == "/dataNew"]
    assert data_calls == [[("categoryId", 18), ("y1", 2024), ("y2", 2025), ("i_ids", 37), ("m1_ids", 2)]]


def test_cbr_period_rules():
    assert cbr_period_end(dt.date(2025, 4, 1), "quarter") == (dt.date(2025, 3, 31), "2025-Q1")
    assert cbr_period_end(dt.date(2026, 1, 1), "year") == (dt.date(2025, 12, 31), "2025")
    assert cbr_period_end(dt.date(2025, 3, 14), "day") == (dt.date(2025, 3, 14), "2025-03-14")
    assert cbr_period_end(dt.date(2025, 2, 1), "month", "as_is") == (dt.date(2025, 2, 28), "2025-02")


def test_cbr_check_updated(fakes):
    assert get("cbr").check_updated({"category": 18, "indicators": [37]}, Context(clients=fakes)) == "2026-10-10"


def test_fedstat_fetch(fakes):
    res = get("fedstat").fetch({"id": 31074, "select": {"0": ["1558883", "1558900"]}, "years_from": 2023},
                               Context(clients=fakes))
    codes = {s.code for s in res.series}
    assert "FEDSTAT_31074_643_11_M" in codes
    assert "FEDSTAT_31074_643_11_Y" in codes
    monthly = [o for o in res.obs if o.key.endswith("P=M") and "s_OKATO=643" in o.key]
    first = min(monthly, key=lambda o: o.period_end)
    assert first.period_end == dt.date(2023, 1, 31) and first.value == 60000.0
    yearly = [o for o in res.obs if o.key.endswith("P=Y") and "s_OKATO=643" in o.key]
    assert {o.period_end for o in yearly} == {dt.date(2023, 12, 31), dt.date(2024, 12, 31)}
    names = {s.name for s in res.series}
    assert any("Российская Федерация" in n for n in names)        # разрез, который различается, — в названии
    assert all("Всего по обследуемым" not in n for n in names)    # одинаковый у всех — не засоряет название
    # выгрузка шла POST с токеном и выбранными значениями; страница показателя читалась один раз
    pages = [u for u, d in fakes["fedstat"].calls if d is None]
    assert len(pages) == 1
    post = [d for u, d in fakes["fedstat"].calls if d is not None][0]
    assert ("token", "SYNTHETICTOKEN123") in post
    assert ("selectedFilterIds", "0_1558883") in post and ("selectedFilterIds", "3_2023") in post


def test_fedstat_lost_value_warns(fakes):
    res = get("fedstat").fetch({"id": 31074, "select": {"0": ["1558883", "999"]}}, Context(clients=fakes))
    assert any("исчезли" in w for w in res.warnings)


@pytest.mark.parametrize("time,period,expected", [
    ("2024", "январь", ("M", dt.date(2024, 1, 31), "2024-01")),
    ("2024", "Февраль", ("M", dt.date(2024, 2, 29), "2024-02")),
    ("2024", "январь-март", ("MC", dt.date(2024, 3, 31), "2024-03")),
    ("2024", "I квартал", ("Q", dt.date(2024, 3, 31), "2024-Q1")),
    ("2024", "3 квартал", ("Q", dt.date(2024, 9, 30), "2024-Q3")),
    ("2024", "II полугодие", ("H", dt.date(2024, 12, 31), "2024-H2")),
    ("2024", "значение показателя за год", ("Y", dt.date(2024, 12, 31), "2024")),
    ("2024", "на 1 января", ("SY", dt.date(2023, 12, 31), "2023")),
    ("2024", "на 1 апреля", ("S", dt.date(2024, 3, 31), "2024-03")),
    ("15.03.2024", "", ("D", dt.date(2024, 3, 15), "2024-03-15")),
])
def test_fedstat_periods(time, period, expected):
    assert fedstat_period(time, period) == expected


def test_fedstat_unknown_period():
    with pytest.raises(StructureError):
        fedstat_period("2024", "неведомый период")


def test_fedstat_big_indicator_downloads_in_parts(fakes, monkeypatch):
    """Сервер не отдаёт больше одного года за раз → хаб дробит выгрузку и склеивает результат."""
    import hub.sources.fedstat as FS
    monkeypatch.setattr(FS.time, "sleep", lambda s: None)
    fs = fakes["fedstat"]
    fs.max_years = 1
    said = []
    ctx = Context(clients=fakes)
    ctx.progress = said.append
    res = get("fedstat").fetch({"id": 31074, "select": {"0": ["1558883"]}}, ctx)
    assert fs.downloads == [["2023"], ["2024"], ["2025"]]
    assert len([o for o in res.obs if o.key.endswith("P=M")]) == 12 + 12 + 8
    assert any("поменьше" in s for s in said)
    assert not res.partial


def test_fedstat_small_indicator_in_one_request(fakes):
    get("fedstat").fetch({"id": 31074, "select": {"0": ["1558883"]}}, Context(clients=fakes))
    assert fakes["fedstat"].downloads == [["2023", "2024", "2025"]]


def test_fedstat_incremental_update_loads_recent_years(fakes):
    ctx = Context(clients=fakes)
    ctx.last_period, ctx.full_reload = dt.date(2025, 8, 31), False
    res = get("fedstat").fetch({"id": 31074, "select": {"0": ["1558883"]}}, ctx)
    assert fakes["fedstat"].downloads == [["2024", "2025"]] and res.partial


def test_fedstat_dims_and_unit():
    from hub.sources.fedstat import clean, is_unit_dim
    assert clean("Тонна;^метрическая тонна (1000 кг)") == "Тонна; метрическая тонна (1000 кг)"
    assert is_unit_dim("Единица измерения") and not is_unit_dim("Регион")


def test_fedstat_series_have_separate_dims(fakes):
    res = get("fedstat").fetch({"id": 31074, "select": {"0": ["1558883", "1558900"]}}, Context(clients=fakes))
    s = next(s for s in res.series if s.code.endswith("643_11_M"))
    assert s.dims[0][0] == "Показатель"
    assert ("Классификатор объектов административно-территориального деления (ОКАТО)", "Российская Федерация") in s.dims


def test_fedstat_release_from_page():
    from hub.core import periods as P
    from hub.sources.fedstat import release_from_page
    today = dt.date(2026, 10, 19)
    rule = ("<table><tr><td>Срок предоставления информации</td>"
            "<td>ежемесячно, на 25-й рабочий день после отчетного периода</td></tr></table>")
    # последние данные за август → следующие за сентябрь: 25 рабочих дней после 30.09 (4 ноября — праздник)
    assert release_from_page(rule, dt.date(2026, 8, 31), P.MONTH, today)[0] == "2026-11-05"
    exact = "<div>Дата очередного обновления: 20.11.2026</div>" + rule
    d, note = release_from_page(exact, dt.date(2026, 8, 31), P.MONTH, today)
    assert d == "2026-11-20" and "25-й рабочий день" in note
    day = "<td>Срок размещения</td><td>20 числа месяца, следующего за отчетным</td>"
    assert release_from_page(day, dt.date(2026, 8, 31), P.MONTH, today)[0] == "2026-10-20"
    assert release_from_page("<p>ничего о сроках</p>", dt.date(2026, 8, 31), P.MONTH, today) == (None, "")
    old = "<div>Дата очередного обновления: 20.11.2020</div>"
    assert release_from_page(old, dt.date(2026, 8, 31), P.MONTH, today) == (None, "")


def test_fedstat_fetch_reads_release_from_same_page(fakes):
    fakes["fedstat"].page += "<div>Дата очередного обновления: 20.11.2099</div>".encode("utf-8")
    res = get("fedstat").fetch({"id": 31074, "select": {"0": ["1558883"]}, "years_from": 2023},
                               Context(clients=fakes))
    assert res.next_release == "2099-11-20"
    assert len([u for u, d in fakes["fedstat"].calls if d is None]) == 1     # лишних запросов нет


def test_fedstat_csrf_block_refreshes_token(fakes):
    """Сервер принимает токен один раз: на следующий запрос — «CSRF блокировка», хаб берёт новый токен."""
    fs = fakes["fedstat"]
    state = {"n": 0, "token": "SYNTHETICTOKEN123"}
    orig_get, orig_filtered = fs._get, fs.filtered

    def page_get(url, data=None, retries=4):
        if "/indicator/" in url and "downloadData" not in url:
            fs.calls.append((url, data))
            return "text/html", fs.page.replace(b"SYNTHETICTOKEN123", state["token"].encode())
        return orig_get(url, data, retries)

    def filtered(data):
        if ("token", state["token"]) not in data:
            return b"<html><body>CSRF \xd0\xb1\xd0\xbb\xd0\xbe\xd0\xba\xd0\xb8\xd1\x80\xd0\xbe\xd0\xb2\xd0\xba\xd0\xb0</body></html>"
        state["n"] += 1
        state["token"] = f"TOKEN{state['n']}"
        return orig_filtered(data)

    fs._get, fs.filtered = page_get, filtered
    res = get("fedstat").fetch({"id": 31074, "select": {"0": ["1558883"]}, "years_from": 2023, "chunk_cells": 1},
                               Context(clients=fakes))
    assert len(fs.downloads) >= 2 and res.obs


def test_fedstat_splits_a_year_by_dimension_values(fakes, monkeypatch):
    """Если сервер не отдаёт даже один год, год делится по значениям самого большого измерения."""
    import hub.sources.fedstat as FS
    monkeypatch.setattr(FS.time, "sleep", lambda s: None)
    fs = fakes["fedstat"]
    fs.max_regions_years = 1                     # за один запрос — только один регион за один год
    res = get("fedstat").fetch({"id": 31074, "select": {"0": ["1558883", "1558900"]}, "years_from": 2023},
                               Context(clients=fakes))
    regions = {o.key.split("s_OKATO=")[1].split("|")[0] for o in res.obs}
    assert regions == {"643", "030"}
    assert all(len(y) == 1 for y in fs.downloads)


def test_fedstat_huge_year_is_split_upfront(fakes, monkeypatch):
    import hub.sources.fedstat as FS
    monkeypatch.setattr(FS.time, "sleep", lambda s: None)
    res = get("fedstat").fetch({"id": 31074, "select": {"0": ["1558883", "1558900"]}, "years_from": 2023,
                                "chunk_cells": 1}, Context(clients=fakes))
    assert res.obs and len(fakes["fedstat"].downloads) >= 4      # 2 года × 2 региона


def test_fedstat_token_is_single_use_no_wasted_requests(fakes):
    """Токен одноразовый: перед каждой выгрузкой — свежая страница, ни одного отказа «CSRF блокировка»."""
    fs = fakes["fedstat"]
    state = {"n": 0, "token": "SYNTHETICTOKEN123", "blocked": 0}
    orig_get, orig_filtered = fs._get, fs.filtered

    def page_get(url, data=None, retries=4):
        if "/indicator/" in url and "downloadData" not in url:
            fs.calls.append((url, data))
            return "text/html", fs.page.replace(b"SYNTHETICTOKEN123", state["token"].encode())
        return orig_get(url, data, retries)

    def filtered(data):
        if ("token", state["token"]) not in data:
            state["blocked"] += 1
            return b"<html><body>CSRF</body></html>"
        state["n"] += 1
        state["token"] = f"TOKEN{state['n']}"
        return orig_filtered(data)

    fs._get, fs.filtered = page_get, filtered
    res = get("fedstat").fetch({"id": 31074, "select": {"0": ["1558883"]}, "years_from": 2023, "chunk_cells": 1},
                               Context(clients=fakes))
    assert res.obs and state["n"] >= 2 and state["blocked"] == 0


def test_fedstat_refuses_too_many_requests(fakes, monkeypatch):
    import hub.sources.fedstat as FS
    from hub.core.errors import TOO_BIG, HubError
    monkeypatch.setattr(FS, "MAX_REQUESTS", 2)
    with pytest.raises(HubError) as e:
        get("fedstat").fetch({"id": 31074, "years_from": 2000, "chunk_cells": 1}, Context(clients=fakes))
    assert e.value.category == TOO_BIG and "меньше" in str(e.value)
    assert not fakes["fedstat"].downloads                     # ни одной выгрузки не сделано


def test_fedstat_browser_headers_and_saved_cookies(tmp_path):
    import json
    from hub.core.paths import Paths
    paths = Paths(tmp_path).ensure()
    (paths.cache / "cookies").mkdir(parents=True, exist_ok=True)
    (paths.cache / "cookies" / "www.fedstat.ru.json").write_text(json.dumps(
        {"user_agent": "Браузер-хаба", "cookies": [{"name": "guard", "value": "ok", "domain": ".fedstat.ru",
                                                    "path": "/", "expires": -1}]}), encoding="utf-8")
    fs = get("fedstat").client(Context(paths=paths))
    heads = dict(fs.op.addheaders)
    assert heads["User-Agent"] == "Браузер-хаба" and "ru-RU" in heads["Accept-Language"]
    jar = next(h.cookiejar for h in fs.op.handlers if hasattr(h, "cookiejar"))
    assert [c.name for c in jar] == ["guard"]


def test_fedstat_value_found_by_title_when_renumbered():
    """С 2023 года у «К предыдущему месяцу» новый номер — хаб берёт оба (по названию)."""
    from hub.sources.fedstat import FedstatSource
    info = {"filters": {"57956": {"title": "Типы цен", "values": {
        "1": "К предыдущему месяцу", "2": "К декабрю предыдущего года", "9": "к предыдущему месяцу "}}}}
    warnings = []
    sel = FedstatSource()._select(info, {"select": {"57956": ["1"]}}, warnings)
    assert sel == {"57956": ["1", "9"]} and not warnings
    # старый номер исчез совсем — находим по сохранённому названию
    info["filters"]["57956"]["values"].pop("1")
    sel = FedstatSource()._select(info, {"select": {"57956": ["1"]},
                                         "select_titles": {"57956": ["К предыдущему месяцу"]}}, warnings)
    assert sel == {"57956": ["9"]} and not warnings


def test_fedstat_coverage_warning():
    from hub.sources.base import FetchResult, Obs
    from hub.sources.fedstat import FedstatSource
    info = {"filters": {"3": {"title": "Год", "values": {"a": "2021", "b": "2022", "c": "2025", "d": "2026"}}}}
    res = FetchResult(obs=[Obs(key="k", period_end=dt.date(2022, 12, 31), period_label="2022-12", value=1.0)])
    FedstatSource._coverage(info, ["a", "b", "c", "d"], res)
    assert "до 2026" in res.warnings[0] and "только до 2022" in res.warnings[0]
    res2 = FetchResult(obs=[Obs(key="k", period_end=dt.date(2026, 8, 31), period_label="2026-08", value=1.0)])
    FedstatSource._coverage(info, ["a", "b", "c", "d"], res2)
    assert not res2.warnings
