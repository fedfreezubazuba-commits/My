"""Устройство страницы «Динамика официального курса» cbr.ru/currency_base/dynamics/: форма с датами «с … по …»
(UniDbQuery.From / UniDbQuery.To, дд.мм.гггг), ответ — таблица «Дата | Единиц | Курс» по рабочим дням.
Курс выдуман: 80 + день года / 100."""
import datetime as dt
from urllib.parse import parse_qs, urlsplit

URL = ("https://cbr.ru/currency_base/dynamics/?UniDbQuery.Posted=True&UniDbQuery.so=1&UniDbQuery.mode=1"
       "&UniDbQuery.date_req1=&UniDbQuery.date_req2=&UniDbQuery.VAL_NM_RQ=R01235"
       "&UniDbQuery.From=17.10.2026&UniDbQuery.To=24.10.2026")
REQ = {"method": "GET", "url": URL, "body": "", "content_type": "", "headers": {}, "frame_url": ""}


CURRENCIES = [("R01235", "Доллар США", 80), ("R01239", "Евро", 90), ("R01375", "Юань", 11)]


def rate(d: dt.date, code="R01235") -> str:
    base = next((b for c, _n, b in CURRENCIES if c == code), 80)
    return f"{base + d.timetuple().tm_yday / 100:.4f}".replace(".", ",")


def page(url, today: dt.date) -> str:
    q = {k: v[0] for k, v in parse_qs(urlsplit(url).query, keep_blank_values=True).items()}
    a = dt.datetime.strptime(q["UniDbQuery.From"], "%d.%m.%Y").date()
    b = min(dt.datetime.strptime(q["UniDbQuery.To"], "%d.%m.%Y").date(), today)
    rows = []
    d = b
    while d >= a:                                  # как на сайте: новые даты сверху, только рабочие дни
        if d.weekday() < 5:
            rows.append(f"<tr><td>{d:%d.%m.%Y}</td><td>1</td><td>{rate(d, q.get('UniDbQuery.VAL_NM_RQ'))}</td></tr>")
        d -= dt.timedelta(days=1)
    opts = "".join(f'<option value="{c}"{" selected" if c == q.get("UniDbQuery.VAL_NM_RQ") else ""}>{n}</option>'
                   for c, n, _b in CURRENCIES)
    form = ('<form><label for="UniDbQuery_VAL_NM_RQ">Валюта</label><select id="UniDbQuery_VAL_NM_RQ" '
            f'name="UniDbQuery.VAL_NM_RQ"><option value="">Выберите</option>{opts}</select></form>')
    return ("<html><body><h1>Динамика официального курса заданной валюты</h1>" + form + "<table class=\"data\">"
            "<tr><th>Дата</th><th>Единиц</th><th>Курс</th></tr>" + "".join(rows) + "</table></body></html>")


def make_site(now):
    """Подмена browser.replay: сайт «живёт» в момент now["t"]."""
    def site(start_url, reqs, pause_s=0, progress=None, interactive=False):
        site.calls = [{k: v[0] for k, v in parse_qs(urlsplit(r["url"]).query).items()} for r in reqs]
        return [page(r["url"], now["t"].date()) for r in reqs]
    site.calls = []
    return site
