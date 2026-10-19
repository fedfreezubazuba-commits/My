"""
Показатели-образцы с первых сайтов из ТЗ (раздел 8.2): Metro, Металл Торг, Fearnleys.
Добавляются кнопкой «Добавить примеры» в «Настройках» (уже добавленные не дублируются).
Параметры проверены на сохранённых страницах samples/web/.
"""

EXAMPLES = [
    {
        "source": "web_value",
        "name": "Лосось Мурманский Inarctica 6/7 (METRO)",
        "schedule": "daily",
        "checks": {"max_jump_pct": 50},
        "tags": ["цены", "образец"],
        "params": {
            "url": "https://online.metro-cc.ru/products/losos-murmanskij-6-7kg-inarctica",
            "code_base": "METRO_losos_murmanskij",
            "fetch_mode": "auto",
            "fields": [
                {"key": "price_kg", "name": "Лосось Мурманский — цена за кг", "unit": "руб./кг",
                 "strategies": [{"type": "profile", "name": "metro", "field": "price_kg"},
                                {"type": "script",
                                 "pattern": r"prices_per_unit:\{(?:[^{}]|\{[^{}]*\})*?\bprice:(\d[\d.]*)"}]},
                {"key": "price_piece", "name": "Лосось Мурманский — цена за штуку (~6,5 кг)", "unit": "руб./шт",
                 "strategies": [{"type": "profile", "name": "metro", "field": "price_piece"},
                                {"type": "jsonld", "path": "offers.price"}]},
                {"key": "available", "name": "Лосось Мурманский — в наличии (1 — да, 0 — нет)", "unit": "",
                 "strategies": [{"type": "profile", "name": "metro", "field": "available"}]},
            ],
        },
    },
    {
        "source": "web_table",
        "name": "Металл Торг: цветные металлы (LME, $/т)",
        "schedule": "daily",
        "checks": {"max_jump_pct": 30},
        "tags": ["металлы", "образец"],
        "params": {"url": "https://www.metaltorg.ru/", "code_prefix": "METALTORG", "mode": "snapshot", "date": "auto",
                   "table": {"kind": "html", "title": "Цветные металлы", "index": 2}, "fetch_mode": "auto"},
    },
    {
        "source": "web_table",
        "name": "Металл Торг: биржевые цены на сталь ($/т)",
        "schedule": "daily",
        "checks": {"max_jump_pct": 30},
        "tags": ["металлы", "образец"],
        "params": {"url": "https://www.metaltorg.ru/", "code_prefix": "METALTORG", "mode": "snapshot", "date": "auto",
                   "table": {"kind": "html", "title": "Биржевые цены на сталь", "index": 0}, "fetch_mode": "auto"},
    },
    {
        "source": "web_table",
        "name": "Fearnleys: танкеры, спот (Dirty, WS)",
        "schedule": "daily",
        "checks": {"max_jump_pct": 60},
        "tags": ["фрахт", "образец"],
        "params": {"url": "https://fearnpulse.com/fearnleys-weekly-report", "code_prefix": "FEARNLEYS",
                   "mode": "snapshot", "date": "auto", "periodicity": "week",
                   "table": {"kind": "cards", "title": "Dirty (Spot WS 2026, Daily Change)", "index": 0},
                   "fetch_mode": "auto"},
    },
    {
        "source": "web_table",
        "name": "Fearnleys: сухогрузы, тайм-чартер 1 год (USD/день)",
        "schedule": "daily",
        "checks": {"max_jump_pct": 60},
        "tags": ["фрахт", "образец"],
        "params": {"url": "https://fearnpulse.com/fearnleys-weekly-report", "code_prefix": "FEARNLEYS",
                   "mode": "snapshot", "date": "auto", "periodicity": "week",
                   "table": {"kind": "cards", "title": "1 Year T/C (USD/Day, Weekly Change)", "index": 5},
                   "fetch_mode": "auto"},
    },
]


def add_examples(cfg) -> list[str]:
    have = {(j["source"], j["name"]) for j in cfg.jobs()}
    added = []
    for e in EXAMPLES:
        if (e["source"], e["name"]) in have:
            continue
        j = cfg.add_job(e["source"], e["name"], e["params"], schedule=e["schedule"], checks=e["checks"], tags=e["tags"])
        added.append(j["id"])
    return added
