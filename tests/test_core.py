import datetime as dt
import xml.etree.ElementTree as ET

import pytest

from hub.core import claude_pack, periods as P, schedule as S, winsched
from hub.core.config import Config, slug
from hub.core.paths import Paths
from hub.core.store import make_code
from hub.excel import formulas, packet


def test_parse_number():
    cases = {"1 979,50 ₽": 1979.5, "$71,778": 71778, "-0,6%": -0.6, "-$134": -134, "12863.5": 12863.5,
             "280'": 280, "+0.41%": 0.41, "1\u00a0250": 1250, "abc": None, "−15": -15}
    for text, val in cases.items():
        assert P.parse_number(text) == val, text


def test_parse_date():
    assert P.parse_date("Week 42 - October 14, 2026") == dt.date(2026, 10, 14)
    assert P.parse_date("19 октября 2026 г.") == dt.date(2026, 10, 19)
    assert P.parse_date("31.01.2025") == dt.date(2025, 1, 31)
    assert P.parse_date("нет даты") is None


def test_labels_and_rank():
    d = dt.date(2025, 3, 31)
    assert [P.label(p, d) for p in (P.MONTH, P.QUARTER, P.HALF, P.YEAR)] == ["2025-03", "2025-Q1", "2025-H1", "2025"]
    assert P.label(P.WEEK, dt.date(2026, 10, 14)) == "2026-W42"
    assert sorted(["2025", "2025-Q1", "2025-03", "2025-03-31"], key=P.rank) == ["2025-03-31", "2025-03", "2025-Q1", "2025"]


def test_schedule_parse_and_slots():
    assert S.parse("weekly:Wed") == ("weekly", (2,), None)
    assert S.parse("weekly:ср") == ("weekly", (2,), None)
    assert S.parse("weekly:Mon,ср@10:30") == ("weekly", (0, 2), (10, 30))
    assert S.parse("monthly:15") == ("monthly", 15, None)
    assert S.parse("daily@9:05") == ("daily", None, (9, 5))
    assert S.parse("workday:5@10:00") == ("workday", 5, (10, 0))
    assert S.parse("every:6h") == ("every", 6, None)
    for bad in ("иногда", "weekly:", "daily@25:00", "auto@10:00", "every:6h@10:00"):
        with pytest.raises(ValueError):
            S.parse(bad)
    assert S.make("weekly", [2, 4], (10, 30)) == "weekly:Wed,Fri@10:30"
    assert S.human("weekly:Wed,Fri@10:30") == "по дням: ср, пт в 10:30"
    assert S.human("weekly:Wed@10:30") == "раз в неделю, в среду в 10:30"
    now = dt.datetime(2026, 10, 19, 10, 0)    # понедельник
    assert S.last_slot("weekly", (2,), now) == dt.datetime(2026, 10, 14, 6)
    assert S.last_slot("weekly", (0, 2), now, (10, 30)) == dt.datetime(2026, 10, 14, 10, 30)
    assert S.last_slot("weekly", (0, 2), now, (9, 0)) == dt.datetime(2026, 10, 19, 9, 0)
    assert S.last_slot("monthly", 31, dt.datetime(2026, 3, 1, 5)) == dt.datetime(2026, 2, 28, 6)
    # 1-й рабочий день января 2026 — 12-е (каникулы до 11-го)
    assert S.last_slot("workday", 1, dt.datetime(2026, 1, 20), (10, 0)) == dt.datetime(2026, 1, 12, 10, 0)
    assert S.next_slot("weekly:Wed@10:30", now) == dt.datetime(2026, 10, 21, 10, 30)
    assert S.next_slot("auto", now) is None


def test_workdays():
    from hub.core import workdays as WD
    assert not WD.is_workday(dt.date(2026, 3, 9))        # 8 марта в воскресенье → 9-е выходной
    assert not WD.is_workday(dt.date(2026, 11, 4))
    assert WD.is_workday(dt.date(2025, 11, 1))           # рабочая суббота
    assert WD.nth_workday(2026, 5, 1) == dt.date(2026, 5, 4)
    assert WD.add_workdays(dt.date(2026, 9, 30), 25) == dt.date(2026, 11, 5)
    assert not WD.is_workday(dt.date(2027, 5, 10))       # общее правило: 9 мая 2027 — воскресенье


def test_workdays_user_file(tmp_path):
    from hub.core import workdays as WD
    f = tmp_path / "Праздники.txt"
    f.write_text("2027-11-06 рабочий\n2027-11-05 выходной\nпросто текст\n", encoding="utf-8")
    assert WD.is_workday(dt.date(2027, 11, 6), f)
    assert not WD.is_workday(dt.date(2027, 11, 5), f)


def test_release_text_parsing():
    today = dt.date(2026, 10, 19)
    text = """Календарь публикаций
    Индекс потребительских цен   21.10.2026 19:00
    Оборот розничной торговли    2026-11-18
    Инфляция: 25 ноября; 23 декабря в 19:00
    Итоги 2025 года — 20 марта
    повтор 21.10.2026"""
    got = S.parse_release_text(text, today)
    assert got == ["2026-10-21T19:00", "2026-11-18", "2026-11-25", "2026-12-23T19:00", "2027-03-20"]
    assert S.fmt_moment("2026-10-21T19:00") == "21.10.2026 19:00"
    assert S.parse_release_text(S.fmt_moment("2026-10-21T19:00"), today) == ["2026-10-21T19:00"]


def test_release_dates_trigger_checks():
    job = {"schedule": "auto", "enabled": True, "release_dates": ["2026-10-21T19:00"]}
    st = {"last_attempt": "2026-10-21T18:00:00", "expected_next": "2026-12-01T06:00:00"}
    at = dt.datetime(2026, 10, 21, 19, 5)
    assert not S.is_due(job, st, at - dt.timedelta(minutes=10), periodicity=P.MONTH)
    assert S.is_due(job, st, at, periodicity=P.MONTH)
    # проверили в 19:05, данных нет → снова через 2 часа, но не после 23:00
    st = {**st, "last_attempt": "2026-10-21T19:05:00"}
    assert not S.is_due(job, st, dt.datetime(2026, 10, 21, 20, 0), periodicity=P.MONTH)
    assert S.is_due(job, st, dt.datetime(2026, 10, 21, 21, 10), periodicity=P.MONTH)
    st2 = {**st, "last_change": "2026-10-21T19:05:00"}
    assert not S.is_due(job, st2, dt.datetime(2026, 10, 21, 21, 10), periodicity=P.MONTH)
    # дата, которую сообщил источник, работает так же
    job2 = {"schedule": "weekly:Mon", "enabled": True}
    st3 = {"last_attempt": "2026-10-19T07:00:00", "last_change": "2026-10-19T07:00:00", "release_next": "2026-10-20"}
    assert not S.is_due(job2, st3, dt.datetime(2026, 10, 20, 8, 0))
    assert S.is_due(job2, st3, dt.datetime(2026, 10, 20, 9, 0))


def test_is_due():
    now = dt.datetime(2026, 10, 19, 10, 0)
    job = {"schedule": "daily", "enabled": True}
    assert S.is_due(job, {}, now)
    assert not S.is_due(job, {"last_attempt": "2026-10-19T07:00:00", "last_change": "2026-10-19T07:00:00"}, now)
    assert S.is_due(job, {"last_attempt": "2026-10-18T22:00:00"}, now)
    assert not S.is_due({**job, "enabled": False}, {}, now)
    # сбой: повтор через 15 мин, потом реже
    st = {"last_attempt": "2026-10-19T09:50:00", "fail_count": 1}
    assert not S.is_due(job, st, now) and S.is_due(job, st, now + dt.timedelta(minutes=6))
    assert S.backoff_minutes(10) == 360
    # auto: до ожидаемой даты — редко, после — ежедневно
    auto = {"schedule": "auto", "enabled": True}
    st = {"last_attempt": "2026-10-17T10:00:00", "expected_next": "2026-11-05T06:00:00"}
    assert not S.is_due(auto, st, now, periodicity=P.MONTH)
    assert S.is_due(auto, st, now + dt.timedelta(days=6), periodicity=P.MONTH)
    st = {"last_attempt": "2026-10-18T10:00:00", "expected_next": "2026-10-10T06:00:00"}
    assert S.is_due(auto, st, now, periodicity=P.MONTH)
    assert S.is_due(auto, {"last_attempt": "2026-10-18T10:00:00"}, now, snapshot=True)


def test_delayed():
    exp = dt.datetime(2026, 10, 1, 6)
    assert not S.is_delayed(exp, P.MONTH, dt.datetime(2026, 10, 5))
    assert S.is_delayed(exp, P.MONTH, dt.datetime(2026, 10, 20))


def test_codes():
    assert make_code("CBR_18_37_m1_2", "x") == "CBR_18_37_m1_2"
    long = make_code("FEDSTAT_31074_" + "_".join(["1234567"] * 8), "ident")
    assert len(long) <= 40 and long.startswith("FEDSTAT_31074_")
    assert make_code("12 лосось!", "y").startswith("S_12")
    assert slug("Лосось Мурманский, руб/кг") == "losos_murmanskij_rub_kg"


def test_formulas():
    ru = dict(formulas.variants("CBR_18_37_m1_2", "ru", layout="wide"))
    assert "=ПРОСМОТРX($A10; Хаб[Дата]; Хаб[CBR_18_37_m1_2])" in ru.values()
    assert '=ПРОСМОТРX("CBR_18_37_m1_2"; Хаб_Ряды[Код]; Хаб_Ряды[Последнее значение])' in ru.values()
    en = formulas.variants("X", "en", layout="wide")
    assert en[0][1] == "=XLOOKUP($A10, Хаб[Дата], Хаб[X])"
    assert any("SUMIFS" in f for _, f in en) and any("INDEX" in f for _, f in en)
    flat = formulas.variants("X", "ru")              # плоская таблица — по умолчанию
    assert flat[0][1] == '=СУММЕСЛИМН(Хаб[Значение]; Хаб[Код]; "X"; Хаб[Дата]; $A10)'
    assert all("Хаб[X]" not in f for _, f in flat)


def test_packet_number_format():
    assert packet.num(15.8) == "15.8"
    assert packet.num(1e-5) == "0.00001"
    assert packet.num(1250000.0) == "1250000"
    assert packet.num(-0.0) == "0"
    assert packet.safe_name('Сбер: "план"/2026') == "Сбер_ _план_2026"


def test_task_xml_is_valid():
    xml = winsched.task_xml(r"C:\Хаб\.venv\Scripts\pythonw.exe", "-m hub update --scheduled", r"C:\Хаб",
                            user="Аналитик", start=dt.datetime(2026, 10, 19, 10))
    root = ET.fromstring(xml.replace('encoding="UTF-16"', ""))
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    assert root.find("t:Settings/t:StartWhenAvailable", ns).text == "true"
    assert root.find("t:Triggers/t:TimeTrigger/t:Repetition/t:Interval", ns).text == "PT15M"
    assert root.find("t:Triggers/t:LogonTrigger", ns) is not None
    assert root.find("t:Actions/t:Exec/t:Arguments", ns).text == "-m hub update --scheduled"


def test_scrub_secrets():
    text = ('<input name="token" value="ABC123"/> Cookie: sid=1; x=2\nAuthorization: Bearer xyz\n'
            'password=qwerty&user=ivan "csrf": "zzz"')
    out = claude_pack.scrub(text)
    for secret in ("ABC123", "sid=1", "xyz", "qwerty", "zzz"):
        assert secret not in out
    assert claude_pack.scrub_obj({"url": "x", "session_cookie": "s"}) == {"url": "x", "session_cookie": "***"}


def test_config_trash_roundtrip(workdir):
    cfg = Config(Paths(workdir).ensure())
    j = cfg.add_job("cbr", "Ставки", {"category": 18})
    j2 = cfg.add_job("cbr", "Ставки", {"category": 19})
    assert j["id"] == "cbr-stavki" and j2["id"] == "cbr-stavki-2"
    cfg.delete_job(j["id"])
    assert [x["id"] for x in cfg.jobs()] == [j2["id"]]
    assert cfg.purge_trash(30, now=dt.datetime.now() + dt.timedelta(days=10)) == []
    cfg.restore_job(j["id"])
    assert {x["id"] for x in cfg.jobs()} == {j["id"], j2["id"]}
    cfg.delete_job(j["id"])
    assert cfg.purge_trash(30, now=dt.datetime.now() + dt.timedelta(days=31)) == [j["id"]]
    # jobs.yaml читается человеком: русские буквы без \\u-экранирования
    assert "Ставки" in cfg.paths.jobs.read_text("utf-8")


def test_claude_package(runner, fakes):
    job = runner.cfg.add_job("cbr", "Ставки", {"category": 18, "indicators": [37]})
    fakes["cbr"].fail = ValueError("Неожиданный ответ")
    runner.run(force=True)
    text = claude_pack.job_package(runner.cfg, runner.store, job["id"])
    assert "Ставки" in text and "Неожиданный ответ" in text and '"category": 18' in text
    assert "Трассировка" in text


def test_report_html(runner, fakes):
    runner.cfg.add_job("cbr", "Ставки", {"category": 18, "indicators": [37]})
    rep = runner.run(force=True)
    html = rep.path.read_text("utf-8")
    assert "Что обновилось" in html and "Ставки" in html and "Здоровье системы" in html


def test_startup_error_is_saved_not_lost(workdir, tmp_path, monkeypatch, capsys):
    """Окно запускается через pythonw без консоли: ошибка запуска должна попасть в файл."""
    import sys
    from hub import __main__ as M
    from hub.core import crash
    monkeypatch.setattr(crash, "PROJECT", tmp_path)
    fake = type(sys)("hub.gui.app")
    fake.main = lambda: 1 / 0
    monkeypatch.setitem(sys.modules, "hub.gui.app", fake)
    assert M.main([]) == 1
    text = (tmp_path / crash.FILE_NAME).read_text(encoding="utf-8-sig")
    assert "ZeroDivisionError" in text and "Версия хаба" in text and "пришлите Claude" in text
    assert "ZeroDivisionError" in capsys.readouterr().err
    assert (workdir / "logs" / "ошибки запуска.log").exists()


def test_startup_watch_reports_hang(tmp_path, monkeypatch, capsys):
    import threading
    import time
    from hub.core import crash
    monkeypatch.setattr(crash, "PROJECT", tmp_path)
    stop = threading.Event()
    w = crash.StartupWatch(seconds=0.3)
    w.stage = "открываю базу данных"
    stop.wait(1.0)                       # главный поток «завис»
    text = (tmp_path / crash.FILE_NAME).read_text(encoding="utf-8-sig")
    assert "завис" in text and "открываю базу данных" in text and "(главный)" in text
    assert "test_startup_watch_reports_hang" in text      # видно место в коде
    w2 = crash.StartupWatch(seconds=0.3)
    w2.ready.set()
    (tmp_path / crash.FILE_NAME).unlink()
    time.sleep(0.5)
    assert not (tmp_path / crash.FILE_NAME).exists()


def test_locked_database_gives_clear_message(tmp_path):
    import sqlite3
    from hub.core import store as ST
    from hub.core.errors import HubError
    db = tmp_path / "hub.sqlite"
    ST.Store(db).close()
    c = sqlite3.connect(db)
    c.execute("ALTER TABLE job_state DROP COLUMN release_note")    # как база прошлой версии
    c.commit()
    c.isolation_level = None
    c.execute("BEGIN IMMEDIATE")                                    # другая копия программы пишет в базу
    old = ST.sqlite3.connect
    ST.sqlite3.connect = lambda *a, **k: old(*a, **{**k, "timeout": 0.2})
    try:
        with pytest.raises(HubError, match="занята другой копией"):
            ST.Store(db)
    finally:
        ST.sqlite3.connect = old
        c.execute("ROLLBACK")


def test_check_problems_are_grouped_and_units_normalized():
    from hub.core.checks import summary, unit_key
    assert unit_key("Тонна;^метрическая тонна (1000 кг)") == unit_key("Тонна; метрическая тонна (1000 кг)")
    assert unit_key("руб.") != unit_key("тыс. руб.")
    assert unit_key("-") == unit_key("—") == ""
    many = summary("Сменились единицы", [f"«ряд {i}»" for i in range(5000)])
    assert "5000 рядов" in many and len(many) < 300


def test_scheduler_next_run_parsing():
    ru = "Папка: \\\nИмя узла: PC\nИмя задачи: \\Хаб данных — обновление\nВремя следующего запуска: 20.10.2026 10:45:00\n"
    assert winsched.parse_next_run(ru) == "20.10.2026 10:45"
    en = "TaskName: \\Хаб данных — обновление\nNext Run Time: 10/20/2026 1:45:00 PM\nStatus: Ready"
    assert winsched.parse_next_run(en) == "20.10.2026 13:45"
    assert winsched.parse_next_run("Next Run Time: N/A") is None


def test_status_explain_plain_words():
    from hub.core import status as ST
    job = {"id": "j", "enabled": True}
    t = ST.explain(job, {"status": "delayed", "expected_next": "2026-10-05T06:00:00"})
    assert "опаздывает" in t and "05.10.2026" in t and "не ошибка" in t
    t = ST.explain(job, {"status": "quarantine", "message": "Скачок 80%"})
    assert "отложены" in t and "Скачок 80%" in t and "Принять" in t
    t = ST.explain(job, {"status": "updated", "last_value": 101.3, "last_unit": "процент", "last_label": "2026-Q2",
                         "release_next": "2026-11-20", "release_note": "Срок: 20-го"})
    assert "101,3 процент за 2026-Q2" in t and "20.11.2026" in t
    assert "паузе" in ST.explain({"enabled": False}, {})


def test_schedule_rules_quarter_year_last_and_several():
    rules = S.parse_rules("weekly:Mon@10:00;quarterly:wd10@10:00;yearly:03-20@19:00;monthly:last;workday:last@18:00")
    assert [r[0] for r in rules] == ["weekly", "quarterly", "yearly", "monthly", "workday"]
    assert S.join_rules(rules) == "weekly:Mon@10:00;quarterly:wd10@10:00;yearly:03-20@19:00;monthly:last;workday:last@18:00"
    assert S.human("quarterly:wd10@10:00") == "раз в квартал, 10-й рабочий день после окончания квартала в 10:00"
    assert S.human("yearly:03-20@19:00") == "раз в год, 20 марта в 19:00"
    assert S.human("weekly:Mon@10:00;monthly:15") == "раз в неделю, в понедельник в 10:00; раз в месяц, 15-го"
    with pytest.raises(ValueError):
        S.parse_rules("auto;daily")
    now = dt.datetime(2026, 10, 20, 12, 0)
    # 10-й рабочий день после 30.09.2026 — 14.10.2026
    assert S.last_slot("quarterly", ("wd", 10), now, (10, 0)) == dt.datetime(2026, 10, 14, 10, 0)
    assert S.last_slot("monthly", "last", now) == dt.datetime(2026, 9, 30, 6, 0)
    assert S.last_slot("workday", "last", dt.datetime(2026, 6, 1), (18, 0)) == dt.datetime(2026, 5, 29, 18, 0)
    assert S.last_slot("yearly", (3, 20), now, (19, 0)) == dt.datetime(2026, 3, 20, 19, 0)
    nxt = S.next_slot("weekly:Mon@10:00;quarterly:wd10@10:00", now, count=3)
    assert nxt == [dt.datetime(2026, 10, 26, 10), dt.datetime(2026, 11, 2, 10), dt.datetime(2026, 11, 9, 10)]
    job = {"schedule": "weekly:Fri@10:00;every:48h", "enabled": True}
    st = {"last_attempt": "2026-10-19T09:00:00", "last_change": "2026-10-19T09:00:00"}
    assert not S.is_due(job, st, now)
    assert S.is_due(job, st, dt.datetime(2026, 10, 21, 9, 30))      # прошло 48 ч
    assert S.is_due(job, {"last_attempt": "2026-10-22T09:00:00"}, dt.datetime(2026, 10, 23, 10, 1))


def test_schedule_time_is_moscow(monkeypatch):
    """Компьютер в Екатеринбурге (UTC+5): «в 10:00 по Москве» — это 12:00 по местному времени."""
    monkeypatch.setattr(S, "_local_offset", lambda moment: dt.timedelta(hours=5))
    assert S.next_slot("daily@10:00", dt.datetime(2026, 10, 20, 11, 0)) == dt.datetime(2026, 10, 20, 12, 0)
    job = {"schedule": "daily@10:00", "enabled": True}
    st = {"last_attempt": "2026-10-20T08:00:00", "last_change": "2026-10-20T08:00:00"}
    assert not S.is_due(job, st, dt.datetime(2026, 10, 20, 11, 59))
    assert S.is_due(job, st, dt.datetime(2026, 10, 20, 12, 0))
    assert S.release_moments({"release_dates": ["2026-10-21T19:00"]}) == [dt.datetime(2026, 10, 21, 21, 0)]


def test_upto_text_and_update_line():
    from hub.core import overview as O, periods as P
    assert P.upto_text("2026-08") == "по август 2026"
    assert P.upto_text("2026-W42") == "по неделю с 12.10.2026"
    assert P.upto_text("2026-Q3") == "по III кв. 2026" and P.upto_text("2026") == "по 2026 год"
    assert P.upto_text("2026-10-21") == "по 21.10.2026"
    u = {"upto": "2026-08", "new": 16000, "revisions": 0, "when": "2026-10-21T14:05:33"}
    assert O.update_text(u) == "по август 2026, +16 000 записей (21.10.26 в 14:05)"
    assert O.update_text({**u, "new": 1, "revisions": 3}) == "по август 2026, +1 запись, пересмотрено 3 (21.10.26 в 14:05)"
    # запуски до 0.6: «по какой период» — самый поздний из сохранённых новых значений
    assert O._upto('{"new": [["A", "2026-07", 1], ["A", "2026-09", 2], ["B", "2025-Q4", 3]]}') == "2026-09"
    assert O._later("2026-W40", "2026-09") == "2026-W40"
    assert O.source_group("web_table") == "sites" and O.source_group("folder_inbox") == "other"


def test_missed_slot_and_retries_until_new_data():
    """Будни в 16:00 по Москве. Компьютер был выключен в 16:00 — проверка при первой возможности
    (включили в 19:40). Новых данных нет — повтор каждый час, ночью (23–7) не дёргаем. Данные пришли —
    ждём следующий срок. Раз в месяц — повтор раз в сутки, раз в неделю — раз в 3 часа."""
    job = {"schedule": "weekly:Mon,Tue,Wed,Thu,Fri@16:00", "enabled": True}
    st = {"last_attempt": "2026-10-21T16:02:00", "last_change": "2026-10-21T16:02:00"}
    assert not S.is_due(job, st, dt.datetime(2026, 10, 22, 15, 59))
    assert S.is_due(job, st, dt.datetime(2026, 10, 22, 19, 40))          # срок пропущен — сразу
    st = {"last_attempt": "2026-10-22T19:40:00", "last_change": "2026-10-21T16:02:00"}   # данных нет
    assert not S.is_due(job, st, dt.datetime(2026, 10, 22, 20, 30))
    assert S.is_due(job, st, dt.datetime(2026, 10, 22, 20, 41))          # через час
    st = {"last_attempt": "2026-10-22T22:50:00", "last_change": "2026-10-21T16:02:00"}
    assert not S.is_due(job, st, dt.datetime(2026, 10, 23, 3, 0))        # ночь
    assert S.is_due(job, st, dt.datetime(2026, 10, 23, 7, 5))
    assert S.next_check(job, st, dt.datetime(2026, 10, 23, 3, 0)) == dt.datetime(2026, 10, 23, 7, 0)
    st = {"last_attempt": "2026-10-22T20:41:00", "last_change": "2026-10-22T20:41:00"}   # данные пришли
    assert not S.is_due(job, st, dt.datetime(2026, 10, 23, 15, 0))
    assert S.next_check(job, st, dt.datetime(2026, 10, 23, 15, 0)) == dt.datetime(2026, 10, 23, 16, 0)
    monthly = {"schedule": "monthly:15@10:00", "enabled": True}
    st = {"last_attempt": "2026-10-15T10:05:00", "last_change": "2026-09-15T10:05:00"}
    assert not S.is_due(monthly, st, dt.datetime(2026, 10, 16, 9, 0))
    assert S.is_due(monthly, st, dt.datetime(2026, 10, 16, 10, 6))      # через сутки
    weekly = {"schedule": "weekly:Wed@10:00", "enabled": True}
    st = {"last_attempt": "2026-10-21T10:05:00", "last_change": "2026-10-14T10:05:00"}
    assert not S.is_due(weekly, st, dt.datetime(2026, 10, 21, 12, 0))
    assert S.is_due(weekly, st, dt.datetime(2026, 10, 21, 13, 6))       # через 3 часа
    assert S.retry_interval(S.parse_rules("daily@09:00")) == dt.timedelta(hours=1)


def test_check_text_and_scheduler_watchdog(tmp_path):
    job = {"schedule": "weekly:Mon,Tue,Wed,Thu,Fri@16:00", "enabled": True}
    st = {"last_attempt": "2026-10-22T19:40:00", "last_success": "2026-10-22T19:40:00",
          "last_change": "2026-10-21T16:02:00"}
    text = S.check_text(job, st, dt.datetime(2026, 10, 22, 20, 0))
    assert "после срока 22.10 16:00 ещё нет" in text and "каждый час" in text
    assert "Следующая проверка: 22.10.2026 20:40" in text
    from hub.core import overview as O
    from hub.core.store import Store
    store = Store(tmp_path / "hub.sqlite")
    start = dt.datetime(2026, 10, 22, 9, 0)
    assert O.scheduler_stale(store, start, now=start + dt.timedelta(minutes=10)) is None    # окно только открыли
    assert "ни разу" in O.scheduler_stale(store, start, now=start + dt.timedelta(hours=1))
    store.meta_set("scheduler_heartbeat", (start + dt.timedelta(minutes=50)).isoformat())
    assert O.scheduler_stale(store, start, now=start + dt.timedelta(hours=1)) is None
    assert "с 22.10.2026 09:50" in O.scheduler_stale(store, start, now=start + dt.timedelta(hours=3))


def test_task_has_daily_triggers_for_schedule_times_and_self_heals(monkeypatch, tmp_path):
    """Времена из графиков («по будням в 19:05») попадают в задачу планировщика отдельными ежедневными
    запусками. Если путь к программе в задаче устарел (папку перенесли → 0x80070002) или поменялись
    графики — sync() пересоздаёт задачу."""
    from hub.core.config import Config
    from hub.core.paths import Paths
    cfg = Config(Paths(tmp_path).ensure())
    cfg.add_job("cbr", "Ставки", {"category": 18}, schedule="weekly:Mon,Tue,Wed,Thu,Fri@19:05")
    cfg.add_job("cbr", "Ставки 2", {"category": 18}, schedule="monthly:15@10:00;every:6h")
    cfg.add_job("cbr", "Авто", {"category": 18})
    times = winsched.rule_times(cfg)
    assert times == ["10:00", "19:05"]
    xml = winsched.task_xml(r"C:\Хаб\.venv\Scripts\pythonw.exe", winsched.ARGS, r"C:\Хаб", user="Аналитик",
                            start=dt.datetime(2026, 10, 23, 12), times=times)
    root = ET.fromstring(xml.replace('encoding="UTF-16"', ""))
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    starts = [e.text for e in root.findall("t:Triggers/t:CalendarTrigger/t:StartBoundary", ns)]
    assert starts == ["2026-10-23T10:00:30", "2026-10-23T19:05:30"]
    assert winsched.problems(xml, r"C:\Хаб\.venv\Scripts\pythonw.exe", r"C:\Хаб", times) == []
    assert winsched.problems(xml, r"D:\Новая папка\Хаб\.venv\Scripts\pythonw.exe", r"D:\Новая папка\Хаб", times) \
        == ["путь к программе", "папка программы"]
    assert winsched.problems(xml, r"C:\Хаб\.venv\Scripts\pythonw.exe", r"C:\Хаб", ["19:05"]) == ["время из графиков"]
    # sync: задача есть, но со старым путём → пересоздаётся
    calls = []

    class R:
        def __init__(self, code, data=b""):
            self.returncode, self.stdout, self.stderr = code, data, b""

    def fake_run(args, **kw):
        calls.append(args[:2])
        if args[1] == "/Query":
            return R(0, xml.encode("cp866", "replace"))
        return R(0)
    monkeypatch.setattr(winsched, "IS_WINDOWS", True)
    monkeypatch.setattr(winsched.subprocess, "run", fake_run)
    exe = tmp_path / "pythonw.exe"
    exe.write_text("")
    monkeypatch.setattr(winsched, "pythonw", lambda: exe)
    fixed, what = winsched.sync(cfg)
    assert fixed and "путь к программе" in what and ["schtasks", "/Create"] in calls
    # код результата из «schtasks /Query /V /FO CSV»
    csv_text = ('"Имя узла","Имя задачи","Время следующего запуска","Состояние","Режим входа","Время прошлого запуска",'
                '"Последний результат"\n"PC","\\Хаб данных — обновление","23.10.2026 12:15:00","Готово","Интерактивно",'
                '"22.10.2026 21:15:01","-2147024894"')
    nxt, code = winsched.parse_status_csv(csv_text)
    assert nxt == "23.10.2026 12:15" and code == 0x80070002
    assert "не находит файл программы" in winsched.result_text(code)


def test_dropped_series_cbr_by_ids():
    from hub.core import impact

    class Store:
        def series_of_job(self, _id):
            return [{"code": c, "ident": ident, "retired": None, "dims": "[]", "name": c}
                    for c, ident in (("A", "37|2|None|%|month"), ("B", "37|3|None|%|month"),
                                     ("C", "38|2|None|%|month"))]
    job = {"id": "j", "source": "cbr", "params": {"indicators": [37, 38], "m1": [2, 3]}}
    got = impact.dropped_series(Store(), job, {"indicators": [37], "m1": [2]})
    assert [r["code"] for r in got] == ["B", "C"]
    assert impact.dropped_series(Store(), job, {"indicators": [37, 38], "m1": []}) == []
