"""
Профиль METRO (online.metro-cc.ru): цена за штуку, цена за кг и наличие.

Где что лежит (проверено на samples/web/metro_losos.html):
  - цена за штуку — JSON-LD Product.offers.price (12863.5);
  - цена за кг — в состоянии страницы window.__NUXT__: stocks[…].prices_per_unit.price (1979),
    в том же блоке prices.price совпадает с ценой из JSON-LD — по этому находим блок именно
    этого товара, а не рекомендаций;
  - наличие — на странице «Раскупили» / «Нет в наличии» или stocks[…].text «Отсутствует».
    JSON-LD при этом может писать InStock, поэтому ему верим в последнюю очередь.

Цена зависит от выбранного магазина. Если нужен конкретный магазин, один раз выберите его во
встроенном браузере («Войти на сайт») — хаб будет читать страницу с этим выбором.
"""
import re

from .. import _web as W

DOMAINS = ["metro-cc.ru"]
NAME = "METRO"
FIELDS = {
    "price_piece": ("цена за штуку", "руб./шт"),
    "price_kg": ("цена за кг", "руб./кг"),
    "available": ("в наличии (1 — да, 0 — нет)", ""),
}
OUT_OF_STOCK = ("раскупили", "нет в наличии", "сообщить о поступлении", "товар закончился")


def extract(text, doc=None):
    doc = doc if doc is not None else W.parse_html(text)
    out = {}
    products = W.jsonld_products(doc)
    ld_price = None
    if products:
        p = products[0]
        out["_name"] = p.get("name")
        ld_price = W.P.parse_number(W.get_path(p, "offers.price"))
        if ld_price is not None:
            out["price_piece"] = ld_price
        avail = str(W.get_path(p, "offers.availability") or "")
        if avail:
            out["available"] = 1.0 if "instock" in avail.lower() else 0.0
    nuxt = next((s for s in W.script_texts(doc) if "__NUXT__" in s), "")
    if nuxt:
        args = W.nuxt_args(nuxt)
        best = None
        for m in re.finditer(r"stocks:\[\{value:([^,]+),text:([^,]+),.*?prices_per_unit:\{(.*?)\},prices:\{price:([^,}]+)",
                             nuxt, re.S):
            per_unit = re.search(r"(?:^|,)price:([^,}]+)", m.group(3))
            kg = W.resolve(per_unit.group(1), args) if per_unit else None
            piece = W.resolve(m.group(4), args)
            text_tok = m.group(2).strip()
            stock_text = text_tok.strip('"') if text_tok.startswith('"') else args.get(text_tok)
            item = (kg, piece, stock_text)
            if ld_price is not None and piece is not None and abs(piece - ld_price) < 0.01:
                best = item
                break
            if best is None:
                best = item
            if m.start() > 200000:
                break
        if best:
            kg, piece, stock_text = best
            if isinstance(kg, (int, float)):
                out["price_kg"] = float(kg)
            if "price_piece" not in out and isinstance(piece, (int, float)):
                out["price_piece"] = float(piece)
            if isinstance(stock_text, str) and stock_text:
                out["available"] = 0.0 if "отсутств" in stock_text.lower() or "нет" in stock_text.lower() else 1.0
    block = doc.xpath('//*[contains(@class,"product-page-prices-and-buttons")]')
    if block:
        txt = W.text_of(block[0]).lower()
        if any(m in txt for m in OUT_OF_STOCK):
            out["available"] = 0.0
        elif "в корзину" in txt:
            out["available"] = 1.0
    if "price_kg" in out and "price_piece" in out and abs(out["price_kg"] - out["price_piece"]) < 0.01:
        out.pop("price_kg")                      # штучный товар: цена за кг не отличается
    return out
