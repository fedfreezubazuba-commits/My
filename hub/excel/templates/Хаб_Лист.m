// Лист из файла как есть («Листы файла в модель»): «Строка» — номер строки в файле, дальше столбцы A, B, C…
// как в файле. В режиме «копить обновления» впереди ещё «Обновлено» и «Файл от» — обновления идут одно под другим.
// Файл лежит рядом с посылкой: «<модель>{{SUFFIX}}».
let
    Путь = Text.Start(Хаб_Посылка, Text.Length(Хаб_Посылка) - 4) & "{{SUFFIX}}",
    Байты = try Binary.Buffer(File.Contents(Путь)) otherwise null,
    Файл = if Байты = null
        then error Error.Record(
            "Хаб: не найден лист из файла",
            "Не найден файл " & Путь & ". Откройте «Хаб данных» и нажмите «Обновить всё сейчас».",
            Путь)
        else Байты,
    Csv = Csv.Document(Файл, [Delimiter = ";", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    Заголовки = Table.PromoteHeaders(Csv, [PromoteAllScalars = true]),
    Число = (x) => if x = null or x = "" then null
        else if Text.Length(Text.Select(x, {"0".."9", ".", "-", "+", "E", "e"})) = Text.Length(x)
            then (try Number.FromText(x, "en-US") otherwise x)
        else x,
    Столбцы = List.Difference(Table.ColumnNames(Заголовки), {"Обновлено", "Файл от"}),
    Значения = Table.TransformColumns(Заголовки, List.Transform(Столбцы, (c) => {c, Число})),
    Результат = if List.Contains(Table.ColumnNames(Значения), "Обновлено")
        then Table.TransformColumnTypes(
            Table.ReplaceValue(Значения, "", null, Replacer.ReplaceValue, {"Файл от"}),
            {{"Обновлено", type datetime}, {"Файл от", type date}}, "en-US")
        else Значения
in
    Результат
