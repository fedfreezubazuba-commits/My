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
    hub = dict(mcode.queries("x.csv"))["Хаб"]
    assert "Table.Pivot(" in hub and "List.Distinct(" in hub and '"en-US"' in hub
    assert "Хаб_Посылка" in hub and "error Error.Record" in hub


def test_m_syntax_checker_catches_errors():
    assert mcode.check_syntax("let a = (1 in a")
    assert mcode.check_syntax('let a = "x in a')
    assert mcode.check_syntax("let a = 1, in a")


def test_lock_prevents_parallel_runs(runner):
    from hub.runner import Busy, Lock
    with Lock(runner.paths.lock):
        with pytest.raises(Busy):
            runner.run(force=True)
