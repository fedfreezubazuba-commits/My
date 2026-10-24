"""
FRED — база экономических данных ФРС Сент-Луиса (fred.stlouisfed.org): сотни тысяч рядов США и мира
(цены на сырьё МВФ, ставки, инфляция…). Ряд узнаётся по коду (PFISHUSDM — мировая цена рыбной муки).

Данные — CSV «Download → CSV (data)» по постоянной ссылке `graph/fredgraph.csv?id=<код>`: без дат и
оформления графика в ссылке (их кнопка сайта вписывает в ссылку каждый раз новыми) — вся история ряда.
Ключ API не нужен. Название, единицы и периодичность — со страницы ряда (`series/<код>`), один раз при
добавлении; не получилось — ряд всё равно грузится (название = код, периодичность — по шагу дат).

Параметры показателя:

    ids: ["PFISHUSDM"]
    meta: {"PFISHUSDM": {"title": "Global price of Fish meal", "units": "U.S. Dollars per Metric Ton",
                         "frequency": "Monthly", "seasonal": "Not Seasonally Adjusted"}}

Дата в FRED — начало периода (январь = 1992-01-01) → хаб пишет конец периода; у недельных и дневных рядов
дата FRED — сам день (неделя «Ending Friday» — пятница), она остаётся как есть.
"""
import datetime as dt
import html
import re
import time
from urllib.parse import parse_qs, urlsplit

from ..core import errors as E
from ..core import periods as P
from . import _web as W
from .base import FetchResult, Obs, SeriesInfo, Source

HOST = "fred.stlouisfed.org"
BASE = f"https://{HOST}"
PAUSE_S = 1
ID_RX = re.compile(r"^[A-Z][A-Z0-9_]{0,39}$")
FREQ = [("daily", P.DAY), ("weekly", P.WEEK), ("biweekly", P.WEEK), ("monthly", P.MONTH),
        ("quarterly", P.QUARTER), ("semiannual", P.HALF), ("annual", P.YEAR)]
LABELS = ("Units", "Frequency", "Seasonal Adjustment", "Source", "Release", "Notes", "Series ID", "Title",
          "Date Range", "Last Updated", "Observation", "Updated")


def csv_url(sid) -> str:
    return f"{BASE}/graph/fredgraph.csv?id={sid}"


def page_url(sid) -> str:
    return f"{BASE}/series/{sid}"


def is_fred(text) -> bool:
    return HOST in (text or "").lower()


def ids_from_text(text) -> list[str]:
    """Коды рядов из ссылки FRED (страница ряда, fredgraph.csv?id=…, data/<код>.txt) или из списка кодов."""
    out = []
    for part in re.split(r"[\s,;]+", (text or "").strip()):
        if not part:
            continue
        if "/" in part or "?" in part:
            u = urlsplit(part if "://" in part else "https://" + part)
            q = parse_qs(u.query)
            found = [x for v in q.get("id", []) for x in v.split(",")]
            m = re.search(r"/(?:series|data|graph/fredgraph\.\w+)/([A-Za-z0-9_]+)", u.path)
            if m:
                found.append(re.sub(r"\.txt$", "", m.group(1)))
        else:
            found = [part]
        for sid in found:
            sid = sid.strip().upper()
            if ID_RX.match(sid) and sid not in out:
                out.append(sid)
    return out


def periodicity_of(frequency) -> str | None:
    low = (frequency or "").lower()
    return next((p for word, p in FREQ if low.startswith(word)), None)


def parse_meta(text, sid) -> dict:
    """Название, единицы, периодичность со страницы ряда (или из текстового файла data/<код>.txt)."""
    meta = {}
    m = re.search(r"<title>\s*(.*?)\s*</title>", text or "", re.S | re.I)
    if m:
        t = html.unescape(m.group(1))
        t = re.split(r"\s*\|\s*FRED", t)[0]
        t = re.sub(rf"\s*\({re.escape(sid)}\)\s*$", "", t).strip()
        if t:
            meta["title"] = t
    plain = html.unescape(re.sub(r"<script.*?</script>|<style.*?</style>", " ", text or "", flags=re.S | re.I))
    plain = re.sub(r"<[^>]+>", "\n", plain)
    lines = [" ".join(x.split()) for x in plain.splitlines()]
    lines = [x for x in lines if x]
    stop = "|".join(re.escape(x) for x in LABELS)
    flat = " ".join(lines)
    for label, key in (("Title", "title"), ("Units", "units"), ("Frequency", "frequency"),
                       ("Seasonal Adjustment", "seasonal")):
        if key in meta:
            continue
        m = re.search(rf"\b{label}:\s*(.+?)\s*(?=\b(?:{stop}):|$)", flat)
        if m:
            meta[key] = m.group(1).strip(" ,")[:200]
    units = meta.get("units") or ""
    m = re.match(r"(.*?),\s*((?:Not )?Seasonally Adjusted.*)$", units, re.I)
    if m:                                  # на странице: «U.S. Dollars per Metric Ton, Not Seasonally Adjusted»
        meta["units"] = m.group(1)
        meta.setdefault("seasonal", m.group(2))
    if meta.get("frequency"):
        meta["frequency"] = re.split(r"\s{2,}|\s+(?=\d)", meta["frequency"])[0][:60]
    return meta


def parse_csv(text, sid) -> list[tuple[dt.date, float]]:
    """observation_date,PFISHUSDM / DATE,VALUE → [(дата, число)]; пропуски («.» или пусто) — пропускаются."""
    lines = [x.strip() for x in (text or "").splitlines() if x.strip()]
    if not lines or not re.match(r"^\W*(observation_date|date)\b", lines[0], re.I):
        raise E.StructureError(f"FRED прислал по ряду {sid} не таблицу с данными")
    out = []
    for line in lines[1:]:
        parts = [x.strip().strip('"') for x in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            d = dt.date.fromisoformat(parts[0][:10])
            v = float(parts[1])
        except ValueError:
            continue
        out.append((d, v))
    return out


# Как спрашивать FRED. Его защита иногда «подвешивает» запросы программ (ответа нет до тайм-аута), причём
# по-разному: одним мешает заголовок «как браузер», другим — его отсутствие. Поэтому — по очереди, с коротким
# ожиданием, последним — встроенный браузер (как Chrome). Сработавший способ запоминается (cache/fred_way.txt).
WAYS = ["plain", "curl", "browser_headers", "browser"]
HEADERS = {"plain": {}, "curl": {"User-Agent": "curl/8.9.1", "Accept": "*/*"}, "browser_headers": None}
QUICK = (8, 20)                  # секунды: соединиться, ждать ответа
_way = {}


def _way_file(ctx):
    paths = getattr(ctx, "paths", None)
    return paths.cache / "fred_way.txt" if paths is not None else None


def _order(ctx) -> list:
    best = _way.get("ok")
    f = _way_file(ctx)
    if best is None and f is not None:
        try:
            best = f.read_text(encoding="utf-8").strip()
        except OSError:
            best = None
    return ([best] if best in WAYS else []) + [w for w in WAYS if w != best]


def _remember(ctx, way):
    if _way.get("ok") == way:
        return
    _way["ok"] = way
    f = _way_file(ctx)
    if f is not None:
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(way, encoding="utf-8")
        except OSError:
            pass


def _browser(ctx):
    b = getattr(ctx, "browser", None)
    try:
        return b if b is not None and b.available() else None
    except Exception:  # noqa: BLE001
        return None


def looks_like_csv(text) -> bool:
    return bool(re.match(r"^\W*(observation_date|date)\b", (text or "").lstrip()[:100], re.I))


def get(url, ctx, page=False, ways=None):
    """(status, текст) с FRED — первым способом, который дал ответ. page=True — страница (для браузера —
    get_html), иначе файл (download). Нет ответа ни одним способом — HubError(NETWORK)."""
    tried = []
    for way in ways or _order(ctx):
        try:
            if way == "browser":
                b = _browser(ctx)
                if b is None:
                    continue
                if page:
                    text, _final = b.get_html(url, timeout_ms=60000, offscreen=True)
                    status = 200
                else:
                    data, _final, ct = b.download(url, headless=False, timeout_ms=60000)
                    status, text = 200, W.decode(data, ct)
            else:
                status, ct, data, _final = W.http_get(url, ctx, timeout=QUICK, headers=HEADERS[way])
                text = W.decode(data, ct)
        except E.HubError as e:
            tried.append(f"{way}: {e}")
            continue
        except Exception as e:  # noqa: BLE001 — тайм-аут, обрыв: пробуем следующий способ
            tried.append(f"{way}: {type(e).__name__}: {e}"[:300])
            continue
        if status in (400, 404) or (status == 200 and not W.detect_block(status, text)
                                    and (page or looks_like_csv(text))):
            if status == 200:
                _remember(ctx, way)
            return status, text
        tried.append(f"{way}: ответ {status}")
    raise E.HubError(E.NETWORK, "FRED не ответил хабу (ни обычным запросом, ни через встроенный браузер). "
                                "Если сайт открывается в Chrome — нажмите «Отправить Claude»",
                     detail="\n".join(tried))


def fetch_meta(sid, ctx) -> dict:
    """Метаданные ряда; сбой — пустой словарь (ряд грузится и без них)."""
    first = _order(ctx)[0]                      # способ, которым уже получен CSV, и браузер — без долгого перебора
    ways = [first] + (["browser"] if first != "browser" else [])
    for url in (page_url(sid), f"{BASE}/data/{sid}.txt"):
        try:
            status, text = get(url, ctx, page=True, ways=ways)
        except E.HubError:
            continue
        if status == 200:
            meta = parse_meta(text, sid)
            if meta.get("units") or meta.get("frequency"):
                return meta
    return {}


class Fred(Source):
    kind = "fred"
    title = "FRED (ФРС Сент-Луиса)"
    snapshot = False

    def describe(self, params):
        ids = params.get("ids") or []
        meta = params.get("meta") or {}
        return "FRED: " + "; ".join(f"{(meta.get(s) or {}).get('title') or s} ({s})" for s in ids)

    def host(self, params):
        return HOST

    def open_url(self, params):
        ids = params.get("ids") or []
        return page_url(ids[0]) if ids else BASE

    def timeout(self, params):
        return 60 + 60 * len(params.get("ids") or [])

    def _csv(self, sid, ctx) -> str:
        status, text = get(csv_url(sid), ctx)
        if status in (400, 404):
            raise E.StructureError(f"На FRED нет ряда с кодом {sid} — проверьте код на странице ряда")
        return text

    def fetch(self, params, ctx) -> FetchResult:
        ids = [s for s in (params.get("ids") or []) if ID_RX.match(str(s))]
        if not ids:
            raise E.StructureError("В показателе FRED не указан код ряда")
        metas = dict(params.get("meta") or {})
        res = FetchResult(url=self.open_url(params))
        raw = []
        for i, sid in enumerate(ids):
            if i:
                time.sleep(PAUSE_S)
            self.say(ctx, f"FRED: {sid} ({i + 1} из {len(ids)})")
            text = self._csv(sid, ctx)
            raw.append(text)
            points = parse_csv(text, sid)
            meta = metas.get(sid) or {}
            per = periodicity_of(meta.get("frequency"))
            if per is None:
                from ._dataset import guess_periodicity
                per = guess_periodicity([d for d, _v in points])
            key = f"fred|{sid}"
            name = meta.get("title") or sid
            if meta.get("seasonal") and "not " not in meta["seasonal"].lower():
                name += " (SA)"
            res.series.append(SeriesInfo(
                key=key, name=name[:200], unit=meta.get("units") or "", periodicity=per, url=page_url(sid),
                code=f"FRED_{sid}"[:40],
                dims=[("Показатель", meta.get("title") or sid), ("Код FRED", sid)]
                + ([("Сезонность", meta["seasonal"])] if meta.get("seasonal") else []),
                date_rule=("дата FRED (день)" if per in (P.DAY, P.WEEK) else "начало периода в FRED → конец периода")))
            for d, v in points:
                end = d if per in (P.DAY, P.WEEK) else P.end_of_period(per, d)
                res.obs.append(Obs(key=key, period_end=end, period_label=P.label(per, end), value=v,
                                   source_date=d.isoformat()))
            if not points:
                res.warnings.append(f"FRED не вернул ни одного значения по ряду {sid}")
        res.raw, res.raw_ext = "\n\n".join(raw).encode("utf-8"), "csv"
        return res
