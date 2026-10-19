// Таблица «Хаб»: Дата | Период | по столбцу на каждый ряд подписки.
// Столбцы не перечислены вручную: список кодов берётся из посылки, поэтому новые ряды
// появляются сами — справа (порядок строк посылки = порядок подписки).
let
    Путь = Хаб_Посылка,
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
    Типы = Table.TransformColumnTypes(
        Заголовки,
        {{"Дата", type date}, {"Период", type text}, {"Код", type text}, {"Значение", type number}},
        "en-US"),
    Коды = List.Distinct(Типы[Код]),
    // Ранг подписи периода: 2025-01-15 (день) < 2025-W03 < 2025-01 < 2025-Q1 < 2025-H1 < 2025
    Ранг = (p as nullable text) as number =>
        let t = if p = null then "" else p in
        if Text.Length(t) = 10 then 0
        else if Text.Contains(t, "-W") then 1
        else if Text.Contains(t, "-Q") then 3
        else if Text.Contains(t, "-H") then 4
        else if Text.Length(t) = 7 then 2
        else 5,
    // На одну дату может прийтись месячный и квартальный ряд — берём самую подробную подпись
    Метки = Table.Group(
        Типы, {"Дата"},
        {{"Период", each List.First(List.Sort([Период], each Ранг(_))), type text}}),
    Длинная = Table.SelectColumns(Типы, {"Дата", "Код", "Значение"}),
    Широкая = Table.Pivot(Длинная, Коды, "Код", "Значение", List.First),
    МеткиДляСоединения = Table.RenameColumns(Метки, {{"Дата", "Дата_метка"}}),
    Соединение = Table.Join(Широкая, "Дата", МеткиДляСоединения, "Дата_метка", JoinKind.LeftOuter),
    БезЛишнего = Table.RemoveColumns(Соединение, {"Дата_метка"}),
    Порядок = Table.ReorderColumns(БезЛишнего, {"Дата", "Период"} & Коды),
    ТипыЗначений = Table.TransformColumnTypes(Порядок, List.Transform(Коды, each {_, type number})),
    Результат = Table.Sort(ТипыЗначений, {{"Дата", Order.Ascending}})
in
    Результат
