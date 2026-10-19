"""
ЕМИСС (fedstat.ru).

Обёртка над проверенным клиентом fedstat_api.Fedstat — CSRF-токен, POST-выгрузка SDMX,
паузы и повторы при 503 не меняются. Запросы к fedstat.ru идут строго последовательно
(это обеспечивает очередь по хостам в runner).

Параметры показателя:

    {id: 31074, select: {"0": ["1558883"], "33560": ["1558885", ...]}, years_from: 2015}

Измерения, которых нет в select, берутся целиком (так новые годы и периоды
подтягиваются сами). Годы задаются через years_from.

Период ЕМИСС («2024» + «январь» / «I квартал» / «январь-март» / «значение за год» /
«на 1 января») приводится к концу периода (раздел 9.6 ТЗ).
"""
import datetime as dt
import re
import time
import xml.etree.ElementTree as ET

from ..core import periods as P
from ..core.errors import HubError, StructureError, classify, human_message
from .base import FetchResult, Obs, SeriesInfo, Source
from .fedstat_api import BACKSLASH_U, BASE, Fedstat

NS = {"m": "http://www.SDMX.org/resources/SDMXML/schemas/v1_0/message",
      "s": "http://www.SDMX.org/resources/SDMXML/schemas/v1_0/structure",
      "g": "http://www.SDMX.org/resources/SDMXML/schemas/v1_0/generic"}
YEARS_FILTER = "3"
ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "1": 1, "2": 2, "3": 3, "4": 4}
YEAR_WORDS = ("значение показателя за год", "за год", "год", "годовое значение", "годовой",
              "в среднем за год", "среднегодовое значение", "")

# вид периода -> (периодичность, пояснение для названия ряда)
KINDS = {
    "M": (P.MONTH, ""),
    "MC": (P.MONTH, "нарастающим итогом с начала года"),
    "Q": (P.QUARTER, ""),
    "H": (P.HALF, ""),
    "Y": (P.YEAR, ""),
    "D": (P.DAY, ""),
    "S": (P.MONTH, "на 1-е число следующего месяца"),
    "SY": (P.YEAR, "на конец года"),
}


def fedstat_period(time_text, period_text):
    """(вид, конец периода, подпись) по году (Time) и периоду (атрибут PERIOD) ЕМИСС."""
    t = (time_text or "").strip()
    p = " ".join((period_text or "").lower().replace("ё", "е").split())
    d = P.parse_date(t) if re.search(r"\d{1,2}[./]\d{1,2}[./]\d{4}|\d{4}-\d{2}-\d{2}", t) else None
    if d:
        return "D", d, d.isoformat()
    m = re.match(r"(\d{4})(?:-(\d{1,2}))?$", t)
    if not m:
        raise StructureError(f"Не распознан год ЕМИСС «{time_text}»")
    year = int(m.group(1))
    if m.group(2):
        end = P.month_end(year, int(m.group(2)))
        return "M", end, P.label(P.MONTH, end)
    if p in YEAR_WORDS or p.startswith("значение показателя за год"):
        return "Y", P.month_end(year, 12), str(year)
    mon = P.month_from_word(p) if p in P.MONTHS_RU else None
    if mon:
        end = P.month_end(year, mon)
        return "M", end, P.label(P.MONTH, end)
    m = re.fullmatch(r"январь\s*[-–—]\s*([а-я]+)", p)
    if m and P.month_from_word(m.group(1)):
        end = P.month_end(year, P.month_from_word(m.group(1)))
        return "MC", end, P.label(P.MONTH, end)
    m = re.fullmatch(r"(iv|iii|ii|i|[1-4])\s*(?:-?й)?\s*квартал", p)
    if m:
        end = P.quarter_end(year, ROMAN[m.group(1)])
        return "Q", end, P.label(P.QUARTER, end)
    m = re.fullmatch(r"(ii|i|[12])\s*(?:-?е)?\s*полугодие", p)
    if m:
        end = P.month_end(year, 6 * ROMAN[m.group(1)])
        return "H", end, P.label(P.HALF, end)
    m = re.fullmatch(r"на (?:1|начало)\s+([а-я]+)", p)
    if m and P.month_from_word(m.group(1)):
        first = dt.date(year, P.month_from_word(m.group(1)), 1)
        end = first - dt.timedelta(days=1)
        if first.month == 1:
            return "SY", end, str(end.year)
        return "S", end, P.label(P.MONTH, end)
    if p in ("на конец года", "на 31 декабря", "на конец периода"):
        return "SY", P.month_end(year, 12), str(year)
    raise StructureError(f"Не распознан период ЕМИСС «{period_text}» (год {time_text})")


def sdmx_series(xml_bytes):
    """SDMX-ML 1.0 Generic → [(коды измерений, названия измерений, атрибуты, [(Time, value)])].

    Тот же разбор, что sdmx_to_rows клиента, но с сохранением исходных кодов —
    из них строится стабильный код ряда."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise StructureError(f"ЕМИСС вернул не SDMX: {e}")
    names, codes = {}, {}
    for cl in root.iter(f"{{{NS['s']}}}CodeList"):
        names[cl.get("id")] = cl.findtext("s:Name", "", NS)
        codes[cl.get("id")] = {c.get("value"): c.findtext("s:Description", "", NS) for c in cl.findall("s:Code", NS)}
    out = []
    for s in root.iter(f"{{{NS['g']}}}Series"):
        key = [(v.get("concept"), v.get("value")) for v in s.findall("g:SeriesKey/g:Value", NS)]
        key_names = {names.get(c, c): codes.get(c, {}).get(v, v) for c, v in key}
        attrs = {v.get("concept"): v.get("value") for v in s.findall("g:Attributes/g:Value", NS)}
        obs = []
        for o in s.findall("g:Obs", NS):
            ov = o.find("g:ObsValue", NS)
            obs.append((o.findtext("g:Time", "", NS), ov.get("value") if ov is not None else None))
        out.append((key, key_names, attrs, obs))
    return out


CHUNK_CELLS = 20000      # примерно столько значений просим за один запрос (дальше размер подбирается сам)


def clean(text) -> str:
    """«Тонна;^метрическая тонна (1000 кг)» → «Тонна; метрическая тонна (1000 кг)»."""
    s = str(text or "").replace(";^", "; ").replace("^", " ")
    return " ".join(s.split())


def is_unit_dim(name) -> bool:
    n = str(name).lower()
    return "единиц" in n and "измерен" in n or n in ("s_okei", "okei")


def _num(v):
    if v is None:
        return None
    s = str(v).strip().replace(" ", "").replace(" ", "").replace(",", ".")
    if s in ("", "-", "...", "…", "x", "х"):
        return None
    try:
        return float(s)
    except ValueError:
        raise StructureError(f"ЕМИСС вернул не число: {v!r}")


# --- срок следующего обновления (паспорт на странице показателя) --------------------------------
# Формат страницы fedstat.ru из облака проверить нельзя, поэтому разбор терпимый: ищем фразы вида
# «Дата очередного обновления: 20.11.2026» и «Срок предоставления: ежемесячно, на 25-й рабочий день
# после отчетного периода». Не нашлось — ничего страшного, хаб оценит срок по истории.
NEXT_RE = re.compile(
    r"(?:очередн\w*|следующ\w*|ближайш\w*|планов\w*|планируем\w*|предполагаем\w*|ожидаем\w*)\s+"
    r"(?:дат\w*\s+)?(?:обновлени\w*|публикаци\w*|актуализаци\w*|размещени\w*|предоставлени\w*)"
    r"[^0-9]{0,40}?(\d{1,2}\.\d{1,2}\.\d{4}|\d{4}-\d{2}-\d{2})", re.I)
NOTE_RE = re.compile(
    r"((?:срок\w*|периодичност\w*)\s+(?:предоставлени\w*|размещени\w*|публикаци\w*|обновлени\w*|"
    r"актуализаци\w*|распространени\w*)[^\n:]{0,60}):?\s*\n?\s*([^\n;]{3,160})", re.I)
WORKDAY_RE = re.compile(r"(\d{1,3})\s*-?\s*(?:й|го|ый|ой)?\s+рабоч\w*\s+д", re.I)
DAYNUM_RE = re.compile(r"(\d{1,2})\s*-?\s*(?:го\s+)?числ", re.I)


def page_text(html) -> str:
    """Текст страницы: каждая ячейка/блок — с новой строки (подпись и значение различимы)."""
    import html as H
    t = html.decode("utf-8", "replace") if isinstance(html, bytes) else str(html or "")
    t = re.sub(BACKSLASH_U, lambda m: chr(int(m.group(1), 16)), t)
    t = H.unescape(re.sub(r"<[^>]+>", "\n", t))
    t = re.sub(r"[ \t\r\f\v\xa0]+", " ", t)
    return re.sub(r"\s*\n\s*", "\n", t)


def release_from_page(html, last_end: dt.date | None, periodicity, today: dt.date):
    """→ (дата следующего обновления «ГГГГ-ММ-ДД» или None, текст о сроках или "")."""
    from ..core import workdays
    text = page_text(html)
    note = ""
    m = NOTE_RE.search(text)
    if m:
        note = (" ".join(m.group(1).split()) + ": " + " ".join(m.group(2).split()))[:200]
    for m in NEXT_RE.finditer(text):
        d = P.parse_date(m.group(1))
        if d and d >= today - dt.timedelta(days=3):
            return d.isoformat(), note
    if not note or not last_end or periodicity not in P.ORDER:
        return None, note
    # «на 25-й рабочий день после отчётного периода» / «20 числа месяца, следующего за отчётным»
    nxt = P.next_period_end(periodicity, last_end)
    low = note.lower()
    m = WORKDAY_RE.search(low)
    if m and "после" in low:
        return workdays.add_workdays(nxt, int(m.group(1))).isoformat(), note
    m = DAYNUM_RE.search(low)
    if m and 1 <= int(m.group(1)) <= 31:
        y, mo = (nxt.year + 1, 1) if nxt.month == 12 else (nxt.year, nxt.month + 1)
        return dt.date(y, mo, min(int(m.group(1)), P.month_end(y, mo).day)).isoformat(), note
    return None, note


class FedstatSource(Source):
    kind = "fedstat"
    title = "ЕМИСС (Росстат)"

    def client(self, ctx):
        return (ctx.clients.get("fedstat") if ctx else None) or Fedstat()

    def host(self, params):
        return "www.fedstat.ru"

    def describe(self, params):
        return f"ЕМИСС: показатель {params.get('id')}"

    def open_url(self, params):
        return f"{BASE}/indicator/{params.get('id')}"

    def timeout(self, params):
        return 3 * 3600          # большой показатель грузится частями по годам, с паузами

    def _select(self, info, params, warnings):
        select = {}
        for fid, vals in (params.get("select") or {}).items():
            fid = str(fid)
            f = info["filters"].get(fid)
            if f is None:
                warnings.append(f"У показателя больше нет измерения {fid}")
                continue
            ok = [str(v) for v in vals if str(v) in f["values"]]
            lost = [str(v) for v in vals if str(v) not in f["values"]]
            if lost:
                warnings.append(f"В измерении «{f['title']}» исчезли значения: {', '.join(lost)}")
            if not ok:
                raise StructureError(f"В измерении «{f['title']}» не осталось выбранных значений")
            select[fid] = ok
        return select

    @staticmethod
    def _years(info, params):
        f = info["filters"].get(YEARS_FILTER)
        if not f:
            return None
        years = [(int(t.strip()[:4]), v) for v, t in f["values"].items() if re.match(r"\d{4}", t.strip())]
        yf = int(params.get("years_from") or 0)
        return [v for y, v in sorted(years) if y >= yf]

    @staticmethod
    def cells_per_year(info, select):
        """Сколько значений приходится на один год: произведение числа выбранных значений измерений."""
        n = 1
        for fid, f in info["filters"].items():
            if fid == YEARS_FILTER:
                continue
            n *= max(1, len(select.get(fid) or f["values"]))
        return n

    def fetch(self, params, ctx) -> FetchResult:
        fs = self.client(ctx)
        ind_id = str(params["id"])
        page = {}
        orig_get = fs._get

        def remember_page(url, *a, **k):     # запросы те же; страницу запоминаем ради сроков обновления
            ct, body = orig_get(url, *a, **k)
            if "/indicator/" in url and "downloadData" not in url:
                page["html"] = body
            return ct, body
        fs._get = remember_page
        try:
            info = fs.indicator(ind_id)
        finally:
            fs._get = orig_get
        # download() снова читает страницу показателя — отдаём ему уже прочитанную
        orig = fs.indicator
        fs.indicator = lambda _id: info
        warnings = []
        partial = False
        try:
            select = self._select(info, params, warnings)
            if not info.get("token"):
                raise StructureError("На странице показателя ЕМИСС не найден токен выгрузки")
            years = self._years(info, params)
            last = getattr(ctx, "last_period", None) if ctx else None
            if years and last and not getattr(ctx, "full_reload", True):
                # дозагрузка: только последние годы (плюс предыдущий — там бывают пересмотры)
                recent = [v for v in years if int(info["filters"][YEARS_FILTER]["values"][v].strip()[:4]) >= last.year - 1]
                if recent and len(recent) < len(years):
                    years, partial = recent, True
            parts = self._download_in_parts(fs, ind_id, info, select, years, params, ctx)
        finally:
            fs.indicator = orig
        res = FetchResult(url=self.open_url(params), raw=parts[-1] if parts else b"", raw_ext="xml",
                          warnings=warnings, partial=partial)
        series = []
        for xml in parts:
            series += sdmx_series(xml)
        self._to_result(series, info, ind_id, res)
        if page.get("html"):
            try:
                self._release(page["html"], res, ctx)
            except Exception:  # noqa: BLE001 — срок обновления не обязателен
                pass
        return res

    @staticmethod
    def _release(html, res, ctx):
        dated = [o for o in res.obs if o.value is not None]
        if not dated:
            return
        per_of = {s.key: s.periodicity for s in res.series}
        best = min((per_of.get(o.key) for o in dated if per_of.get(o.key) in P.ORDER),
                   key=lambda p: P.typical_days(p), default=None)
        last = max((o.period_end for o in dated if per_of.get(o.key) == best), default=None)
        today = (getattr(ctx, "now", None) or dt.datetime.now()).date() if ctx else dt.date.today()
        res.next_release, res.release_note = release_from_page(html, last, best, today)

    def _download_in_parts(self, fs, ind_id, info, select, years, params, ctx):
        """Большие показатели — частями по годам. Размер части подбирается сам: при сбое части она
        делится пополам (вплоть до одного года), после удачи — следующая часть вдвое больше."""
        if not years:
            self.say(ctx, f"ЕМИСС: выгружаю показатель {ind_id}")
            return [fs.download(ind_id, "sdmx", select)]
        limit = int(params.get("chunk_cells") or CHUNK_CELLS)
        per_year = self.cells_per_year(info, select)
        size = max(1, min(len(years), limit // max(per_year, 1)))
        parts, i, fails = [], 0, 0
        while i < len(years):
            chunk = years[i:i + size]
            label = self._year_label(info, chunk)
            if size < len(years):
                self.say(ctx, f"ЕМИСС: показатель большой (≈{per_year * len(years):,} значений), загружаю частями: "
                              f"{label}".replace(",", " "))
            try:
                xml = fs.download(ind_id, "sdmx", {**select, YEARS_FILTER: chunk})
                if b"<" not in xml[:200]:
                    raise StructureError("ЕМИСС вернул пустой ответ")
                parts.append(xml)
                i += len(chunk)
                fails = 0
                size = min(size * 2, max(1, limit * 4 // max(per_year, 1)))
            except StructureError:
                raise
            except Exception as e:  # noqa: BLE001 — сервер не справился с объёмом: дробим
                code = getattr(e, "code", None)
                if code and 400 <= code < 500 and code not in (408, 429):
                    raise
                if size > 1:
                    size = max(1, size // 2)
                    self.say(ctx, f"ЕМИСС не отдал данные за {label} — пробую частями поменьше ({size} г.)")
                    time.sleep(getattr(fs, "pause", 2) * 2)
                    continue
                fails += 1
                if fails >= 2:
                    raise HubError(classify(e), f"ЕМИСС не отдаёт данные даже за один год ({label}): "
                                                f"{human_message(e)}. Попробуйте позже или выберите меньше значений")
                time.sleep(getattr(fs, "pause", 2) * 10)
        return parts

    @staticmethod
    def _year_label(info, chunk):
        vals = info["filters"].get(YEARS_FILTER, {}).get("values", {})
        ys = [vals.get(v, v).strip()[:4] for v in chunk]
        return ys[0] if len(ys) == 1 else f"{ys[0]}–{ys[-1]}"

    def _to_result(self, series, info, ind_id, res):
        varying = self._varying_dims(series)
        kinds_seen = set()
        parsed = []
        for key, key_names, attrs, obs in series:
            for time_text, value in obs:
                kind, end, label = fedstat_period(time_text, attrs.get("PERIOD", ""))
                kinds_seen.add(kind)
                parsed.append((key, key_names, attrs, kind, end, label, _num(value), time_text))
        known, seen_obs = {}, set()
        for key, key_names, attrs, kind, end, label, value, time_text in parsed:
            if value is None:
                continue
            ident = "|".join(f"{c}={v}" for c, v in key) + f"|P={kind}"
            if (ident, end) in seen_obs:
                continue                       # одна и та же точка в соседних частях выгрузки
            seen_obs.add((ident, end))
            if ident not in known:
                per, note = KINDS[kind]
                unit_dims = [d for d in key_names if is_unit_dim(d)]
                dims = [("Показатель", clean(info["title"]))]
                dims += [(clean(d), clean(key_names[d])) for d in key_names if d not in unit_dims]
                parts = [clean(info["title"])] + [clean(key_names[d]) for d in key_names
                                                  if d in varying and d not in unit_dims]
                if note and (len(kinds_seen) > 1 or kind in ("MC", "S", "SY")):
                    parts.append(note)
                    dims.append(("Вид значения", note))
                ei = clean(attrs.get("EI") or "")
                unit = ei if ei not in ("", "-", "—") else (clean(key_names[unit_dims[0]]) if unit_dims else
                                                          clean(info.get("unit") or ""))
                code = f"FEDSTAT_{ind_id}_" + "_".join(str(v) for _c, v in key if _c and v) + f"_{kind}"
                known[ident] = SeriesInfo(key=ident, name=" | ".join(p for p in parts if p), unit=unit,
                                          periodicity=per, code=code, url=res.url, dims=dims,
                                          date_rule=f"период ЕМИСС → конец периода ({per})")
                res.series.append(known[ident])
            res.obs.append(Obs(key=ident, period_end=end, period_label=label, value=value,
                               source_date=f"{time_text} {attrs.get('PERIOD', '')}".strip()))

    @staticmethod
    def _varying_dims(series):
        vals = {}
        for _key, key_names, _attrs, _obs in series:
            for d, v in key_names.items():
                vals.setdefault(d, set()).add(v)
        return {d for d, vs in vals.items() if len(vs) > 1}
