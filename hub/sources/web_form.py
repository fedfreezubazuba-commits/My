"""
«Форма на сайте» (web_form) — данные, которые сайт выдаёт только после заполнения формы
(таможня КНР stats.customs.gov.cn: импорт/экспорт, код товара, страна, год и месяцы).

Пользователь один раз заполняет форму во встроенном браузере и нажимает поиск; хаб запоминает запрос
(_forms) и дальше повторяет его сам — через тот же встроенный браузер (сайт открывается как у человека,
со своими скриптами), по одному запросу на год (или месяц), с паузами между запросами.

Параметры показателя:

    url: http://stats.customs.gov.cn/              # страница, с которой начинается работа с сайтом
    request: {method: GET, url: "...queryDataListEn?...", body: "", content_type: "", frame_url: "..."}
    period: {kind: year_months, year: year, start: startMonth, end: endMonth}
    from_year: 2020
    by: year                                       # year — запрос на год (в ответе есть месяц); month — на месяц
    values: {outerValue1: "27111100"}              # поля, которые пользователь поменял в окне настройки
    table: {title: "", index: 0}
    periodicity: month
    code_prefix: CUSTOMS
"""
import datetime as dt
import re
from urllib.parse import urlparse

from ..core import errors as E
from ..core import periods as P
from ..core.config import slug
from . import _forms as F
from .base import FetchResult, Obs, SeriesInfo, Source

PAUSE_S = 4            # пауза между запросами к сайту (сайты с формами обычно медленные и не любят спешки)
TITLES = {"stats.customs.gov.cn": "Таможня Китая"}


def prefix_for(url) -> str:
    """stats.customs.gov.cn → CUSTOMS; www.example.com → EXAMPLE."""
    parts = [p for p in urlparse(url).netloc.lower().split(":")[0].split(".")
             if p not in ("www", "stats", "data", "gov", "com", "org", "net", "co", "edu") and len(p) > 2]
    return re.sub(r"[^A-Z0-9]", "", (parts[0] if parts else "WEB").upper()) or "WEB"


def requests_for(params, today: dt.date, since: dt.date | None = None):
    """[(запрос, начало, конец)] — что спросить у сайта."""
    req = params["request"]
    flds = F.fields(req)
    fixed = {**F.page_size_values(flds), **(params.get("values") or {})}
    period = params.get("period")
    if not period:
        return [(F.with_fields(req, fixed), None, None)]
    out = []
    for vals, a, b in F.chunks(period, params.get("from_year") or today.year, today, params.get("by", "year"), since):
        out.append((F.with_fields(req, {**fixed, **vals}), a, b))
    return out


class WebForm(Source):
    kind = "web_form"
    title = "Форма на сайте"
    snapshot = False

    def describe(self, params):
        return f"Форма на сайте {urlparse(params.get('url') or params['request']['url']).netloc}"

    def host(self, params):
        return urlparse(params["request"]["url"]).netloc or "web_form"

    def open_url(self, params):
        return params.get("url") or params["request"].get("frame_url") or ""

    def timeout(self, params):
        n = len(requests_for(params, dt.date.today()))
        return min(60 + n * (90 + PAUSE_S), 4 * 3600)

    def fetch(self, params, ctx) -> FetchResult:
        today = (ctx.now if ctx else dt.datetime.now()).date()
        since = None
        if ctx is not None and ctx.last_period and not ctx.full_reload:
            # дозагрузка: последний загруженный период и всё после него (сайт мог уточнить последние месяцы)
            lp = ctx.last_period
            last = lp if isinstance(lp, dt.date) else (P.label_end(lp) or today)
            since = dt.date(last.year, max(1, last.month - 2), 1)
        plan = requests_for(params, today, since)
        if not plan:                           # январь: прошлый год уже загружен, а новый месяц ещё не кончился
            return FetchResult(url=params.get("url") or "", unchanged=True, partial=True)
        replay = (ctx.clients.get("web_form") if ctx and ctx.clients else None)
        if replay is None:
            if ctx is None or ctx.browser is None or not ctx.browser.available():
                raise E.HubError(E.INTERNAL, "Для «Формы на сайте» нужен встроенный браузер — запустите «Установка.bat»")
            replay = ctx.browser.replay
        self.say(ctx, f"Запросов к сайту: {len(plan)} (по одному на {'год' if params.get('by', 'year') == 'year' else 'месяц'})")
        answers = replay(params.get("url") or params["request"].get("frame_url") or params["request"]["url"],
                         [r for r, _a, _b in plan], pause_s=PAUSE_S,
                         progress=lambda i, msg=None: self.say(ctx, msg or f"Запрос {i} из {len(plan)}"),
                         interactive=bool(getattr(ctx, "interactive", False)))
        res = FetchResult(url=params.get("url") or "", partial=since is not None)
        seen = {}
        raw = []
        for (req, a, b), text in zip(plan, answers):
            raw.append(f"<!-- {req['method']} {req['url']} {req.get('body') or ''} -->\n{text[:200000]}")
            table = F.result_table(text, params.get("table"))
            if table is None:
                if a is None:
                    raise E.StructureError("В ответе сайта нет таблицы с данными — возможно, сайт изменил форму")
                res.warnings.append(f"За {a:%m.%Y}–{b:%m.%Y} сайт не вернул таблицу (данных ещё нет?)")
                continue
            self._rows(table, params, a, b, res, seen)
        res.raw, res.raw_ext = "\n\n".join(raw).encode("utf-8"), "html"
        if not res.obs and not res.warnings:
            raise E.StructureError("Сайт ответил, но в таблице нет чисел")
        return res

    def _rows(self, table, params, a, b, res, seen):
        lay = F.layout(table)
        head = lay["head"]
        if not lay["values"]:
            raise E.StructureError("В таблице ответа не найден столбец с числами")
        prefix = params.get("code_prefix") or prefix_for(params["request"]["url"])
        per = params.get("periodicity") or (P.MONTH if lay["date"] is not None or params.get("by") == "month"
                                            else P.YEAR if a and b and (a.month, b.month) == (1, 12) else P.MONTH)
        for r in lay["rows"]:
            if lay["date"] is not None:
                end = F.month_of(r[lay["date"]])
                src = r[lay["date"]]
            else:
                end, src = b, (f"{a:%Y-%m}–{b:%Y-%m}" if a else None)
            if end is None:
                continue                       # строка «Итого» без даты и т. п.
            end = P.end_of_period(per, end) if per in P.ORDER else end
            labels = [(head[j], r[j]) for j in lay["labels"] if r[j]]
            label = " | ".join(v for _h, v in labels) or "Значение"
            row_unit = r[lay["unit"]] if lay["unit"] is not None else ""
            for j in lay["values"]:
                v = P.parse_number(r[j]) if r[j] else None
                if v is None:
                    continue
                key = f"{label}|{head[j]}"
                if key not in seen:
                    seen[key] = SeriesInfo(
                        key=key, name=f"{label} | {head[j]}"[:200], unit=F.unit_for(head[j], row_unit),
                        periodicity=per, code=f"{prefix}_{slug(label, 18)}_{slug(head[j], 10)}".upper(),
                        url=params.get("url") or "", dims=labels + [("Показатель", head[j])],
                        date_rule="месяц из ответа сайта → конец месяца" if lay["date"] is not None
                        else "период запроса → конец периода")
                    res.series.append(seen[key])
                res.obs.append(Obs(key=key, period_end=end, period_label=P.label(per, end), value=v, source_date=src))
