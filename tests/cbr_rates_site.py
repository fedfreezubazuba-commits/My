"""Устройство страницы «Динамика официального курса» cbr.ru/currency_base/dynamics/: форма с датами «с … по …»
(UniDbQuery.From / UniDbQuery.To, дд.мм.гггг), ответ — таблица «Дата | Единиц | Курс» по рабочим дням.
Курс выдуман: 80 + день года / 100."""
import datetime as dt
from urllib.parse import parse_qs, urlsplit

URL = ("https://cbr.ru/currency_base/dynamics/?UniDbQuery.Posted=True&UniDbQuery.so=1&UniDbQuery.mode=1"
       "&UniDbQuery.date_req1=&UniDbQuery.date_req2=&UniDbQuery.VAL_NM_RQ=R01235"
       "&UniDbQuery.From=17.10.2026&UniDbQuery.To=24.10.2026")
REQ = {"method": "GET", "url": URL, "body": "", "content_type": "", "headers": {}, "frame_url": ""}


def rate(d: dt.date) -> str:
    return f"{80 + d.timetuple().tm_yday / 100:.4f}".replace(".", ",")


def page(url, today: dt.date) -> str:
    q = {k: v[0] for k, v in parse_qs(urlsplit(url).query, keep_blank_values=True).items()}
    a = dt.datetime.strptime(q["UniDbQuery.From"], "%d.%m.%Y").date()
    b = min(dt.datetime.strptime(q["UniDbQuery.To"], "%d.%m.%Y").date(), today)
    rows = []
    d = b
    while d >= a:                                  # как на сайте: новые даты сверху, только рабочие дни
        if d.weekday() < 5:
            rows.append(f"<tr><td>{d:%d.%m.%Y}</td><td>1</td><td>{rate(d)}</td></tr>")
        d -= dt.timedelta(days=1)
    return ("<html><body><h1>Динамика официального курса заданной валюты</h1><table class=\"data\">"
            "<tr><th>Дата</th><th>Единиц</th><th>Курс</th></tr>" + "".join(rows) + "</table></body></html>")


def make_site(now):
    """Подмена browser.replay: сайт «живёт» в момент now["t"]."""
    def site(start_url, reqs, pause_s=0, progress=None, interactive=False):
        site.calls = [{k: v[0] for k, v in parse_qs(urlsplit(r["url"]).query).items()} for r in reqs]
        return [page(r["url"], now["t"].date()) for r in reqs]
    site.calls = []
    return site
