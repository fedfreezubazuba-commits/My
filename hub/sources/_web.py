"""
Общие инструменты веб-источников: загрузка страницы (requests → встроенный браузер),
очистка ссылки от рекламных параметров, распознавание защиты от роботов и требования входа,
поиск цены (JSON-LD, микроразметка, мета-теги, встроенный JSON, CSS/XPath, текстовый якорь),
поиск даты отчёта на странице.
"""
import json
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from ..core import errors as E
from ..core import periods as P

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
TRACKING = re.compile(r"^(utm_.*|yclid|gclid|fbclid|ysclid|_openstat|from|frommarket|ref|referrer|"
                      r"roistat.*|mc_.*|_ga|_gl|clid|erid|advert.*|campaign.*|partner.*|admitad_uid)$", re.I)
BOT_MARKERS = ("captcha", "капча", "qrator", "ddos-guard", "cf-chl", "cf_chl", "challenge-platform",
               "доступ ограничен", "access denied", "are you a robot", "вы робот", "подтвердите, что вы не робот",
               "smartcaptcha", "please enable javascript and cookies")
LOGIN_MARKERS = ("войдите", "войти в личный кабинет", "авторизуйтесь", "доступно подписчикам",
                 "только для подписчиков", "please log in", "sign in to", "log in to continue")


def clean_url(url: str) -> str:
    """Убрать рекламные параметры (utm_*, yclid, gclid…) и якорь."""
    url = (url or "").strip()
    if not url:
        return url
    if not re.match(r"^[a-z]+://", url, re.I):
        url = "https://" + url
    u = urlparse(url)
    q = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if not TRACKING.match(k)]
    return urlunparse((u.scheme, u.netloc, u.path, u.params, urlencode(q), ""))


def site_name(url: str) -> str:
    """online.metro-cc.ru → metro; www.metaltorg.ru → metaltorg; fearnpulse.com → fearnpulse."""
    host = urlparse(url).netloc.lower().split(":")[0]
    parts = [p for p in host.split(".") if p not in ("www", "online", "m", "shop")]
    name = parts[-2] if len(parts) >= 2 else (parts[0] if parts else "web")
    return re.sub(r"[^a-z0-9]+", "", name.split("-")[0]) or "web"


def decode(data: bytes, content_type: str = "") -> str:
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    head = data[:4000]
    m2 = re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', head, re.I)
    for enc in [m.group(1) if m else None, m2.group(1).decode() if m2 else None, "utf-8", "cp1251"]:
        if not enc:
            continue
        try:
            return data.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", "replace")


def detect_block(status: int, text: str):
    """Категория ошибки, если страница — заглушка защиты или входа; иначе None."""
    low = (text or "")[:200000].lower()
    if status in (401,):
        return E.LOGIN
    if status in (429, 503) and not any(m in low for m in BOT_MARKERS):
        return E.RATE_LIMIT
    if status in (403, 429, 503) or (len(low) < 30000 and any(m in low for m in BOT_MARKERS)):
        return E.BOT
    return None


def looks_like_login(text: str) -> bool:
    low = (text or "").lower()
    return 'type="password"' in low and any(m in low for m in LOGIN_MARKERS + ("вход", "login"))


def http_get(url, ctx=None, timeout=60):
    """(status, content_type, bytes, final_url) через requests."""
    import requests
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                      "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})
    r = s.get(url, timeout=timeout, allow_redirects=True)
    return r.status_code, r.headers.get("Content-Type", ""), r.content, r.url


def fetch_page(url, ctx, mode="auto", need=None):
    """Страница как текст. mode: auto | requests | browser.

    need(text) -> bool — «нужное нашлось»; если нет и есть встроенный браузер, пробуем через него
    (страницы, которые строятся скриптом, и сайты с защитой). Возвращает (text, via, final_url)."""
    err = None
    if mode in ("auto", "requests"):
        try:
            status, ctype, data, final = http_get(url, ctx)
            text = decode(data, ctype)
            cat = detect_block(status, text)
            if cat is None and status >= 400:
                raise E.HubError(E.NETWORK if status >= 500 else E.STRUCTURE, f"Сайт ответил ошибкой {status}")
            if cat:
                raise E.HubError(cat, {E.BOT: "Сайт включил защиту от роботов",
                                       E.LOGIN: "Сайт требует вход",
                                       E.RATE_LIMIT: "Сайт просит подождать"}.get(cat, "Сайт не отдал страницу"))
            if need is None or need(text):
                return text, "requests", final
            err = E.StructureError("На странице не найдено нужное: изменилась вёрстка или страница строится скриптом")
            err.raw_text = text
        except E.HubError as e:
            err = e
        except Exception as e:  # noqa: BLE001
            err = e
        if mode == "requests":
            raise err
    browser = getattr(ctx, "browser", None)
    if browser is None or not browser.available():
        if err:
            if isinstance(err, E.HubError) and err.category == E.STRUCTURE:
                raise E.StructureError(str(err) + ". Установите встроенный браузер (запустите «Установка.bat» "
                                                  "ещё раз), чтобы читать такие страницы.")
            raise err
        raise E.HubError(E.INTERNAL, "Встроенный браузер не установлен — запустите «Установка.bat» ещё раз")
    try:
        text, final = browser.get_html(url)
    except Exception as e:  # noqa: BLE001 — браузер не запустился: важнее исходная причина
        if err is None:
            raise
        if isinstance(err, E.HubError) and err.category == E.STRUCTURE:
            raise E.StructureError(f"{err} (встроенный браузер тоже не помог: {E.human_message(e)})")
        raise err
    cat = detect_block(200, text)
    if cat == E.BOT:
        raise E.HubError(E.BOT, "Сайт показывает проверку «я не робот» — откройте его во встроенном браузере")
    if need is not None and not need(text):
        # вторая попытка — настоящее окно за краем экрана: часть сайтов прячет данные от «безголового» браузера
        try:
            text2, final2 = browser.get_html(url, offscreen=True)
            if need(text2):
                return text2, "browser", final2
            text = text2
        except Exception:  # noqa: BLE001
            pass
        if looks_like_login(text):
            raise E.LoginRequired("Нужные данные видны только после входа на сайт")
        e = E.StructureError("На странице не найдено нужное: изменилась вёрстка или выбранный элемент "
                             "больше не находится. Страница сохранена — нажмите «Отправить Claude»")
        e.raw_text = text
        raise e
    return text, "browser", final


def parse_html(text):
    from lxml import html as LH
    try:
        return LH.fromstring(text)
    except Exception as e:  # noqa: BLE001
        raise E.StructureError(f"Страница не читается как HTML: {e}")


def text_of(el) -> str:
    return " ".join((el.text_content() if hasattr(el, "text_content") else str(el)).split())


# --- JSON-LD и микроразметка -------------------------------------------------
def jsonld_objects(doc):
    out = []
    for s in doc.xpath('//script[@type="application/ld+json"]'):
        raw = (s.text or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            try:
                data = json.loads(re.sub(r",\s*([}\]])", r"\1", raw))
            except ValueError:
                continue
        stack = [data]
        while stack:
            x = stack.pop()
            if isinstance(x, list):
                stack.extend(x)
            elif isinstance(x, dict):
                out.append(x)
                if "@graph" in x:
                    stack.append(x["@graph"])
    return out


def get_path(obj, path):
    for part in [p for p in str(path).split(".") if p]:
        if isinstance(obj, list):
            if part.isdigit() and int(part) < len(obj):
                obj = obj[int(part)]
                continue
            obj = obj[0] if obj else None
        if isinstance(obj, dict):
            obj = obj.get(part)
        else:
            return None
    if isinstance(obj, list) and obj and not isinstance(obj[0], (dict, list)):
        obj = obj[0]
    return obj


def jsonld_products(doc):
    return [o for o in jsonld_objects(doc) if "product" in str(o.get("@type", "")).lower()]


def meta_price(doc):
    """[(откуда, значение)] — микроразметка и мета-теги цены."""
    out = []
    for name in ("product:price:amount", "og:price:amount", "price"):
        for m in doc.xpath(f'//meta[@property="{name}" or @name="{name}"]/@content'):
            v = P.parse_number(m)
            if v is not None:
                out.append((f"meta {name}", v))
    for el in doc.xpath('//*[@itemprop="price"]'):
        v = P.parse_number(el.get("content") or text_of(el))
        if v is not None:
            out.append(("itemprop price", v))
    return out


# --- встроенный JSON / JS ----------------------------------------------------
def script_texts(doc):
    return [s.text or "" for s in doc.xpath("//script") if (s.text or "").strip()]


def nuxt_args(script: str) -> dict:
    """window.__NUXT__=(function(a,b,…){…}(1,"x",…)) → {"a": 1, "b": "x", …} для подстановки сокращений."""
    m = re.search(r"__NUXT__\s*=\s*\(function\(([^)]*)\)", script)
    if not m:
        return {}
    names = [n.strip() for n in m.group(1).split(",")]
    end = script.rstrip().rstrip(";").rstrip()
    if not end.endswith("))"):
        return {}
    start = end.rfind("}(")
    if start < 0:
        return {}
    values = _js_values(end[start + 2:-2])
    return dict(zip(names, values))


def _js_values(text):
    """Разбор списка JS-литералов верхнего уровня: числа, строки, true/false/null/void 0; объекты → None."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c in " \n\t,":
            i += 1
            continue
        if c == '"' or c == "'":
            j, buf = i + 1, []
            while j < n and text[j] != c:
                if text[j] == "\\" and j + 1 < n:
                    nxt = text[j + 1]
                    if nxt == "u" and j + 5 < n:
                        buf.append(chr(int(text[j + 2:j + 6], 16)))
                        j += 6
                        continue
                    buf.append({"n": "\n", "t": "\t"}.get(nxt, nxt))
                    j += 2
                    continue
                buf.append(text[j])
                j += 1
            out.append("".join(buf))
            i = j + 1
            continue
        if c in "{[":
            depth, j = 0, i
            while j < n:
                ch = text[j]
                if ch in "\"'":
                    q = ch
                    j += 1
                    while j < n and text[j] != q:
                        j += 2 if text[j] == "\\" else 1
                elif ch in "{[":
                    depth += 1
                elif ch in "}]":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            out.append(None)
            i = j + 1
            continue
        m = re.match(r"(void 0|true|false|null|undefined|-?\d+(?:\.\d+)?(?:e[-+]?\d+)?|[\w$.]+)", text[i:])
        if not m:
            i += 1
            continue
        tok = m.group(1)
        val = {"true": True, "false": False, "null": None, "undefined": None, "void 0": None}.get(tok, tok)
        if isinstance(val, str):
            try:
                val = float(val) if re.match(r"-?\d", val) else None
            except ValueError:
                val = None
        out.append(val)
        i += len(tok)
    return out


def resolve(token, mapping):
    token = token.strip()
    if re.match(r"^-?\d", token):
        return P.parse_number(token)
    return mapping.get(token)


def regex_number(texts, pattern, group=1):
    rx = re.compile(pattern, re.S)
    for t in texts:
        m = rx.search(t)
        if m:
            v = P.parse_number(m.group(group))
            if v is not None:
                return v
    return None


def anchor_number(doc, anchor, max_chars=200):
    """Первое число после текста-якоря (например, «Цена за кг»)."""
    import copy
    body = doc.xpath("//body")
    root = copy.deepcopy(body[0] if body else doc)
    for bad in root.xpath(".//script|.//style|.//noscript"):
        if bad.getparent() is not None:
            bad.getparent().remove(bad)
    full = " ".join(root.text_content().split())
    i = full.lower().find(anchor.lower())
    if i < 0:
        return None
    return P.parse_number(full[i + len(anchor): i + len(anchor) + max_chars])


# --- дата отчёта на странице -------------------------------------------------
DATE_PATTERNS = [
    r"week\s*\d{1,2}\s*[-–—]\s*([A-Za-z]+\s+\d{1,2},?\s+\d{4})",
    r"неделя\s*\d{1,2}[^0-9]{0,10}(\d{1,2}[./]\d{1,2}[./]\d{4})",
    r"(?:по состоянию на|на дату|дата обновления|обновлено|данные на|отчёт от|report date)[:\s]+"
    r"(\d{1,2}[./]\d{1,2}[./]\d{2,4}|\d{1,2}\s+[а-яё]+\s+\d{4}|[A-Za-z]+\s+\d{1,2},?\s+\d{4})",
]


def page_date(doc_or_text):
    """(дата, «неделя?») — дата отчёта, найденная на странице, или (None, False)."""
    if isinstance(doc_or_text, str):
        text = doc_or_text
    else:
        body = doc_or_text.xpath("//body")
        text = " ".join((body[0] if body else doc_or_text).text_content().split())[:20000]
        for m in doc_or_text.xpath('//meta[@property="article:modified_time" or @property="article:published_time"]/@content'):
            d = P.parse_date(m)
            if d:
                return d, False
    for i, pat in enumerate(DATE_PATTERNS):
        m = re.search(pat, text, re.I)
        if m:
            d = P.parse_date(m.group(1))
            if d:
                return d, i < 2
    return None, False
