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
import xml.etree.ElementTree as ET

from ..core import periods as P
from ..core.errors import StructureError
from .base import FetchResult, Obs, SeriesInfo, Source
from .fedstat_api import BASE, Fedstat

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

    def fetch(self, params, ctx) -> FetchResult:
        fs = self.client(ctx)
        ind_id = str(params["id"])
        info = fs.indicator(ind_id)
        # download() снова читает страницу показателя — отдаём ему уже прочитанную
        orig = fs.indicator
        fs.indicator = lambda _id: info
        warnings = []
        try:
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
            if params.get("years_from") and YEARS_FILTER in info["filters"]:
                yf = int(params["years_from"])
                select[YEARS_FILTER] = [v for v, t in info["filters"][YEARS_FILTER]["values"].items()
                                        if re.match(r"\d{4}", t.strip()) and int(t.strip()[:4]) >= yf]
            if not info.get("token"):
                raise StructureError("На странице показателя ЕМИСС не найден токен выгрузки")
            xml = fs.download(ind_id, "sdmx", select)
        finally:
            fs.indicator = orig
        res = FetchResult(url=self.open_url(params), raw=xml, raw_ext="xml", warnings=warnings)
        series = sdmx_series(xml)
        varying = self._varying_dims(series)
        kinds_seen = set()
        parsed = []
        for key, key_names, attrs, obs in series:
            for time_text, value in obs:
                kind, end, label = fedstat_period(time_text, attrs.get("PERIOD", ""))
                kinds_seen.add(kind)
                parsed.append((key, key_names, attrs, kind, end, label, _num(value), time_text))
        known = {}
        for key, key_names, attrs, kind, end, label, value, time_text in parsed:
            if value is None:
                continue
            ident = "|".join(f"{c}={v}" for c, v in key) + f"|P={kind}"
            if ident not in known:
                per, note = KINDS[kind]
                parts = [info["title"]] + [str(key_names[d]).strip() for d in key_names if d in varying]
                if note and (len(kinds_seen) > 1 or kind in ("MC", "S", "SY")):
                    parts.append(note)
                code = f"FEDSTAT_{ind_id}_" + "_".join(str(v) for _c, v in key if _c and v) + f"_{kind}"
                known[ident] = SeriesInfo(key=ident, name=" | ".join(p for p in parts if p),
                                          unit=(attrs.get("EI") or info.get("unit") or "").strip(),
                                          periodicity=per, code=code, url=res.url,
                                          date_rule=f"период ЕМИСС → конец периода ({per})")
                res.series.append(known[ident])
            res.obs.append(Obs(key=ident, period_end=end, period_label=label, value=value,
                               source_date=f"{time_text} {attrs.get('PERIOD', '')}".strip()))
        return res

    @staticmethod
    def _varying_dims(series):
        vals = {}
        for _key, key_names, _attrs, _obs in series:
            for d, v in key_names.items():
                vals.setdefault(d, set()).add(v)
        return {d for d, vs in vals.items() if len(vs) > 1}
