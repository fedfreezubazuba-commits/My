// Ячейки листа из файла — для формул: по строке на каждое число листа.
// Обновлено | Файл от | Лист в файле | Строка | Подпись | Путь (подпись с вышестоящими строками) | Столбец |
// Буква | Заголовок (шапка столбца) | Значение | Таблица (название над таблицей).
// Последнее известное значение: СУММЕСЛИМН(…[Значение]; …[Путь]; "…"; …[Заголовок]; "…"; …[Обновлено];
// МАКСЕСЛИ(…[Обновлено]; …[Путь]; "…"; …[Заголовок]; "…")). Файл лежит рядом с посылкой: «<модель>{{SUFFIX}}».
let
    Путь = Text.Start(Хаб_Посылка, Text.Length(Хаб_Посылка) - 4) & "{{SUFFIX}}",
    Байты = try Binary.Buffer(File.Contents(Путь)) otherwise null,
    Файл = if Байты = null
        then error Error.Record(
            "Хаб: не найдены ячейки листа из файла",
            "Не найден файл " & Путь & ". Откройте «Хаб данных» и нажмите «Обновить всё сейчас».",
            Путь)
        else Байты,
    Csv = Csv.Document(Файл, [Delimiter = ";", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    Заголовки = Table.PromoteHeaders(Csv, [PromoteAllScalars = true]),
    ПустыеВNull = Table.ReplaceValue(Заголовки, "", null, Replacer.ReplaceValue, {"Файл от", "Значение"}),
    Типы = Table.TransformColumnTypes(
        ПустыеВNull,
        {{"Обновлено", type datetime}, {"Файл от", type date}, {"Лист в файле", type text}, {"Строка", Int64.Type},
         {"Подпись", type text}, {"Путь", type text}, {"Столбец", Int64.Type}, {"Буква", type text},
         {"Заголовок", type text}, {"Значение", type number}, {"Таблица", type text}},
        "en-US")
in
    Типы
