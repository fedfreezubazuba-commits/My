"""
Общий интерфейс источников (раздел 8.1 ТЗ).

Источник получает параметры показателя и возвращает FetchResult: список рядов
(SeriesInfo) и наблюдений (Obs) в длинном формате, сырые байты для архива и метаданные.

Код ряда источник только предлагает (SeriesInfo.code). Окончательный код присваивает
хранилище один раз — по паре (показатель, SeriesInfo.key) — и больше не меняет.
"""
import datetime as dt
from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass
class SeriesInfo:
    key: str                      # стабильная идентичность ряда внутри показателя
    name: str
    unit: str = ""
    periodicity: str = ""         # day | week | month | quarter | halfyear | year | snapshot
    code: str = ""                # предлагаемый код (латиница, цифры, _)
    url: str = ""
    date_rule: str = ""           # как получена «Дата» (для справочника ряда)
    dims: list = field(default_factory=list)   # разрезы отдельными полями: [("Регион", "Российская Федерация"), …]


@dataclass
class Obs:
    key: str                      # SeriesInfo.key
    period_end: dt.date
    period_label: str
    value: float | None
    source_date: str | None = None


@dataclass
class FetchResult:
    series: list = field(default_factory=list)
    obs: list = field(default_factory=list)
    raw: bytes | None = None
    raw_ext: str = "bin"
    url: str = ""
    source_updated: str | None = None
    warnings: list = field(default_factory=list)
    unchanged: bool = False       # источник сам знает, что нового нет (например, папка «Входящие» пуста)
    partial: bool = False         # загружена только свежая часть истории (дозагрузка) — не сравнивать объём
    next_release: str | None = None   # когда источник обещает следующее обновление («2026-11-20» или с временем)
    release_note: str = ""            # что источник пишет о сроках («ежемесячно, на 20-й рабочий день…»)
    timing_text: str = ""             # куски страницы про сроки, если срок разобрать не удалось (для Claude)

    def describe(self) -> str:
        """«3 ряда, месячные, 2010–2025, последнее значение 15,8 % на 30.09.2025»."""
        from ..core.periods import PERIODICITY_RU
        if not self.obs:
            return "Данных не получено"
        n = len({o.key for o in self.obs if o.value is not None})
        pers = sorted({s.periodicity for s in self.series if s.periodicity})
        dates = [o.period_end for o in self.obs if o.value is not None]
        if not dates:
            return "Данных не получено"
        a, b = min(dates), max(dates)
        last = max((o for o in self.obs if o.value is not None), key=lambda o: o.period_end)
        unit = next((s.unit for s in self.series if s.key == last.key), "")
        per = ", ".join(PERIODICITY_RU.get(p, p) for p in pers)
        years = f"{a.year}–{b.year}" if a.year != b.year else f"{a.year}"
        return (f"{plural(n, 'ряд', 'ряда', 'рядов')}, {per}, {years}, последнее значение "
                f"{fmt_num(last.value)}{(' ' + unit) if unit else ''} на {last.period_end:%d.%m.%Y}")


def plural(n, one, few, many):
    n10, n100 = n % 10, n % 100
    word = one if n10 == 1 and n100 != 11 else few if 2 <= n10 <= 4 and not 12 <= n100 <= 14 else many
    return f"{n} {word}"


def fmt_num(v) -> str:
    if v is None:
        return "—"
    s = f"{v:,.4f}".rstrip("0").rstrip(".")
    return s.replace(",", " ").replace(".", ",")


class Context:
    """Что источнику дают снаружи: пути, журнал, браузер, «сессия» запросов, флаг остановки."""

    def __init__(self, paths=None, log=None, settings=None, clients=None, browser=None, now=None, dry_run=False):
        self.dry_run = dry_run           # пробная загрузка: ничего не перемещать и не менять
        self.interactive = False         # человек у компьютера (обновление по кнопке): можно показать ему окно
        self.last_period = None          # последний загруженный период показателя (для дозагрузки)
        self.full_reload = True          # нужна полная перезагрузка истории
        self.progress = None             # progress("текст") — что сейчас происходит (для окна)
        self.paths = paths
        self.log = log
        self.settings = settings or {}
        self.clients = clients or {}     # подмена клиентов в тестах: {"cbr": FakeCBR(), ...}
        self.browser = browser           # hub.sources.browser.Browser или None
        self.now = now or dt.datetime.now()

    def info(self, msg):
        if self.log:
            self.log.info(msg)


class Source:
    """Базовый класс источника. Подклассы кладутся в hub/sources/ и регистрируются сами."""

    kind = ""            # короткое имя: fedstat, cbr, web_value…
    title = ""           # как источник называется для пользователя
    snapshot = False     # True — история строится из снимков (проверять ежедневно)

    def fetch(self, params, ctx) -> FetchResult:
        raise NotImplementedError

    def check_updated(self, params, ctx):
        """Дешёвая проверка свежести: дата обновления в источнике или None, если неизвестно."""
        return None

    def describe(self, params) -> str:
        return self.title

    def default_schedule(self, params) -> str:
        return "auto"      # для снимков «автоматически» = раз в день

    def host(self, params) -> str:
        """Хост для очереди запросов: к одному хосту — строго последовательно."""
        url = params.get("url") or ""
        return urlparse(url).netloc or self.kind

    def default_checks(self, params) -> dict:
        return {"max_jump_pct": 50} if self.snapshot else {}

    def open_url(self, params) -> str:
        return params.get("url") or ""

    def incremental(self, params) -> bool:
        """Умеет ли источник дозагрузку (только свежие периоды, а всю историю — раз в N дней)."""
        return False

    def timeout(self, params) -> int:
        """Сколько секунд можно ждать загрузку (больше — для источников, которые грузят частями)."""
        return 0

    def say(self, ctx, text):
        if ctx is not None and getattr(ctx, "progress", None):
            try:
                ctx.progress(text)
            except Exception:  # noqa: BLE001
                pass
        if ctx is not None:
            ctx.info(text)
