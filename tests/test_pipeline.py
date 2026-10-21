"""Приёмка этапа 1: от сохранённых ответов ЦБ и ЕМИСС до посылки модели."""
import csv
import io
import os
import urllib.error

import pytest

from hub.core.errors import FileBusy
from hub.excel import mcode, packet


def add_jobs(runner):
    cbr = runner.cfg.add_job("cbr", "Ставки по вкладам физлиц", {"category": 18, "indicators": [37], "m1": [2]})
    fs = runner.cfg.add_job("fedstat", "Зарплата", {"id": 31074, "select": {"0": ["1558883"]}, "years_from": 2023})
    return cbr, fs


def read_packet(path):
    text = path.read_bytes()
    assert text.startswith(b"\xef\xbb\xbf"), "посылка должна быть в UTF-8 с BOM"
    return list(csv.reader(io.StringIO(text.decode("utf-8-sig")), delimiter=";"))


def test_packet_from_saved_responses(runner):
    cbr, fs = add_jobs(runner)
    runner.cfg.add_model("Сбербанк", "C:/модели/Сбербанк.xlsx")
    runner.cfg.subscribe("Сбербанк", job_id=cbr["id"])
    runner.cfg.subscribe("Сбербанк", job_id=fs["id"])
    rep = runner.run(force=True)
    assert {o.status for o in rep.outcomes} == {"updated"}
    p = packet.packet_path(runner.paths, "Сбербанк")
    rows = read_packet(p)
    assert rows[0] == ["Дата", "Период", "Код", "Название", "Единицы", "Значение"]
    first = rows[1]
    assert first[:3] == ["2024-01-31", "2024-01", "CBR_18_37_m1_2"]
    assert first[4] == "% годовых" and first[5] == "14"
    codes = list(dict.fromkeys(r[2] for r in rows[1:]))
    assert codes[0] == "CBR_18_37_m1_2"
    assert set(codes[1:]) == {"FEDSTAT_31074_643_11_M", "FEDSTAT_31074_643_11_Y"}
    # точка — десятичный разделитель, без экспоненты
    assert all("," not in r[5] and "e" not in r[5].lower() for r in rows[1:])
    srows = read_packet(packet.series_path(p))
    assert srows[0][:6] == ["Код", "Название", "Единицы", "Периодичность", "Последняя дата", "Последнее значение"]
    assert srows[1][0] == "CBR_18_37_m1_2" and srows[1][4] == "2025-09-30" and srows[1][5] == "19"
    assert srows[1][6] == "18.75"


def test_rerun_without_new_data_keeps_packet(runner):
    cbr, _ = add_jobs(runner)
    runner.cfg.add_model("М", "x.xlsx")
    runner.cfg.subscribe("М", job_id=cbr["id"])
    runner.run(force=True)
    p = packet.packet_path(runner.paths, "М")
    before = (p.read_bytes(), packet.series_path(p).read_bytes())
    os.utime(p, (1_000_000, 1_000_000))
    rep = runner.run(force=True)
    assert rep.packets["М"] == "same"
    assert (p.read_bytes(), packet.series_path(p).read_bytes()) == before
    assert os.path.getmtime(p) == 1_000_000            # файл даже не переписывался
    # синтетические данные заканчиваются в 2025 году, поэтому «задерживается» — тоже без изменений
    assert all(o.status in ("waiting", "unchanged", "delayed") and not o.changed for o in rep.outcomes)


def test_new_period_and_new_series_on_the_right(runner, fakes):
    cbr, fs = add_jobs(runner)
    runner.cfg.add_model("М", "x.xlsx")
    runner.cfg.subscribe("М", job_id=fs["id"])
    runner.run(force=True)
    order1 = runner.store.subscribed("М")
    # новый период у ЦБ (октябрь 2025) и новый ряд в подписке
    fakes["cbr"].add_month(37, 2, "2025-11-01", 19.5)
    runner.cfg.subscribe("М", job_id=cbr["id"])
    rep = runner.run(force=True)
    cbr_out = next(o for o in rep.outcomes if o.job["id"] == cbr["id"])
    assert cbr_out.status == "updated"
    order2 = runner.store.subscribed("М")
    assert order2[:len(order1)] == order1               # старые столбцы не сдвинулись
    assert order2[len(order1):] == ["CBR_18_37_m1_2"]    # новый — справа
    rows = read_packet(packet.packet_path(runner.paths, "М"))
    assert list(dict.fromkeys(r[2] for r in rows[1:])) == order2
    assert ["2025-10-31", "2025-10", "CBR_18_37_m1_2"] == rows[-1][:3]
    wide = packet.wide(runner.store, order2)
    assert wide[-1][1] == "2025-10" and wide[-1][2]["CBR_18_37_m1_2"] == 19.5
    # на дату 31.12.2024 есть и месячный, и годовой ряд — подпись берётся самая подробная
    dec = next(w for w in wide if str(w[0]) == "2024-12-31")
    assert dec[1] == "2024-12"


def test_revision_is_kept_as_history(runner, fakes):
    cbr, _ = add_jobs(runner)
    runner.run(job_ids=[cbr["id"]])
    row = next(r for r in fakes["cbr"].full["RowData"] if r["indicator_id"] == 37 and r["measure1_id"] == 2
               and r["date"].startswith("2025-10-01"))
    row["obs_val"] = 18.9
    rep = runner.run(job_ids=[cbr["id"]])
    o = rep.outcomes[0]
    assert o.revisions == [("CBR_18_37_m1_2", "2025-09", 19.0, 18.9)]
    hist = runner.store.history("CBR_18_37_m1_2", "2025-09-30")
    assert [(h["value"], h["state"]) for h in hist] == [(19.0, "old"), (18.9, "current")]


def test_one_failure_does_not_stop_others(runner, fakes):
    cbr, fs = add_jobs(runner)
    fakes["fedstat"].fail = urllib.error.HTTPError("https://www.fedstat.ru", 503, "busy", {}, None)
    rep = runner.run(force=True)
    st = {o.job["id"]: o for o in rep.outcomes}
    assert st[cbr["id"]].status == "updated"
    assert st[fs["id"]].status == "retry" and st[fs["id"]].category == "rate_limit"
    # после трёх сбоев подряд — красный статус
    for _ in range(2):
        rep = runner.run(job_ids=[fs["id"]])
    assert rep.outcomes[0].status == "error"
    assert runner.store.state(fs["id"])["fail_count"] == 3


def test_busy_packet_keeps_data_and_retries(runner, fakes, monkeypatch):
    cbr, _ = add_jobs(runner)
    runner.cfg.add_model("М", "x.xlsx")
    runner.cfg.subscribe("М", job_id=cbr["id"])
    real = packet.write_if_changed

    def busy(path, data):
        raise FileBusy(path)
    monkeypatch.setattr(packet, "write_if_changed", busy)
    rep = runner.run(force=True)
    assert rep.packets["М"].startswith("busy")
    assert runner.store.current("CBR_18_37_m1_2")            # данные уже в базе
    monkeypatch.setattr(packet, "write_if_changed", real)
    rep = runner.run()                                         # следующий проход без новых загрузок
    assert rep.packets["М"] == "changed"
    assert packet.packet_path(runner.paths, "М").exists()


def test_quarantine_blocks_packet_until_accepted(runner, fakes):
    cbr = runner.cfg.add_job("cbr", "Ставки", {"category": 18, "indicators": [37], "m1": [2]},
                             checks={"max_jump_pct": 20})
    runner.cfg.add_model("М", "x.xlsx")
    runner.cfg.subscribe("М", job_id=cbr["id"])
    runner.run(force=True)
    fakes["cbr"].add_month(37, 2, "2025-11-01", 95.0)          # скачок с 19 до 95
    rep = runner.run(force=True)
    o = rep.outcomes[0]
    assert o.status == "quarantine" and "Скачок" in o.message
    p = packet.packet_path(runner.paths, "М")
    assert "2025-10-31" not in p.read_text("utf-8-sig")       # в модель не попало
    q = runner.store.pending_quarantine(cbr["id"])[0]
    runner.store.accept_quarantine(q["id"])
    runner.run(force=True)
    assert "2025-10-31" in p.read_text("utf-8-sig")


def test_codes_are_stable(runner, fakes):
    cbr, _ = add_jobs(runner)
    runner.run(force=True)
    code = runner.store.series_of_job(cbr["id"])[0]["code"]
    for l in fakes["cbr"].full["Links"]:
        l["indicator_name"] = "Переименованный показатель"
    runner.run(force=True)
    s = runner.store.series_of_job(cbr["id"])
    assert [r["code"] for r in s] == [code] and s[0]["name"].startswith("Переименованный")


def test_m_template_has_no_hardcoded_columns(runner):
    cbr, _ = add_jobs(runner)
    runner.run(force=True)
    for name, code in mcode.queries("C:/MarketData/Для моделей/М.csv"):
        assert mcode.check_syntax(code) == [], name
        assert "CBR_" not in code and "FEDSTAT_" not in code
    for lay in ("long", "wide"):
        for name, code in mcode.queries("x.csv", layout=lay):
            assert mcode.check_syntax(code) == [], (lay, name)
    hub = dict(mcode.queries("x.csv", layout="wide"))["Хаб"]
    assert "Table.Pivot(" in hub and "List.Distinct(" in hub and '"en-US"' in hub
    assert "Хаб_Посылка" in hub and "error Error.Record" in hub
    flat = dict(mcode.queries("x.csv"))["Хаб"]
    assert "— таблица.csv" in flat and '"en-US"' in flat and "Table.ColumnNames" in flat
    assert "Table.Pivot(" not in flat


def test_m_syntax_checker_catches_errors():
    assert mcode.check_syntax("let a = (1 in a")
    assert mcode.check_syntax('let a = "x in a')
    assert mcode.check_syntax("let a = 1, in a")


def test_lock_prevents_parallel_runs(runner):
    from hub.runner import Busy, Lock
    with Lock(runner.paths.lock):
        with pytest.raises(Busy):
            runner.run(force=True)


def test_selection_gives_model_only_an_extract(runner):
    """Выжимка: из большого показателя модель получает только выбранные ряды и только с 2024 года."""
    fs = runner.cfg.add_job("fedstat", "Зарплата", {"id": 31074, "select": {"0": ["1558883", "1558900"]}})
    runner.run(force=True)
    assert len(runner.store.series_of_job(fs["id"])) == 4        # 2 региона × (месячные + годовые)
    runner.cfg.add_model("М", "x.xlsx")
    runner.cfg.subscribe("М", job_id=fs["id"])
    region = "Классификатор объектов административно-территориального деления (ОКАТО)"
    sel = runner.cfg.save_selection("М", {"job": fs["id"], "name": "Зарплата РФ, месячная, с 2024",
                                          "filters": {region: ["Российская Федерация"], "Периодичность": ["месячная"]},
                                          "from": "2024-01-01", "to": ""})
    assert sel["id"] == "s1" and fs["id"] not in runner.cfg.model("М")["jobs"]   # выборка заменила «весь показатель»
    runner.run()
    rows = read_packet(packet.packet_path(runner.paths, "М"))
    codes = {r[2] for r in rows[1:]}
    assert codes == {"FEDSTAT_31074_643_11_M"}
    assert min(r[0] for r in rows[1:]) == "2024-01-31"
    srows = read_packet(packet.series_path(packet.packet_path(runner.paths, "М")))
    assert len(srows) == 2 and srows[1][-5:-3] == [srows[1][-5], "Российская Федерация"]
    # новые регионы, подходящие под выборку, появятся сами; удаление выборки убирает ряды
    runner.cfg.remove_selection("М", "s1")
    runner.run()
    assert read_packet(packet.packet_path(runner.paths, "М"))[1:] == []


def test_selection_windows_merge():
    assert packet._merge_window(("2022-01-01", ""), ("2020-01-01", "2021-12-31")) == ("2020-01-01", "")
    assert packet._merge_window(("2022-01-01", ""), None) is None
    assert packet.in_window("2023-05-31", ("2022-01-01", "")) and not packet.in_window("2021-05-31", ("2022-01-01", ""))


def test_release_date_from_source_sets_expected(runner, fakes):
    fakes["fedstat"].page += "<div>Дата очередного обновления: 20.11.2099</div>".encode("utf-8")
    _, fs = add_jobs(runner)
    runner.run(job_ids=[fs["id"]])
    st = runner.store.state(fs["id"])
    assert st["release_next"] == "2099-11-20"
    assert st["expected_next"].startswith("2099-11-20T09:00")


def test_scheduled_empty_pass_is_cheap(runner, monkeypatch):
    cbr, _ = add_jobs(runner)
    runner.run(force=True)
    calls = []
    monkeypatch.setattr(packet, "rebuild", lambda *a, **k: calls.append(1) or {})
    rep = runner.run(scheduled=True)                 # всё только что проверено
    assert not rep.outcomes and not calls
    runner.store.meta_set("packets_built", "2000-01-01T00:00:00")
    runner.run(scheduled=True)
    assert calls == [1]                               # раз в час посылки всё же пересобираются


def test_selection_by_field_missing_in_some_series(runner):
    """«Вид значения» есть только у рядов «нарастающим итогом» (старые данные): остальные — «значение за период»."""
    import json
    from hub.sources.base import SeriesInfo
    job = runner.cfg.add_job("fedstat", "Цены", {"id": 1})
    for key, dims in (("a", [("Товар", "Кукуруза")]),
                      ("b", [("Товар", "Кукуруза"), ("Вид значения", "нарастающим итогом с начала года")])):
        code = runner.store.assign_code(job, SeriesInfo(key=key, name=key, unit="руб", periodicity="month",
                                                        code=f"FEDSTAT_1_{key}_M", dims=dims))
        runner.store.update_series_meta(code, SeriesInfo(key=key, name=key, unit="руб", periodicity="month",
                                                         code=code, dims=dims))
    sel = {"job": job["id"], "filters": {"Вид значения": ["значение за период"]}}
    assert packet.selection_codes(runner.store, sel) == ["FEDSTAT_1_a_M"]
    assert json.loads(runner.store.series("FEDSTAT_1_b_M")["dims"])[-1][0] == "Вид значения"


def test_old_exclusions_do_not_hide_selection(runner):
    from hub.excel import packet
    job = runner.cfg.add_job("fedstat", "Зарплата", {"id": 31074, "select": {"0": ["1558883"]}})
    runner.run(force=True)
    codes = [s["code"] for s in runner.store.series_of_job(job["id"])]
    runner.cfg.add_model("М", "C:/m.xlsx")
    runner.cfg.save_selection("М", {"job": job["id"], "name": "В", "filters": {}, "from": "", "to": ""})
    runner.cfg.unsubscribe_codes("М", codes, exclude=True)      # как после «Убрать» в 0.5.3
    assert packet.model_plan(runner.cfg, runner.store, runner.cfg.model("М"))[0] == codes
    assert runner.cfg.model("М")["exclude"] == []


def test_flat_table_has_all_fields_and_value_last(runner):
    """Плоская таблица: строка на значение, все поля ряда отдельными столбцами, значение — последним."""
    cbr, fs = add_jobs(runner)
    runner.cfg.add_model("М", "C:/m.xlsx")
    runner.cfg.save_selection("М", {"job": fs["id"], "name": "В", "filters": {"Периодичность": ["месячная"]},
                                     "from": "2024-01-01", "to": ""})
    runner.cfg.subscribe("М", job_id=cbr["id"])
    runner.run(force=True)
    p = packet.packet_path(runner.paths, "М")
    rows = read_packet(packet.table_path(p))
    head = rows[0]
    assert head[:3] == ["Дата", "Период", "Показатель"] and head[-1] == "Значение"
    assert head[-6:] == ["Периодичность", "Единицы", "Источник", "Код", "Обновлено", "Значение"]
    region = "Классификатор объектов административно-территориального деления (ОКАТО)"
    assert region in head
    fed = [dict(zip(head, r)) for r in rows[1:] if r[head.index("Код")].startswith("FEDSTAT")]
    assert fed and all(r["Дата"] >= "2024-01-01" for r in fed)
    assert {r[region] for r in fed} == {"Российская Федерация"} and fed[0]["Периодичность"] == "месячная"
    assert fed[0]["Обновлено"][:2] == "20" and float(fed[0]["Значение"]) > 0
    cb = [dict(zip(head, r)) for r in rows[1:] if r[head.index("Код")].startswith("CBR")]
    assert cb and cb[0][region] == "" and cb[0]["Источник"] == "Банк России"
    assert len(rows) - 1 == len(read_packet(p)) - 1          # те же значения, что в посылке


def test_layout_default_and_old_models(runner):
    runner.cfg.add_model("Новая", "C:/a.xlsx")
    assert packet.layout(runner.cfg, runner.cfg.model("Новая")) == "long"
    runner.cfg.update_model("Новая", connected_method="com")          # подключена до 0.5.4 — по столбцам
    assert packet.layout(runner.cfg, runner.cfg.model("Новая")) == "wide"
    runner.cfg.update_model("Новая", layout="long")
    assert packet.layout(runner.cfg, runner.cfg.model("Новая")) == "long"


def test_expected_date_ignores_discontinued_series(runner):
    """Товар, который перестали наблюдать в 2017-м, не делает показатель «задерживающимся»."""
    import datetime as dt
    from hub.core import schedule as S
    from hub.sources.base import Obs, SeriesInfo
    job = runner.cfg.add_job("fedstat", "Цены", {"id": 1})
    for key, last in (("old", "2017-12-31"), ("live", "2026-08-31")):
        info = SeriesInfo(key=key, name=key, unit="руб", periodicity="month", code=f"FEDSTAT_1_{key}_M")
        code = runner.store.assign_code(job, info)
        o = Obs(key=key, period_end=dt.date.fromisoformat(last), period_label=last[:7], value=1.0)
        run = runner.store.start_run(job["id"])
        runner.store.apply(code, runner.store.diff(code, [o]), run, "2026-10-01T10:00:00", initial=True)
    exp, per = S.expected_next(runner.store, runner.cfg.job(job["id"]))
    assert per == "month" and exp.date() > dt.date(2026, 9, 30)


def test_unparsed_timing_goes_to_claude_pack(runner, fakes):
    from hub.core import claude_pack
    fakes["fedstat"].page += ("<table><tr><td>Периодичность публикации</td><td>ежеквартально</td></tr>"
                              "<tr><td>Ответственный</td><td>Росстат</td></tr></table>").encode("utf-8")
    _, fs = add_jobs(runner)
    runner.run(job_ids=[fs["id"]])
    text = claude_pack.job_package(runner.cfg, runner.store, fs["id"], runner.paths)
    assert "о сроках обновления" in text and "ежеквартально" in text


def test_blocked_site_pauses_whole_host(runner, fakes):
    """403 от ЕМИСС: остальные показатели сайта не трогаем, пауза 1 ч → 3 ч; удача снимает паузу."""
    import datetime as dt
    import urllib.error
    from hub.core import hostblock as HB
    from hub.core import notify
    a = runner.cfg.add_job("fedstat", "Зарплата", {"id": 31074, "select": {"0": ["1558883"]}})
    runner.cfg.add_job("fedstat", "Зарплата 2", {"id": 31074, "select": {"0": ["1558900"]}})
    fakes["fedstat"].fail = urllib.error.HTTPError("https://www.fedstat.ru", 403, "Forbidden", {}, None)
    rep = runner.run(force=True)
    assert [o.status for o in rep.outcomes] == ["blocked"]          # второй показатель не запрашивался
    till = HB.until(runner.store, "www.fedstat.ru")
    assert till and dt.timedelta(minutes=55) < till - dt.datetime.now() <= dt.timedelta(minutes=60)
    assert "до " in rep.outcomes[0].message and "попробует сам" in rep.outcomes[0].message
    assert "не пускает хаб" in notify.message_for(rep)[1]
    assert runner.due_jobs() == []                                  # по расписанию — ждём
    rep = runner.run(force=True)                                    # кнопка — ещё одна попытка
    assert HB.until(runner.store, "www.fedstat.ru") - dt.datetime.now() > dt.timedelta(hours=2)
    fakes["fedstat"].fail = None
    runner.run(job_ids=[a["id"]])
    assert HB.until(runner.store, "www.fedstat.ru") is None and HB.active(runner.store) == []
