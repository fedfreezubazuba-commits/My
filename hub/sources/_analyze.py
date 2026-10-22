"""
Мастер «Сайт»: что хаб нашёл на странице — цены и числа, таблицы с превью, ссылки на файлы.
И сборка параметров показателя из того, что выбрал пользователь.
"""
import re
from dataclasses import dataclass, field

from . import _files as F
from . import _tables as T
from . import _web as W
from . import sites
from .web_value import code_base_for, fields_from_profile


@dataclass
class Found:
    url: str
    title: str = ""
    profile: object = None
    values: list = field(default_factory=list)    # [{"title", "value", "unit", "field": {...}}]
    tables: list = field(default_factory=list)    # [Table]
    files: list = field(default_factory=list)     # [(текст, ссылка)]
    page_date: object = None
    page_is_week: bool = False

    def summary(self):
        parts = []
        if self.values:
            parts.append(f"значений: {len(self.values)}")
        if self.tables:
            parts.append(f"таблиц: {len(self.tables)}")
        if self.files:
            parts.append(f"ссылок на файлы: {len(self.files)}")
        return ("Найдено — " + ", ".join(parts)) if parts else "На странице не найдено ни цен, ни таблиц"


def analyze(text, url) -> Found:
    url = W.clean_url(url)
    doc = W.parse_html(text)
    f = Found(url=url)
    t = doc.xpath("//title")
    f.title = W.text_of(t[0]) if t else ""
    f.profile = sites.for_url(url)
    if f.profile:
        got = f.profile.extract(text, doc)
        name = got.get("_name") or f.title
        for fld in fields_from_profile(f.profile, url, name):
            v = got.get(fld["key"])
            if v is not None:
                f.values.append({"title": fld["name"], "value": v, "unit": fld["unit"], "field": fld})
    else:
        products = W.jsonld_products(doc)
        for i, p in enumerate(products[:3]):
            v = W.P.parse_number(W.get_path(p, "offers.price"))
            if v is None:
                v = W.P.parse_number(W.get_path(p, "offers.lowPrice"))
            if v is not None:
                name = p.get("name") or f.title
                cur = W.get_path(p, "offers.priceCurrency") or ""
                f.values.append({"title": f"{name} — цена", "value": v, "unit": cur, "field": {
                    "key": "price" if i == 0 else f"price{i + 1}", "name": f"{name} — цена", "unit": cur,
                    "strategies": [{"type": "jsonld", "path": "offers.price"}, {"type": "meta"}]}})
        if not f.values:
            for src, v in W.meta_price(doc)[:3]:
                f.values.append({"title": f"Цена ({src})", "value": v, "unit": "", "field": {
                    "key": "price", "name": f"{f.title} — цена", "unit": "",
                    "strategies": [{"type": "meta"}]}})
                break
    f.tables = []
    for t_ in T.all_tables(doc):
        try:
            T.guess_columns(t_)
        except Exception:  # noqa: BLE001 — таблица без чисел (только проценты, тексты) — не предлагаем
            continue
        f.tables.append(t_)
    f.files = F.file_links(doc, url)
    f.page_date, f.page_is_week = W.page_date(doc)
    return f


def value_params(found: Found, chosen_keys=None):
    """(параметры web_value, название) по выбранным значениям."""
    vals = [v for v in found.values if not chosen_keys or v["field"]["key"] in chosen_keys]
    params = {"url": found.url, "code_base": code_base_for(found.url, found.profile),
              "fields": [v["field"] for v in vals], "fetch_mode": "auto"}
    name = vals[0]["title"].split(" — ")[0] if vals else found.title
    return params, name


def table_params(found: Found, table, mode="snapshot"):
    if mode == "snapshot" and T.rows_are_dates(table):
        mode = "history"          # в строках даты (торговые сессии, недели…) — каждый столбец станет рядом
    params = {"url": found.url, "table": {"kind": table.kind, "title": table.title, "index": table.index},
              "mode": mode, "date": "auto", "code_prefix": W.site_name(found.url).upper(), "fetch_mode": "auto"}
    name = table.title or f"Таблица {table.index + 1}"
    if found.page_is_week:
        params["periodicity"] = "week"
    return params, f"{W.site_name(found.url).capitalize()}: {name}"[:120]


def picked_value_params(url, pick):
    """Пользователь щёлкнул по числу во встроенном браузере → поле с CSS, XPath и текстовым якорем."""
    url = W.clean_url(url)
    strategies = [{"type": "css", "selector": pick["selector"]}, {"type": "xpath", "xpath": pick["xpath"]}]
    if pick.get("label"):
        strategies.append({"type": "anchor", "text": pick["label"]})
    label = pick.get("label") or "значение"
    field_ = {"key": "value", "name": label, "unit": "", "strategies": strategies}
    return {"url": url, "code_base": f"WEB_{W.site_name(url)}".upper() + f"_{T.slug(label, 16)}",
            "fields": [field_], "fetch_mode": "auto"}, label


def picked_table(url, pick):
    """Пользователь выбрал блок с таблицей → такая же таблица среди найденных на всей странице.

    Сначала ищем точное совпадение строк, затем — таблицу, чьи подписи строк больше всего
    встречаются в тексте выбранного блока. Если ничего похожего нет — None."""
    page = W.parse_html(pick.get("page_html") or "<html></html>")
    tables = []
    for t in T.all_tables(page):
        try:
            T.guess_columns(t)
            tables.append(t)
        except Exception:  # noqa: BLE001
            continue
    html = pick.get("html") or ""
    if re.match(r"\s*<(tbody|thead|tr)\b", html, re.I):
        html = f"<table>{html}</table>"          # щёлкнули по телу таблицы, а не по самой таблице
    frag = T.all_tables(W.parse_html(f"<html><body>{html}</body></html>")) if html else []
    if frag:
        rows = frag[0].rows[:3]
        for t in tables:
            if t.rows[:3] == rows:
                return t
    text = " ".join((pick.get("text") or "").split())
    best, score = None, 0
    for t in tables:
        labels = [r[0] for r in t.rows if r and r[0]]
        hits = sum(1 for lab in labels if lab in text)
        if labels and hits / len(labels) >= 0.5 and hits > score:
            best, score = t, hits
    return best
