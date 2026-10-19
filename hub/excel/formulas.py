"""
Готовые формулы к листу «Хаб» модели (раздел 9.3 ТЗ) — в русской или английской локали.
Формулы ссылаются на таблицы внутри самой модели: Хаб и Хаб_Ряды.
"""

RU = {"xlookup": "ПРОСМОТРX", "sumifs": "СУММЕСЛИМН", "eomonth": "КОНМЕСЯЦА", "index": "ИНДЕКС",
      "match": "ПОИСКПОЗ", "iferror": "ЕСЛИОШИБКА", "sep": ";"}
EN = {"xlookup": "XLOOKUP", "sumifs": "SUMIFS", "eomonth": "EOMONTH", "index": "INDEX",
      "match": "MATCH", "iferror": "IFERROR", "sep": ","}


def variants(code: str, locale: str = "ru", date_cell: str = "$A10") -> list[tuple[str, str]]:
    """[(что делает, формула)] для ряда code."""
    f = RU if locale == "ru" else EN
    s = f["sep"]
    col = f"Хаб[{code}]"
    return [
        ("Значение на дату из ячейки " + date_cell,
         f"={f['xlookup']}({date_cell}{s} Хаб[Дата]{s} {col})"),
        ("Последнее значение",
         f"={f['xlookup']}(\"{code}\"{s} Хаб_Ряды[Код]{s} Хаб_Ряды[Последнее значение])"),
        ("Дата последнего значения",
         f"={f['xlookup']}(\"{code}\"{s} Хаб_Ряды[Код]{s} Хаб_Ряды[Последняя дата])"),
        ("Сумма за квартал из месячных данных (в " + date_cell + " — конец квартала)",
         f"={f['sumifs']}({col}{s} Хаб[Дата]{s} \">\"&{f['eomonth']}({date_cell}{s}-3){s} "
         f"Хаб[Дата]{s} \"<=\"&{date_cell})"),
        ("Значение на дату для старых версий Excel (без " + f["xlookup"] + ")",
         f"={f['iferror']}({f['index']}({col}{s} {f['match']}({date_cell}{s} Хаб[Дата]{s} 0)){s} \"\")"),
    ]


def main_formula(code: str, locale: str = "ru") -> str:
    return variants(code, locale)[0][1]


def as_text(code: str, name: str = "", locale: str = "ru") -> str:
    lines = [f"Формулы для ряда {code}" + (f" — {name}" if name else ""), ""]
    for title, formula in variants(code, locale):
        lines += [title + ":", formula, ""]
    lines.append("Таблицы Хаб и Хаб_Ряды находятся на листе «Хаб» вашей модели.")
    return "\n".join(lines)
