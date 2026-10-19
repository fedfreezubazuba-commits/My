"""
Генератор СИНТЕТИЧЕСКИХ образцов ответов ЦБ и ЕМИСС (samples/cbr, samples/fedstat).

Живых ответов в репозитории пока нет (из облака сайты недоступны). Образцы повторяют
формат, который разбирают проверенные клиенты (поля JSON ЦБ, JS-объект filters
страницы ЕМИСС, SDMX-ML 1.0 Generic), но числа выдуманы. Когда у пользователя
появятся живые ответы (`python -m hub samples` → samples/live/), тесты стоит
дополнить ими.

Запуск: python tests/make_synthetic_samples.py
"""
import datetime as dt
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "samples"


def cbr():
    d = ROOT / "cbr"
    d.mkdir(parents=True, exist_ok=True)
    pubs = [
        {"id": 3, "parent_id": -1, "category_name": "Банковский сектор", "NoActive": 1,
         "pub_description": "", "updated_time": "2026-10-01T00:00:00"},
        {"id": 18, "parent_id": 3, "category_name": "Процентные ставки по вкладам (депозитам) физических лиц",
         "NoActive": 0, "pub_description": "", "updated_time": "2026-10-10T00:00:00"},
        {"id": 5, "parent_id": 3, "category_name": "Денежная масса (М2)", "NoActive": 0,
         "pub_description": "", "updated_time": "2026-10-12T00:00:00"},
    ]
    cats = {"category": [
        {"category_id": 18, "category_name": "Процентные ставки по вкладам (депозитам) физических лиц",
         "indicator_id": 37, "indicator_name": "Средневзвешенные процентные ставки по вкладам физических лиц в рублях",
         "indicator_parent": -1, "begin_dt": 2024, "end_dt": 2025, "link": "/statistics/bank_sector/int_rat/"},
        {"category_id": 18, "category_name": "Процентные ставки по вкладам (депозитам) физических лиц",
         "indicator_id": 38, "indicator_name": "Средневзвешенные процентные ставки по вкладам физических лиц в долларах США",
         "indicator_parent": -1, "begin_dt": 2024, "end_dt": 2025, "link": ""},
        {"category_id": 5, "category_name": "Денежная масса (М2)", "indicator_id": 7,
         "indicator_name": "Денежная масса М2 (остаток на 1 число)", "indicator_parent": -1,
         "begin_dt": 2024, "end_dt": 2025, "link": ""},
    ]}
    rows, links = [], []
    rid = 0
    terms = {2: "до 1 года, включая «до востребования»", 3: "свыше 1 года"}
    for ind, base in ((37, 14.0), (38, 1.5)):
        for m1, add in ((2, 0.0), (3, 1.2)):
            links.append({"indicator_id": ind, "measure1_id": m1, "measure2_id": None, "unit_id": 2,
                          "indicator_name": next(c["indicator_name"] for c in cats["category"] if c["indicator_id"] == ind),
                          "measure1_name": terms[m1], "measure2_name": None, "un_name": "% годовых"})
            for i in range(21):   # январь 2024 … сентябрь 2025
                y, m = 2024 + (i // 12), i % 12 + 1
                nxt = dt.date(y + (m == 12), m % 12 + 1, 1)   # дата ЦБ: 1-е число следующего месяца
                rid += 1
                rows.append({"id": rid, "indicator_id": ind, "measure1_id": m1, "measure2_id": None, "unit_id": 2,
                             "obs_val": round(base + add + i * 0.25, 2), "row_id": rid, "dt": "",
                             "periodicity": "month", "date": nxt.isoformat() + "T00:00:00", "digits": 2})
    links.append({"indicator_id": 7, "measure1_id": None, "measure2_id": None, "unit_id": 5,
                  "indicator_name": "Денежная масса М2 (остаток на 1 число)", "measure1_name": None,
                  "measure2_name": None, "un_name": "млрд руб."})
    for i in range(21):
        y, m = 2024 + (i // 12), i % 12 + 1
        nxt = dt.date(y + (m == 12), m % 12 + 1, 1)
        rid += 1
        rows.append({"id": rid, "indicator_id": 7, "measure1_id": None, "measure2_id": None, "unit_id": 5,
                     "obs_val": 100000 + i * 1500.5, "row_id": rid, "dt": "", "periodicity": "month",
                     "date": nxt.isoformat() + "T00:00:00", "digits": 1})
    data = {"RowData": rows, "Links": links, "S_name": "synthetic"}
    datasets = {18: [{"id": 37, "name": "…", "updated_time": "2026-10-10T12:00:00"},
                     {"id": 38, "name": "…", "updated_time": "2026-10-10T12:00:00"}],
                5: [{"id": 7, "name": "…", "updated_time": "2026-10-12T12:00:00"}]}
    (d / "publications.json").write_text(json.dumps(pubs, ensure_ascii=False, indent=1), "utf-8")
    (d / "categoryNew.json").write_text(json.dumps(cats, ensure_ascii=False, indent=1), "utf-8")
    (d / "dataNew_all.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    for k, v in datasets.items():
        (d / f"datasets_{k}.json").write_text(json.dumps(v, ensure_ascii=False, indent=1), "utf-8")
    (d / "README.md").write_text(
        "# Образцы ответов Банка России (синтетические)\n\n"
        "Формат повторяет ответы /publications, /categoryNew, /datasets, /dataNew, которые разбирает\n"
        "`hub/sources/cbr_api.py`. Числа выдуманы. Даты месячных рядов — 1-е число следующего месяца,\n"
        "как у ЦБ. Пересоздать: `python tests/make_synthetic_samples.py`.\n", "utf-8")


MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь",
          "октябрь", "ноябрь", "декабрь"]


def fedstat():
    d = ROOT / "fedstat"
    d.mkdir(parents=True, exist_ok=True)
    month_vals = "".join(f"{1558885 + i}: {{title: '{m}', order: {i}, checked: true}},\n" for i, m in enumerate(MONTHS))
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>ЕМИСС</title></head><body>
<form id="exportForm" action="/indicator/downloadData.do" method="post">
<input type="hidden" name="struts.token.name" value="token" />
<input type="hidden" name="token" value="SYNTHETICTOKEN123" />
</form>
<script type="text/javascript">
var grid = new FGrid({{
    title: 'Среднемесячная номинальная начисленная заработная плата работников по полному кругу организаций',
    unit: 'рубль',
    filters: {{
        0: {{title: 'Классификатор объектов административно-территориального деления (ОКАТО)', all: false,
            values: {{ 1558883: {{title: 'Российская Федерация', order: 0, checked: true}},
                      1558900: {{title: 'Центральный федеральный округ', order: 1, checked: false}} }}}},
        3: {{title: 'Год', all: true,
            values: {{ 2023: {{title: '2023', order: 0, checked: true}},
                      2024: {{title: '2024', order: 1, checked: true}},
                      2025: {{title: '2025', order: 2, checked: true}} }}}},
        33560: {{title: 'Период', all: true,
            values: {{ {month_vals} 1558897: {{title: 'значение показателя за год', order: 12, checked: true}} }}}},
        57956: {{title: 'Единица измерения', all: true, values: {{ 1558884: {{title: 'рубль', order: 0, checked: true}} }}}}
    }},
    left_columns: [0],
    top_columns: [3, 33560],
    groups: [57956]
}});
</script></body></html>"""
    (d / "indicator_31074.html").write_text(html, "utf-8")

    def series(okato, name_mult, periods, year_list):
        obs = []
        for y in year_list:
            for p, v in periods(y):
                obs.append(f"<generic:Obs><generic:Time>{y}</generic:Time>"
                           f"<generic:ObsValue value=\"{str(round(v * name_mult, 1)).replace('.', ',')}\"/></generic:Obs>")
        return obs

    parts = []
    for okato, mult in (("643", 1.0), ("030", 1.25)):
        for idx, m in enumerate(MONTHS):
            obs = []
            for y in (2023, 2024, 2025):
                if y == 2025 and idx > 7:     # 2025 — по август
                    continue
                v = (60000 + (y - 2023) * 9000 + idx * 600) * mult
                obs.append(f"<generic:Obs><generic:Time>{y}</generic:Time>"
                           f"<generic:ObsValue value=\"{str(round(v, 1)).replace('.', ',')}\"/></generic:Obs>")
            parts.append(f"""<generic:Series><generic:SeriesKey><generic:Value concept="s_OKATO" value="{okato}"/>
<generic:Value concept="s_mosh" value="11"/></generic:SeriesKey>
<generic:Attributes><generic:Value concept="EI" value="рубль"/><generic:Value concept="PERIOD" value="{m}"/></generic:Attributes>
{''.join(obs)}</generic:Series>""")
        obs = []
        for y in (2023, 2024):
            obs.append(f"<generic:Obs><generic:Time>{y}</generic:Time>"
                       f"<generic:ObsValue value=\"{str(round((63000 + (y - 2023) * 9000) * mult, 1)).replace('.', ',')}\"/></generic:Obs>")
        parts.append(f"""<generic:Series><generic:SeriesKey><generic:Value concept="s_OKATO" value="{okato}"/>
<generic:Value concept="s_mosh" value="11"/></generic:SeriesKey>
<generic:Attributes><generic:Value concept="EI" value="рубль"/><generic:Value concept="PERIOD" value="значение показателя за год"/></generic:Attributes>
{''.join(obs)}</generic:Series>""")
    sdmx = f"""<?xml version="1.0" encoding="UTF-8"?>
<GenericData xmlns="http://www.SDMX.org/resources/SDMXML/schemas/v1_0/message"
 xmlns:common="http://www.SDMX.org/resources/SDMXML/schemas/v1_0/common"
 xmlns:compact="http://www.SDMX.org/resources/SDMXML/schemas/v1_0/compact"
 xmlns:generic="http://www.SDMX.org/resources/SDMXML/schemas/v1_0/generic"
 xmlns:structure="http://www.SDMX.org/resources/SDMXML/schemas/v1_0/structure">
<Header><ID>none</ID><Test>false</Test><Prepared>2026-10-17T10:00:00</Prepared>
<Sender id="FSGS"><Name xml:lang="ru">Росстат</Name></Sender></Header>
<CodeLists>
<structure:CodeList id="s_OKATO" agencyID="FSGS"><structure:Name xml:lang="ru">Классификатор объектов административно-территориального деления (ОКАТО)</structure:Name>
<structure:Code value="643"><structure:Description xml:lang="ru">Российская Федерация</structure:Description></structure:Code>
<structure:Code value="030"><structure:Description xml:lang="ru">Центральный федеральный округ</structure:Description></structure:Code>
</structure:CodeList>
<structure:CodeList id="s_mosh" agencyID="FSGS"><structure:Name xml:lang="ru">Виды экономической деятельности</structure:Name>
<structure:Code value="11"><structure:Description xml:lang="ru">Всего по обследуемым видам экономической деятельности</structure:Description></structure:Code>
</structure:CodeList>
</CodeLists>
<DataSet>
{chr(10).join(parts)}
</DataSet>
</GenericData>
"""
    (d / "data_31074.sdmx.xml").write_text(sdmx, "utf-8")
    (d / "README.md").write_text(
        "# Образцы ответов ЕМИСС (синтетические)\n\n"
        "`indicator_31074.html` — фрагмент страницы показателя с JS-объектом `filters` и CSRF-токеном в том виде,\n"
        "который разбирает `hub/sources/fedstat_api.py` (`Fedstat.indicator`). `data_31074.sdmx.xml` — выгрузка\n"
        "SDMX-ML 1.0 Generic (атрибуты EI и PERIOD). Числа выдуманы.\n"
        "Пересоздать: `python tests/make_synthetic_samples.py`.\n", "utf-8")


if __name__ == "__main__":
    cbr()
    fedstat()
    print("Готово:", ROOT)
