// Таблица «Хаб_Ряды»: справочник рядов подписки — код, название, единицы, периодичность,
// последняя дата, последнее и предыдущее значение, источник, дата обновления.
// Файл лежит рядом с посылкой: «<модель> — ряды.csv».
let
    Путь = Text.Start(Хаб_Посылка, Text.Length(Хаб_Посылка) - 4) & " — ряды.csv",
    Байты = try Binary.Buffer(File.Contents(Путь)) otherwise null,
    Файл = if Байты = null
        then error Error.Record(
            "Хаб: не найден справочник рядов",
            "Не найден файл " & Путь & ". Откройте «Хаб данных» и нажмите «Обновить всё сейчас».",
            Путь)
        else Байты,
    Csv = Csv.Document(Файл, [Delimiter = ";", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    Заголовки = Table.PromoteHeaders(Csv, [PromoteAllScalars = true]),
    ПустыеВNull = Table.ReplaceValue(
        Заголовки, "", null, Replacer.ReplaceValue,
        {"Последняя дата", "Последнее значение", "Предыдущее значение", "Обновлено"}),
    Типы = Table.TransformColumnTypes(
        ПустыеВNull,
        {{"Код", type text}, {"Название", type text}, {"Единицы", type text}, {"Периодичность", type text},
         {"Последняя дата", type date}, {"Последнее значение", type number},
         {"Предыдущее значение", type number}, {"Источник", type text}, {"Обновлено", type date},
         {"Правило даты", type text}, {"Порядок", Int64.Type}},
        "en-US"),
    Результат = Table.Sort(Типы, {{"Порядок", Order.Ascending}})
in
    Результат
