"""
Сохранение живых ответов ЦБ и ЕМИСС в samples/live/ — для тестов будущих сессий Claude.

Запускается у пользователя в Windows (из облака эти сайты недоступны):
    python -m hub samples
Затем папку samples/live/ можно добавить в репозиторий (личных данных там нет).
"""
import json
import time

from .paths import PROJECT

CBR_CATEGORY, CBR_INDICATOR = 18, 37      # ставки по вкладам — пример из ТЗ
FEDSTAT_ID = 31074                         # среднемесячная зарплата — пример


def save_live_samples() -> str:
    out = PROJECT / "samples" / "live"
    out.mkdir(parents=True, exist_ok=True)
    lines = []
    from ..sources.cbr_api import CBR
    cbr = CBR()
    try:
        (out / "cbr_publications.json").write_text(json.dumps(cbr._get("/publications"), ensure_ascii=False), "utf-8")
        cats = cbr._get("/categoryNew")
        (out / "cbr_categoryNew.json").write_text(json.dumps(cats, ensure_ascii=False), "utf-8")
        ds = cbr._get("/datasets", [("publicationId", CBR_CATEGORY)])
        (out / f"cbr_datasets_{CBR_CATEGORY}.json").write_text(json.dumps(ds, ensure_ascii=False), "utf-8")
        y = time.localtime().tm_year
        d = cbr._get("/dataNew", [("categoryId", CBR_CATEGORY), ("y1", y - 2), ("y2", y), ("i_ids", CBR_INDICATOR)])
        (out / f"cbr_dataNew_{CBR_CATEGORY}_{CBR_INDICATOR}.json").write_text(json.dumps(d, ensure_ascii=False), "utf-8")
        lines.append("Банк России: сохранено 4 файла")
    except Exception as e:  # noqa: BLE001
        lines.append(f"Банк России: не удалось ({e})")
    from ..sources.fedstat_api import BASE, Fedstat
    fs = Fedstat()
    try:
        _, page = fs._get(f"{BASE}/indicator/{FEDSTAT_ID}")
        (out / f"fedstat_indicator_{FEDSTAT_ID}.html").write_bytes(page)
        info = fs.indicator(FEDSTAT_ID)
        years = sorted(info["filters"].get("3", {}).get("values", {}))[-3:]
        xml = fs.download(FEDSTAT_ID, "sdmx", {"3": years} if years else None)
        (out / f"fedstat_{FEDSTAT_ID}.sdmx.xml").write_bytes(xml)
        lines.append("ЕМИСС: сохранено 2 файла")
    except Exception as e:  # noqa: BLE001
        lines.append(f"ЕМИСС: не удалось ({e})")
    lines.append(f"Папка: {out}")
    return "\n".join(lines)
