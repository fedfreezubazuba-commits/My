// Таблица «Хаб» плоская: каждое значение — отдельная строка со всеми полями:
// Дата | Период | Показатель | поля ряда (регион, товар…) | Периодичность | Единицы | Источник | Код |
// Обновлено | Значение. Удобно для СУММЕСЛИМН, фильтров и сводных таблиц.
// Файл лежит рядом с посылкой: «<модель> — таблица.csv». Столбцы полей не перечислены вручную:
// новые поля появятся сами.
let
    Путь = Text.Start(Хаб_Посылка, Text.Length(Хаб_Посылка) - 4) & " — таблица.csv",
    Байты = try Binary.Buffer(File.Contents(Путь)) otherwise null,
    Файл = if Байты = null
        then error Error.Record(
            "Хаб: не найден файл с данными",
            "Не найден файл " & Путь & ". Откройте «Хаб данных» и нажмите «Обновить всё сейчас». " &
            "Если рабочая папка хаба переехала, исправьте путь в запросе Хаб_Посылка.",
            Путь)
        else Байты,
    Csv = Csv.Document(Файл, [Delimiter = ";", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    Заголовки = Table.PromoteHeaders(Csv, [PromoteAllScalars = true]),
    ПустыеВNull = Table.ReplaceValue(Заголовки, "", null, Replacer.ReplaceValue, {"Обновлено", "Значение"}),
    Особые = {"Дата", "Обновлено", "Значение"},
    Текстовые = List.Transform(List.Difference(Table.ColumnNames(ПустыеВNull), Особые), each {_, type text}),
    Результат = Table.TransformColumnTypes(
        ПустыеВNull,
        {{"Дата", type date}, {"Обновлено", type date}, {"Значение", type number}} & Текстовые,
        "en-US")
in
    Результат
