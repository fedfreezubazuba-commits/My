"""Столбцы «Моих показателей»: дельта к графику, частота, периодичность, модели через выборки."""
import datetime as dt

from hub.core import joblist as JL


def test_words_and_delta():
    assert JL.period_word(["month", "month"]) == "Месяц" and JL.period_word(["month", "year"]) == "Иное"
    assert JL.period_word(["halfyear"]) == "Иное" and JL.period_word([]) == "—"
    assert JL.frequency_word({"schedule": "weekly:Mon,Tue,Wed,Thu,Fri@10:00"}) == "День"
    assert JL.frequency_word({"schedule": "weekly:Mon@10:00"}) == "Неделя"
    assert JL.frequency_word({"schedule": "monthly:15@12:00"}) == "Месяц"
    assert JL.frequency_word({"schedule": "quarterly:wd10"}) == "Квартал"
    assert JL.frequency_word({"schedule": "yearly:03-20"}) == "Год"
    assert JL.frequency_word({"schedule": "every:6h"}) == "День"
    assert JL.frequency_word({"schedule": "auto"}, "month") == "Месяц (авто)"
    assert JL.delta_text(dt.timedelta(hours=5)) == "+5 ч" and JL.delta_text(dt.timedelta(days=-2)) == "−2 дн"
    assert JL.delta_text(dt.timedelta(minutes=20)) == "0" and JL.delta_text(None) == "—"
    assert JL.history_text({"first_period": "2020-01-31", "last_period": "2026-09-30"}) == "2020–2026"


def test_delta_against_own_schedule():
    """Свой график «по будням в 10:00»: данные пришли в 15:30 — «+6 ч» (позже срока); пришли в 08:00 —
    «−2 ч» (раньше ближайшего срока)."""
    job = {"id": "x", "source": "web_form", "schedule": "weekly:Mon,Tue,Wed,Thu,Fri@10:00"}
    when = dt.datetime(2026, 10, 22, 15, 30)                     # четверг
    p = JL.planned_moment(None, job, {}, when)
    assert p == dt.datetime(2026, 10, 22, 10, 0) and JL.delta_text(when - p) == "+6 ч"
    when = dt.datetime(2026, 10, 22, 8, 0)
    assert JL.delta_text(when - JL.planned_moment(None, job, {}, when)) == "−2 ч"


def test_model_links_full_and_partial(runner):
    fs = runner.cfg.add_job("fedstat", "Зарплата", {"id": 31074, "select": {"0": ["1558883", "1558900"]}})
    runner.run(force=True)
    runner.cfg.add_model("М", "x.xlsx")
    runner.cfg.add_model("Н", "y.xlsx")
    runner.cfg.subscribe("Н", job_id=fs["id"])
    region = "Классификатор объектов административно-территориального деления (ОКАТО)"
    runner.cfg.save_selection("М", {"job": fs["id"], "name": "РФ", "filters": {region: ["Российская Федерация"]},
                                    "from": "", "to": ""})
    assert JL.model_links(runner.cfg, runner.store) == {fs["id"]: "М (часть), Н"}


def test_lag_text():
    now = dt.datetime(2026, 10, 25, 12)
    assert JL.lag_text({"last_period": "2026-09-30"}, now) == "25 дн"
    assert JL.lag_text({"last_period": "2026-10-31"}, now) == "−6 дн"
    assert JL.lag_text({}, now) == "—"
