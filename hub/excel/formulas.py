"""
Готовые формулы к листу «Хаб» модели (раздел 9.3 ТЗ) — в русской или английской локали.
Формулы ссылаются на таблицы внутри самой модели: Хаб и Хаб_Ряды.
"""

RU = {"xlookup": "ПРОСМОТРX", "sumifs": "СУММЕСЛИМН", "eomonth": "КОНМЕСЯЦА", "index": "ИНДЕКС",
      "match": "ПОИСКПОЗ", "iferror": "ЕСЛИОШИБКА", "lookup": "ПРОСМОТР", "sep": ";"}
EN = {"xlookup": "XLOOKUP", "sumifs": "SUMIFS", "eomonth": "EOMONTH", "index": "INDEX",
      "match": "MATCH", "iferror": "IFERROR", "lookup": "LOOKUP", "sep": ","}


def variants(code: str, locale: str = "ru", date_cell: str = "$A10", layout: str = "long") -> list[tuple[str, str]]:
    """[(что делает, формула)] для ряда code. layout — вид таблицы «Хаб» в модели (long — строки, wide — столбцы)."""
    f = RU if locale == "ru" else EN
    s = f["sep"]
    if layout == "long":
        return long_variants(code, f, s, date_cell)
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
        ("Последнее известное значение на дату (для цен с сайтов: если именно на эту дату значения нет)",
         f"={f['lookup']}(2{s} 1/((Хаб[Дата]<={date_cell})*({col}<>\"\")){s} {col})"),
        ("Значение на дату для старых версий Excel (без " + f["xlookup"] + ")",
         f"={f['iferror']}({f['index']}({col}{s} {f['match']}({date_cell}{s} Хаб[Дата]{s} 0)){s} \"\")"),
    ]


def long_variants(code, f, s, date_cell):
    """Плоская таблица «Хаб»: строка на значение, ряд отбирается по столбцу Код."""
    by_code = f"Хаб[Код]{s} \"{code}\""
    return [
        ("Значение на дату из ячейки " + date_cell,
         f"={f['sumifs']}(Хаб[Значение]{s} {by_code}{s} Хаб[Дата]{s} {date_cell})"),
        ("Последнее значение",
         f"={f['xlookup']}(\"{code}\"{s} Хаб_Ряды[Код]{s} Хаб_Ряды[Последнее значение])"),
        ("Дата последнего значения",
         f"={f['xlookup']}(\"{code}\"{s} Хаб_Ряды[Код]{s} Хаб_Ряды[Последняя дата])"),
        ("Сумма за квартал из месячных данных (в " + date_cell + " — конец квартала)",
         f"={f['sumifs']}(Хаб[Значение]{s} {by_code}{s} Хаб[Дата]{s} \">\"&{f['eomonth']}({date_cell}{s}-3){s} "
         f"Хаб[Дата]{s} \"<=\"&{date_cell})"),
        ("Значение на дату, если его может не быть (пусто вместо нуля)",
         f"={f['xlookup']}(1{s} (Хаб[Код]=\"{code}\")*(Хаб[Дата]={date_cell}){s} Хаб[Значение]{s} \"\")"),
    ]


def main_formula(code: str, locale: str = "ru", layout: str = "long") -> str:
    return variants(code, locale, layout=layout)[0][1]


def as_text(code: str, name: str = "", locale: str = "ru") -> str:
    lines = [f"Формулы для ряда {code}" + (f" — {name}" if name else ""), ""]
    for title, formula in variants(code, locale):
        lines += [title + ":", formula, ""]
    lines.append("Таблицы Хаб и Хаб_Ряды находятся на листе «Хаб» вашей модели.")
    return "\n".join(lines)
