"""
«Значение на странице» (web_value): цена или число на сайте.

Истории на сайте обычно нет, поэтому хаб строит её сам из ежедневных снимков: каждое
обновление сохраняет значение на дату получения.

Параметры показателя:

    url: https://online.metro-cc.ru/products/losos-murmanskij-6-7kg-inarctica
    code_base: METRO_losos_murmanskij
    fields:
      - key: price_kg
        name: Лосось Мурманский — цена за кг
        unit: руб./кг
        strategies:                 # по порядку; запасные пробуются, если основная сломалась
          - {type: profile, name: metro, field: price_kg}
          - {type: script, pattern: 'prices_per_unit:\\{[^{}]*?price:([\\d.]+)'}
    fetch_mode: auto                # auto | requests | browser

Виды стратегий: jsonld (путь в Product, например offers.price), meta (мета-теги и itemprop),
script (регулярное выражение по встроенным скриптам), json (JSON из <script id=…> по пути),
css, xpath, anchor (первое число после текста), profile (профиль сайта из hub/sources/sites).
Если несколько стратегий дают разные числа — это сигнал проверить (замечание в отчёте).
"""
import json
import re

from ..core import errors as E
from ..core import periods as P
from ..core.config import slug
from . import _web as W
from . import sites
from .base import FetchResult, Obs, SeriesInfo, Source

FIELD_CODES = {"price_piece": "rub_sht", "price_kg": "rub_kg", "available": "nalichie", "price": "price"}


def evaluate(strategy, doc, text, url, cache):
    t = strategy.get("type")
    if t == "profile":
        mod = sites.by_name(strategy.get("name")) or sites.for_url(url)
        if mod is None:
            return None
        if mod.__name__ not in cache:
            cache[mod.__name__] = mod.extract(text, doc)
        return cache[mod.__name__].get(strategy.get("field"))
    if t == "jsonld":
        for p in W.jsonld_products(doc) or W.jsonld_objects(doc):
            v = P.parse_number(W.get_path(p, strategy.get("path", "offers.price")))
            if v is not None:
                return v
        return None
    if t == "meta":
        vals = W.meta_price(doc)
        want = strategy.get("name")
        for src, v in vals:
            if not want or want in src:
                return v
        return None
    if t == "script":
        return W.regex_number(W.script_texts(doc), strategy["pattern"], int(strategy.get("group", 1)))
    if t == "json":
        sel = strategy.get("script_id")
        for s in doc.xpath(f'//script[@id="{sel}"]' if sel else '//script[@type="application/json"]'):
            try:
                v = P.parse_number(W.get_path(json.loads(s.text or ""), strategy.get("path", "")))
            except ValueError:
                continue
            if v is not None:
                return v
        return None
    if t == "css":
        try:
            from lxml.cssselect import CSSSelector
            els = CSSSelector(strategy["selector"])(doc)
        except Exception:  # noqa: BLE001 — нет cssselect или неверный селектор
            els = []
        for el in els:
            v = P.parse_number(W.text_of(el))
            if v is not None:
                return v
        return None
    if t == "xpath":
        try:
            found = doc.xpath(strategy["xpath"])
        except Exception:  # noqa: BLE001
            return None
        for el in found if isinstance(found, list) else [found]:
            v = P.parse_number(el if isinstance(el, str) else W.text_of(el))
            if v is not None:
                return v
        return None
    if t == "anchor":
        return W.anchor_number(doc, strategy["text"], int(strategy.get("chars", 200)))
    return None


def describe_strategy(s):
    t = s.get("type")
    return {"profile": f"профиль сайта ({s.get('field')})", "jsonld": "разметка товара (JSON-LD)",
            "meta": "мета-теги", "script": "данные в скрипте страницы", "json": "JSON на странице",
            "css": f"элемент {s.get('selector', '')}", "xpath": "элемент страницы (XPath)",
            "anchor": f"число после «{s.get('text', '')}»"}.get(t, t)


def extract_fields(fields, doc, text, url):
    """{ключ: (значение, откуда, [расхождения])}."""
    cache, out = {}, {}
    for f in fields:
        found = []
        for s in f.get("strategies", []):
            try:
                v = evaluate(s, doc, text, url, cache)
            except Exception:  # noqa: BLE001 — сломанная стратегия не мешает запасным
                v = None
            if v is not None:
                found.append((float(v), describe_strategy(s)))
        if not found:
            out[f["key"]] = (None, None, [])
            continue
        main, where = found[0]
        diffs = [f"{w}: {o:g}" for o, w in found[1:]
                 if abs(o - main) > 0.01 * max(abs(main), 1e-9)]
        out[f["key"]] = (main, where, diffs)
    return out


class WebValue(Source):
    kind = "web_value"
    title = "Значение на сайте"
    snapshot = True

    def describe(self, params):
        names = ", ".join(f.get("name", f["key"]) for f in params.get("fields", []))
        return f"Значение на странице {params.get('url')}: {names}"

    def default_checks(self, params):
        return {"max_jump_pct": 50}

    def fetch(self, params, ctx) -> FetchResult:
        url = W.clean_url(params["url"])
        fields = params.get("fields") or []
        if not fields:
            raise E.StructureError("В показателе не указано, какое значение брать")

        def need(text):
            doc = W.parse_html(text)
            got = extract_fields(fields, doc, text, url)
            return any(v[0] is not None for v in got.values())

        text, via, final = W.fetch_page(url, ctx, params.get("fetch_mode", "auto"), need=need)
        doc = W.parse_html(text)
        got = extract_fields(fields, doc, text, url)
        today = (ctx.now if ctx else __import__("datetime").datetime.now()).date()
        res = FetchResult(url=url, raw=text.encode("utf-8"), raw_ext="html")
        base = params.get("code_base") or f"WEB_{W.site_name(url)}".upper()
        for f in fields:
            value, where, diffs = got[f["key"]]
            if value is None:
                res.warnings.append(f"Не найдено: {f.get('name', f['key'])}")
                continue
            if diffs:
                res.warnings.append(f"Расхождение источников для «{f.get('name', f['key'])}»: основной ({where}) "
                                    f"{value:g}; " + "; ".join(diffs))
            code = f"{base}_{FIELD_CODES.get(f['key'], slug(f['key'], 12))}"
            res.series.append(SeriesInfo(key=f["key"], name=f.get("name", f["key"]), unit=f.get("unit", ""),
                                         periodicity="snapshot", code=code, url=url,
                                         date_rule="снимок: дата получения (история строится хабом)"))
            res.obs.append(Obs(key=f["key"], period_end=today, period_label=today.isoformat(), value=value,
                               source_date=via))
        if not res.obs:
            if W.looks_like_login(text):
                raise E.LoginRequired("Цена видна только после входа на сайт")
            raise E.StructureError("На странице не найдено ни одного из нужных значений — вероятно, изменилась вёрстка")
        return res


def fields_from_profile(mod, url, product_name=""):
    """Поля показателя по профилю сайта (Metro: цена за шт., за кг, наличие)."""
    name = product_name or "Товар"
    fields = []
    for key, (title, unit) in mod.FIELDS.items():
        strategies = [{"type": "profile", "name": mod.__name__.rsplit(".", 1)[-1], "field": key}]
        if key == "price_piece":
            strategies.append({"type": "jsonld", "path": "offers.price"})
        if key == "price_kg":
            strategies.append({"type": "script", "pattern": r"prices_per_unit:\{(?:[^{}]|\{[^{}]*\})*?\bprice:(\d[\d.]*)"})
        fields.append({"key": key, "name": f"{name} — {title}", "unit": unit, "strategies": strategies})
    return fields


def code_base_for(url, mod=None):
    """METRO_losos_murmanskij — сайт + первые слова из адреса товара."""
    site = (getattr(mod, "NAME", None) or W.site_name(url)).upper()
    from urllib.parse import urlparse
    path = [p for p in urlparse(url).path.split("/") if p]
    words = re.split(r"[-_]+", path[-1]) if path else []
    words = [w for w in words if w and not w.isdigit()][:2]
    return f"{site}_{'_'.join(slug(w, 12) for w in words)}" if words else site
