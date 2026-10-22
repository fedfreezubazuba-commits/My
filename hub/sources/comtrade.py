"""
ООН — UN Comtrade (comtrade). Внешняя торговля стран по товарам и партнёрам, по месяцам, из отчётов самих
стран (Китай отчитывается данными своей таможни). Бесплатный открытый доступ без регистрации и ключа
(«public preview»): до 500 строк на запрос — для одного товара и одной пары стран этого хватает с запасом.

Запасной источник для таможни Китая: без формы и без проверки «я не робот», но код товара — 6 знаков
(271111 — сжиженный природный газ), а данные появляются позже, чем на сайте таможни.

Параметры показателя:

    reporter: 156          # кто отчитывается (Китай); коды стран — COUNTRIES
    partner: 643           # страна-партнёр (Россия); 0 — весь мир
    flow: M                # M — импорт, X — экспорт
    cmd: "271111"          # код товара ТН ВЭД / HS (2, 4 или 6 знаков)
    from_year: 2017
"""
import datetime as dt
import json
import time
from urllib.parse import urlencode

from ..core import errors as E
from ..core import periods as P
from . import _web as W
from .base import FetchResult, Obs, SeriesInfo, Source

BASE = "https://comtradeapi.un.org/public/v1/preview/C/M/HS"
HOST = "comtradeapi.un.org"
PAUSE_S = 2            # пауза между запросами (у открытого доступа есть ограничение на частоту)
COUNTRIES = [(156, "Китай"), (643, "Россия"), (0, "Весь мир"), (699, "Индия"), (392, "Япония"),
             (410, "Республика Корея"), (842, "США"), (276, "Германия"), (792, "Турция"), (398, "Казахстан"),
             (36, "Австралия"), (634, "Катар"), (458, "Малайзия"), (360, "Индонезия"), (528, "Нидерланды"),
             (682, "Саудовская Аравия"), (784, "ОАЭ"), (76, "Бразилия")]
FLOWS = {"M": "импорт", "X": "экспорт"}
MEASURES = [("netWgt", "Вес нетто", "кг", "KG"), ("primaryValue", "Стоимость", "USD", "USD")]
# + «Средняя цена», USD/т = стоимость / вес нетто × 1000 — считается, если есть и то и другое


def country(code) -> str:
    return next((n for c, n in COUNTRIES if c == int(code)), f"страна {code}")


def query_url(params, months) -> str:
    q = {"reportercode": params["reporter"], "flowCode": params.get("flow", "M"),
         "period": ",".join(f"{m:%Y%m}" for m in months), "cmdCode": params["cmd"],
         "partnerCode": params.get("partner", 0), "partner2Code": 0, "motCode": 0, "customsCode": "C00",
         "includeDesc": "true"}
    return f"{BASE}?{urlencode(q)}"


def month_chunks(from_year, today: dt.date, since: dt.date | None = None) -> list[list[dt.date]]:
    """По году (не больше 12 месяцев в запросе), по прошлый месяц включительно."""
    out = []
    for y in range(int(from_year), today.year + 1):
        last = 12 if y < today.year else today.month - 1
        months = [dt.date(y, m, 1) for m in range(1, last + 1) if not since or dt.date(y, m, 1) >= since]
        if months:
            out.append(months)
    return out


class Comtrade(Source):
    kind = "comtrade"
    title = "ООН (UN Comtrade)"
    snapshot = False

    def describe(self, params):
        return (f"UN Comtrade: {country(params['reporter'])}, {FLOWS.get(params.get('flow', 'M'), '')} "
                f"{params['cmd']} — {country(params.get('partner', 0))}")

    def host(self, params):
        return HOST

    def open_url(self, params):
        return "https://comtradeplus.un.org/TradeFlow"

    def timeout(self, params):
        n = len(month_chunks(params.get("from_year") or dt.date.today().year, dt.date.today()))
        return min(120 + n * 60, 3600)

    def _get(self, url, ctx):
        status, _ct, data, _final = W.http_get(url, ctx, timeout=90)
        text = W.decode(data)
        cat = W.detect_block(status, text)
        if cat:
            raise E.HubError(cat, f"UN Comtrade не выдал данные (ответ {status})")
        if status != 200:
            raise E.HubError(E.NETWORK, f"UN Comtrade ответил ошибкой {status}")
        try:
            return json.loads(text)
        except ValueError:
            raise E.StructureError("UN Comtrade прислал не данные, а непонятный ответ") from None

    def _rows(self, params, months, ctx):
        js = self._get(query_url(params, months), ctx)
        if js.get("error") and len(months) > 1:          # не принял несколько месяцев сразу — по одному
            out = []
            for m in months:
                time.sleep(PAUSE_S)
                out += self._rows(params, [m], ctx)
            return out
        if js.get("error"):
            raise E.StructureError(f"UN Comtrade: {js['error']}")
        return js.get("data") or []

    def fetch(self, params, ctx) -> FetchResult:
        today = (ctx.now if ctx else dt.datetime.now()).date()
        since = None
        if ctx is not None and ctx.last_period and not ctx.full_reload:
            lp = ctx.last_period if isinstance(ctx.last_period, dt.date) else P.label_end(ctx.last_period)
            if lp:
                # страны уточняют последние месяцы — берём их заново
                y, m = lp.year, lp.month - 3
                while m < 1:
                    y, m = y - 1, m + 12
                since = dt.date(y, m, 1)
        chunks = month_chunks(params.get("from_year") or today.year - 5, today, since)
        res = FetchResult(url=self.open_url(params), partial=since is not None)
        if not chunks:
            res.unchanged = True
            return res
        flow = params.get("flow", "M")
        rep, par, cmd = int(params["reporter"]), int(params.get("partner", 0)), str(params["cmd"])
        seen, raw, got = {}, [], set()
        for i, months in enumerate(chunks):
            if i:
                time.sleep(PAUSE_S)
            self.say(ctx, f"UN Comtrade: {months[0]:%Y}, запрос {i + 1} из {len(chunks)}")
            rows = self._rows(params, months, ctx)
            raw.append(json.dumps(rows[:50], ensure_ascii=False))
            for r in rows:
                if (r.get("partner2Code") or 0) != 0 or (r.get("motCode") or 0) != 0 \
                        or (r.get("customsCode") or "C00") != "C00":
                    continue
                y, m = r.get("refYear"), r.get("refMonth")
                if not (y and m):
                    per = str(r.get("period") or "")
                    y, m = (int(per[:4]), int(per[4:6])) if len(per) == 6 else (None, None)
                if not (y and m) or (y, m) in got:
                    continue
                got.add((y, m))
                end = P.month_end(int(y), int(m))
                qty_unit = r.get("qtyUnitAbbr") or ""
                measures = list(MEASURES)
                if r.get("qty") is not None and qty_unit and qty_unit.lower() not in ("kg", "n/a", "na"):
                    measures.append(("qty", "Количество", qty_unit, "QTY"))
                if r.get("netWgt") and r.get("primaryValue") is not None:
                    r = dict(r, _price=r["primaryValue"] / r["netWgt"] * 1000)
                    measures.append(("_price", "Средняя цена", "USD/т", "PRICE"))
                for field, word, unit, suffix in measures:
                    v = r.get(field)
                    if v is None:
                        continue
                    key = f"{flow}|{rep}|{par}|{cmd}|{field}"
                    if key not in seen:
                        goods = f"{cmd} {r.get('cmdDesc') or ''}".strip()
                        seen[key] = SeriesInfo(
                            key=key, unit=unit, periodicity=P.MONTH, url=self.open_url(params),
                            name=f"{country(rep)}: {FLOWS[flow]} {cmd} — {country(par)} | {word}"[:200],
                            code=f"COMTRADE_{flow}_{rep}_{par}_{cmd}_{suffix}",
                            dims=[("Отчитывающаяся страна", country(rep)), ("Поток", FLOWS[flow]),
                                  ("Страна-партнёр", country(par)), ("Товар", goods[:120]), ("Показатель", word)],
                            date_rule="месяц отчёта → конец месяца")
                        res.series.append(seen[key])
                    res.obs.append(Obs(key=key, period_end=end, period_label=P.label(P.MONTH, end),
                                       value=float(v), source_date=f"{int(y)}{int(m):02d}"))
        res.raw, res.raw_ext = ("[" + ",".join(raw) + "]").encode("utf-8"), "json"
        if not res.obs and not res.partial:
            res.warnings.append("UN Comtrade не вернул ни одного значения — проверьте коды стран и товара "
                                "(товар — 2, 4 или 6 знаков) и годы")
        return res
