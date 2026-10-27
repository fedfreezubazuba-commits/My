"""
«Путь к файлу» — для сайтов, где файл лежит не по постоянной ссылке, а в свежей новости (Минфин: список отчётов →
новость за последний месяц → ссылка «Скачать»). Пользователь один раз проходит путь во встроенном браузере, хаб
записывает щелчки и превращает их в шаги; дальше при каждом обновлении проходит шаги сам.

Шаг — какую ссылку взять на текущей странице:

    {"words": "скачать",                 # слова в тексте самой ссылки (все; регистр и «ё» не важны)
     "near": "ежемесячная информация",   # слова в «плашке» вокруг ссылки (карточка новости) — если у ссылки нет
                                         # текста (значок со стрелкой) или текст у всех ссылок одинаковый
     "pick": "first",                    # first — первая подходящая на странице (свежие обычно сверху),
                                         # newest — с самой поздней датой в плашке, last — последняя
     "href": ""}                         # необязательно: кусок адреса ссылки («.xlsx», «/document/»)

Параметры показателя (file_link / file_sheets): path: {start: <адрес первой страницы>, steps: [шаг, …]}.
"""
import datetime as dt
import re
from urllib.parse import urljoin

from ..core import periods as P
from ..core.errors import StructureError

PICKS = [("first", "первую подходящую (свежие обычно сверху)"), ("newest", "с самой поздней датой рядом"),
         ("last", "последнюю подходящую")]
GENERIC = re.compile(r"^(скачать|подробнее|далее|читать|открыть|download|more|read more|pdf|xlsx?|docx?|zip|"
                     r"файл|документ)\.?$", re.I)
DATE_RX = re.compile(r"\b\d{1,2}[./]\d{1,2}[./]\d{4}\b|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}\s+[а-яё]+\s+\d{4}\b", re.I)
STOP = {"и", "в", "во", "на", "по", "за", "с", "со", "об", "о", "от", "до", "из", "к", "для", "г", "гг", "год", "года"}
MAX_CONTEXT = 600
TAG_RX = re.compile(r"#\S+(?:\s+[A-ZА-ЯЁ0-9-]{2,}(?![a-zа-яё]))*")   # «#ФЕДЕРАЛЬНЫЙ БЮДЖЕТ #СТАТИСТИКА»


def norm(text) -> str:
    return " ".join(str(text or "").lower().replace("ё", "е").split())


def links(html_text, base_url) -> list[dict]:
    """Ссылки страницы: [{href, text, near, index}]; near — текст «плашки» вокруг ссылки (ближайший предок
    с заметным текстом, но не вся страница)."""
    from lxml import html as LH
    try:
        doc = LH.fromstring(html_text)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for i, a in enumerate(doc.iter("a")):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        text = _text_of(a) or (a.get("title") or a.get("aria-label") or "").strip()
        near = text
        node = a
        for _ in range(6):
            node = node.getparent()
            if node is None:
                break
            t = _text_of(node)
            if len(t) > MAX_CONTEXT:
                break
            if len(t) > len(text) + 15:
                near = t
                if len(t) > 60:
                    break
        out.append({"href": urljoin(base_url, href), "text": text, "near": near, "index": i})
    return out


def _text_of(node) -> str:
    """Текст элемента с пробелами между частями («#СТАТИСТИКА Документ», а не «#СТАТИСТИКАДокумент»)."""
    return " ".join(" ".join(t for t in node.itertext() if t and t.strip()).split())


def _has(words, text) -> bool:
    hay = norm(text)
    return all(w in hay for w in norm(words).split())


def matches(link, step) -> bool:
    if step.get("words") and not _has(step["words"], link["text"]):
        return False
    if step.get("near") and not _has(step["near"], link["near"]):
        return False
    if step.get("href") and norm(step["href"]) not in link["href"].lower():
        return False
    return bool(step.get("words") or step.get("near") or step.get("href"))


def date_near(link) -> dt.date | None:
    m = DATE_RX.findall(link.get("near") or "")
    dates = [d for d in (P.parse_date(x) for x in m) if d]
    return max(dates) if dates else None


def choose(cands, pick="first"):
    if not cands:
        return None
    if pick == "last":
        return cands[-1]
    if pick == "newest":
        dated = [(date_near(c), c) for c in cands]
        if any(d for d, _c in dated):
            return max(((d or dt.date.min, -c["index"]), c) for d, c in dated)[1]
    return cands[0]


def step_links(html_text, base_url, step) -> list[dict]:
    return [lk for lk in links(html_text, base_url) if matches(lk, step)]


def describe(step) -> str:
    parts = []
    if step.get("words"):
        parts.append(f"в тексте ссылки «{step['words']}»")
    if step.get("near"):
        parts.append(f"рядом со ссылкой «{step['near']}»")
    if step.get("href"):
        parts.append(f"в адресе «{step['href']}»")
    pick = dict(PICKS).get(step.get("pick") or "first", "")
    return "ссылка: " + ", ".join(parts) + (f"; брать {pick}" if pick else "")


def follow(path, ctx=None, get_page=None, trace=None) -> str:
    """Пройти шаги от стартовой страницы; → адрес файла (ссылка последнего шага). trace — список, куда
    записывается ход по шагам: (страница, сколько подходящих ссылок, выбранная ссылка)."""
    from . import _web as W
    url = W.clean_url(path["start"])
    steps = path.get("steps") or []
    if not steps:
        raise StructureError("В пути к файлу нет ни одного шага")
    get_page = get_page or (lambda u, need: W.fetch_page(u, ctx, "auto", need=need)[::2])
    for n, step in enumerate(steps, 1):
        text, final = get_page(url, lambda t, s=step, u=url: bool(step_links(t, u, s)))
        cands = step_links(text, final or url, step)
        if n == len(steps) and not step.get("href"):     # последний шаг — файл: ссылки на файлы важнее страниц
            files = [c for c in cands if looks_like_file(c["href"])]
            cands = files or cands
        chosen = choose(cands, step.get("pick") or "first")
        if trace is not None:
            trace.append((final or url, len(cands), chosen))
        if chosen is None:
            err = StructureError(f"Шаг {n}: на странице {url} не нашлась {describe(step)}")
            err.raw_text = text
            raise err
        url = chosen["href"]
    return url


def _stable_words(text, limit=6, common=(), skip_generic=True) -> str:
    """Слова, которые не меняются от выпуска к выпуску: без дат, чисел, месяцев, служебных слов, тегов (#…) и
    общих слов («документ», «скачать»). common — слова, которые есть почти у всех ссылок страницы: сначала
    берутся слова, отличающие плашку от других, затем — до limit — остальные (в порядке текста)."""
    t = TAG_RX.sub(" ", DATE_RX.sub(" ", str(text or "")))
    words = []
    for w in re.findall(r"#?[A-Za-zА-Яа-яЁё]{3,}", t):
        lw = norm(w)
        if lw.startswith("#") or lw in STOP or (skip_generic and GENERIC.match(lw)) or P.month_from_word(lw) \
                or lw in words:
            continue
        words.append(lw)
    own = [w for w in words if w not in common][:limit]
    for w in words:
        if len(own) >= limit:
            break
        if w not in own:
            own.append(w)
    return " ".join(w for w in words if w in own)


FILE_EXT = re.compile(r"\.(xlsx|xlsm|xls|csv|pdf|zip|docx?|rar|7z)$", re.I)


def looks_like_file(href) -> bool:
    from urllib.parse import urlparse
    path = urlparse(href or "").path.lower()
    return bool(FILE_EXT.search(path)) or any(w in path for w in ("/download", "/upload", "/files/", "/file/"))


def _href_hint(href) -> str:
    """Постоянная часть адреса ссылки: расширение файла («.xlsx») или раздел сайта («/ru/document/»)."""
    from urllib.parse import urlparse
    path = urlparse(href or "").path
    m = FILE_EXT.search(path)
    if m:
        return m.group(0).lower()
    head, _sep, last = path.rstrip("/").rpartition("/")
    if re.search(r"\d", last) and head:
        return head + "/"
    return ""


def _common_words(click) -> set:
    """Слова, которые есть в плашках у большинства других ссылок той же страницы (по её сохранённому тексту)."""
    html_text = click.get("html")
    if not html_text:
        return set()
    others = [lk for lk in links(html_text, click.get("page") or "") if lk["href"] != click.get("href")]
    if len(others) < 2:
        return set()
    counts = {}
    for lk in others:
        for w in set(re.findall(r"[a-zа-я]{3,}", norm(lk["near"]))):
            counts[w] = counts.get(w, 0) + 1
    near_links = [lk for lk in others if lk["near"] != lk["text"]] or others
    return {w for w, n in counts.items() if n >= max(2, len(near_links) // 2)}


def guess_steps(clicks) -> list[dict]:
    """Записанные щелчки → шаги. Текст ссылки, если он говорящий («Скачать», «Краткая информация…»); иначе
    (значок без текста, «Подробнее») — слова из плашки вокруг, которые отличают её от других плашек страницы.
    Даты и месяцы не берутся — они меняются. У ссылки без текста — ещё постоянная часть адреса (.xlsx)."""
    steps = []
    for c in clicks:
        text = c.get("text") or ""
        words = _stable_words(text, skip_generic=False)
        near, href = "", ""
        common = _common_words(c)
        if not _stable_words(text) or (words and set(words.split()) <= common):
            near = _stable_words(c.get("near") or "", 4, common | set(words.split()))
            if not text:
                href = _href_hint(c.get("href"))
        pick = "newest" if DATE_RX.search(c.get("near") or "") else "first"
        steps.append({"words": words, "near": near, "pick": pick, "href": href})
    return steps
