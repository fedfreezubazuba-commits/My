# Образцы ответов ЕМИСС (синтетические)

`indicator_31074.html` — фрагмент страницы показателя с JS-объектом `filters` и CSRF-токеном в том виде,
который разбирает `hub/sources/fedstat_api.py` (`Fedstat.indicator`). `data_31074.sdmx.xml` — выгрузка
SDMX-ML 1.0 Generic (атрибуты EI и PERIOD). Числа выдуманы.
Пересоздать: `python tests/make_synthetic_samples.py`.
