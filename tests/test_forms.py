"""«Форма на сайте»: запись запроса формы, поля периода, повтор по годам (образец — устройство таможни КНР)."""
import datetime as dt
import os
from urllib.parse import parse_qs, urlsplit

import pytest

from hub.sources import Context, _forms as F, get

import customs_site as CS

NOW = dt.datetime(2026, 10, 22, 12, 0)
LIST_URL = ("http://stats.customs.gov.cn/queryDataForEN/queryDataListEn?pageSize=10&iEType=1&currencyType=usd"
            "&year=2026&startMonth=1&endMonth=12&monthFlag=1&unitFlag=false&codeLength=8&outerField1=CODE_TS"
            "&outerField2=ORIGIN_COUNTRY&outerValue1=27111100&outerValue2=344&orderType=CODE+ASC+DEFAULT")
REQ = {"method": "GET", "url": LIST_URL, "body": "", "content_type": "", "headers": {"x-requested-with": "XMLHttpRequest"},
       "frame_url": "http://stats.customs.gov.cn/queryDataForEN/queryDataByWhereEn"}


def fake_site(start_url, reqs, pause_s=0, progress=None, interactive=False):
    out = []
    for r in reqs:
        q = {k: v[0] for k, v in parse_qs(urlsplit(r["url"]).query, keep_blank_values=True).items()}
        out.append(CS.table(q))
    fake_site.calls = [parse_qs(urlsplit(r["url"]).query) for r in reqs]
    return out


def test_period_fields_and_chunks():
    flds = F.fields(REQ)
    period = F.detect_period(flds, NOW.date())
    assert period == {"kind": "year_months", "year": "year", "start": "startMonth", "end": "endMonth"}
    roles = F.roles(flds, period)
    assert "перебирает" in roles["year"] and "1000" in roles["pageSize"] and "monthFlag" not in roles
    ch = F.chunks(period, 2024, NOW.date())
    assert [c[0] for c in ch] == [{"year": "2024", "startMonth": "1", "endMonth": "12"},
                                  {"year": "2025", "startMonth": "1", "endMonth": "12"},
                                  {"year": "2026", "startMonth": "1", "endMonth": "9"}]
    assert len(F.chunks(period, 2026, NOW.date(), by="month")) == 9          # октябрь ещё не закончился
    assert F.chunks(period, 2020, NOW.date(), since=dt.date(2026, 7, 1))[0][1] == dt.date(2026, 1, 1)
    new = F.with_fields(REQ, {"year": "2024", "pageSize": "1000"})
    q = parse_qs(urlsplit(new["url"]).query)
    assert q["year"] == ["2024"] and q["pageSize"] == ["1000"] and q["outerValue1"] == ["27111100"]


def test_period_in_json_body_and_yyyymm():
    req = {"method": "POST", "url": "https://x.org/api/query", "content_type": "application/json",
           "body": '{"flow": "M", "hs": "27111100", "from": "202401", "to": "202412", "limit": 20}'}
    p = F.detect_period(F.fields(req), NOW.date())
    assert p == {"kind": "yyyymm", "start": "from", "end": "to"}
    vals = F.period_values(p, dt.date(2025, 1, 1), dt.date(2025, 12, 31))
    new = F.with_fields(req, {**vals, **F.page_size_values(F.fields(req))})
    assert '"from": "202501"' in new["body"] and '"to": "202512"' in new["body"] and '"limit": 1000' in new["body"]
    assert F.month_of("2024年3月") == dt.date(2024, 3, 31) and F.month_of("202402") == dt.date(2024, 2, 29)
    assert F.month_of("Mar 2024") == dt.date(2024, 3, 31) and F.month_of("27111100") is None


def test_json_answer_becomes_table():
    text = '{"data": {"rows": [{"month": "2024-01", "hs": "27111100", "kg": 10, "usd": 20}, ' \
           '{"month": "2024-02", "hs": "27111100", "kg": 11, "usd": 22}]}}'
    t = F.result_table(text)
    lay = F.layout(t)
    assert t.header == ["month", "hs", "kg", "usd"] and lay["date"] == 0 and lay["values"] == [2, 3]


def test_customs_fetch_by_years_monthly_rows():
    """С 2024 года: три запроса (по году), в ответе — строки по месяцам; размер страницы увеличен, иначе
    в ответ вошли бы только 10 месяцев из 12. Ряды: количество (кг) и стоимость ($)."""
    params = {"url": "http://stats.customs.gov.cn/indexEn", "request": REQ, "from_year": 2024,
              "period": F.detect_period(F.fields(REQ), NOW.date()), "by": "year", "periodicity": "month"}
    res = get("web_form").fetch(params, Context(now=NOW, clients={"web_form": fake_site}))
    assert [c["year"] for c in fake_site.calls] == [["2024"], ["2025"], ["2026"]]
    assert all(c["pageSize"] == ["1000"] for c in fake_site.calls)
    by = {s.name.split(" | ")[-1]: s for s in res.series}
    assert set(by) == {"First quantity", "US dollar"}
    assert by["First quantity"].unit == "Kilogram" and by["US dollar"].unit == "USD"
    assert ("Commodity code", "27111100") in by["US dollar"].dims and ("Trading partner", "Russian Federation") in by["US dollar"].dims
    assert by["US dollar"].code.startswith("CUSTOMS_")
    val = {(o.key.split("|")[-1], o.period_end): o.value for o in res.obs}
    assert val[("First quantity", dt.date(2025, 3, 31))] == 25003 and val[("US dollar", dt.date(2025, 3, 31))] == 50006
    assert len([o for o in res.obs if o.key.endswith("US dollar")]) == 12 + 12 + 9
    # дозагрузка: только текущий год
    ctx = Context(now=NOW, clients={"web_form": fake_site})
    ctx.last_period, ctx.full_reload = "2026-09", False
    res = get("web_form").fetch(params, ctx)
    assert [c["year"] for c in fake_site.calls] == [["2026"]] and res.partial


@pytest.fixture
def site():
    srv, base = CS.start()
    CS.Handler.calls.clear()
    CS.Handler.check_after = None
    yield base
    CS.Handler.check_after = None
    srv.shutdown()


@pytest.mark.skipif(not os.environ.get("HUB_CHROMIUM"), reason="нужен Chromium (HUB_CHROMIUM)")
def test_record_form_and_replay_in_browser(site, tmp_path):
    """Настоящий Chromium: форма внутри <iframe>, рамка перезагружается при открытии (как защита сайта),
    данные выдаются только с кукой. Пользователь заполняет форму — хаб записывает запрос и повторяет его
    по годам из страницы сайта."""
    pytest.importorskip("playwright")
    import functools
    from hub.core.paths import Paths
    from hub.sources.browser import Browser
    br = Browser(Paths(tmp_path))

    def fill(page):
        page.wait_for_timeout(1500)
        fr = page.frame_locator("#iframe")
        fr.locator("#year").select_option("2025")
        fr.locator("#code").fill("27111100")
        fr.locator("#partner").fill("344")
        fr.locator("#search").click()
        fr.locator("table").wait_for(timeout=10000)
        page.wait_for_timeout(500)
    cands = br.record(site + "/indexEn", headless=True, on_page=fill)
    cand, table = F.best_candidate(cands)
    assert cand and "queryDataListEn" in cand["url"] and cand["frame_url"].endswith("queryDataByWhereEn")
    assert len(table.rows) == 10                                   # на странице сайта — только 10 месяцев
    params = {"url": site + "/indexEn", "request": cand, "from_year": 2025, "by": "year", "periodicity": "month",
              "period": F.detect_period(F.fields(cand), NOW.date())}
    ctx = Context(now=NOW, clients={"web_form": functools.partial(br.replay, headless=True)})
    res = get("web_form").fetch(params, ctx)
    assert [c["year"] for c in CS.Handler.calls[-2:]] == ["2025", "2026"]
    assert len([o for o in res.obs if o.key.endswith("US dollar")]) == 12 + 9


@pytest.mark.skipif(not os.environ.get("HUB_CHROMIUM"), reason="нужен Chromium (HUB_CHROMIUM)")
def test_pick_inside_iframe_after_reload(site, tmp_path):
    """«Выбрать на странице» на сайте, который перезагружает страницу и держит данные во <iframe>:
    раньше — «Execution context was destroyed», теперь выбор работает и в рамке."""
    pytest.importorskip("playwright")
    from hub.core.paths import Paths
    from hub.sources.browser import Browser
    br = Browser(Paths(tmp_path))

    def click(page):
        page.wait_for_timeout(1500)
        fr = page.frame_locator("#iframe")
        fr.locator("#code").fill("27111100")
        fr.locator("#search").evaluate("b => b.onclick()")
        fr.locator("table").wait_for(timeout=10000)
        page.wait_for_timeout(800)
        fr.locator("table").evaluate("t => t.dispatchEvent(new MouseEvent('click', {bubbles: true, shiftKey: true}))")
    pick = br.pick(site + "/indexEn", headless=True, timeout_s=30, on_page=click)
    assert pick and pick["kind"] == "table" and "queryDataByWhereEn" in pick["url"]
    assert "Liquefied natural gas" in pick["page_html"]


def test_runner_loads_history_then_only_fresh_months(workdir):
    """Через runner: первая загрузка — с 2024 по годам; следующая — только текущий год (дозагрузка)."""
    from hub.core.paths import Paths
    from hub.runner import Runner
    r = Runner(Paths(workdir), ctx_clients={"web_form": fake_site}, now=NOW)
    try:
        params = {"url": "http://stats.customs.gov.cn/indexEn", "request": REQ, "from_year": 2024,
                  "period": F.detect_period(F.fields(REQ), NOW.date()), "by": "year", "periodicity": "month"}
        job = r.cfg.add_job("web_form", "Таможня Китая: СПГ из России", params)
        out = r.run(job_ids=[job["id"]]).outcomes[0]
        assert out.status == "updated", out.message
        assert len(r.store.series_of_job(job["id"])) == 2
        assert [c["year"] for c in fake_site.calls] == [["2024"], ["2025"], ["2026"]]
        out = r.run(job_ids=[job["id"]]).outcomes[0]
        assert [c["year"] for c in fake_site.calls] == [["2026"]]
        assert out.status in ("waiting", "unchanged", "delayed") and not out.new_periods
    finally:
        r.close()


@pytest.mark.skipif(not os.environ.get("HUB_CHROMIUM"), reason="нужен Chromium (HUB_CHROMIUM)")
def test_slider_check_needs_human(site, tmp_path):
    """После второго запроса сайт просит «сдвинуть ползунок». В фоне — ошибка «проверка» (сайт на паузу,
    на «Обзоре» кнопка); по кнопке — окно на экран, человек сдвигает ползунок, хаб продолжает сам."""
    pytest.importorskip("playwright")
    from hub.core import errors as E
    from hub.core.paths import Paths
    from hub.sources.browser import Browser, is_check
    br = Browser(Paths(tmp_path))
    req = dict(REQ, url=site + "/queryDataForEN/queryDataListEn?" + LIST_URL.split("?", 1)[1],
               frame_url=site + "/queryDataForEN/queryDataByWhereEn")
    reqs = [F.with_fields(req, {"year": str(y)}) for y in (2023, 2024, 2025)]
    assert is_check(200, CS.CHECK) and not is_check(200, CS.table({"year": "2024", "startMonth": "1", "endMonth": "2"}))
    CS.Handler.check_after = 1
    with pytest.raises(E.HubError) as ei:
        br.replay(site + "/indexEn", reqs, pause_s=0, headless=True)
    assert ei.value.category == E.BOT and "ползунок" in str(ei.value)
    CS.Handler.calls.clear()
    seen = []
    out = br.replay(site + "/indexEn", reqs, pause_s=0, headless=True, interactive=True,
                    human=lambda tab: (seen.append(tab.url), tab.click("#slide")), human_wait_s=30)
    assert len(seen) == 1 and len(out) == 3 and all("Liquefied natural gas" in t for t in out)
    # 2024 спрошен дважды: вкладка проверки открывает тот же запрос (после ползунка там уже данные)
    assert [c["year"] for c in CS.Handler.calls] == ["2023", "2024", "2024", "2025"]


def test_check_in_background_pauses_site(workdir):
    """В фоне сайт попросил ползунок: показатель «сайт не пускает», сайт на паузе с причиной «проверка»,
    по кнопке (не в фоне) хаб передаёт браузеру «человек рядом»."""
    from hub.core import errors as E, hostblock as HB
    from hub.core.paths import Paths
    from hub.runner import Runner
    from hub.sources.browser import CHECK_MESSAGE
    seen = []

    def check_site(start_url, reqs, pause_s=0, progress=None, interactive=False):
        seen.append(interactive)
        raise E.HubError(E.BOT, CHECK_MESSAGE)
    r = Runner(Paths(workdir), ctx_clients={"web_form": check_site}, now=NOW)
    try:
        params = {"url": "http://stats.customs.gov.cn/indexEn", "request": REQ, "from_year": 2026,
                  "period": F.detect_period(F.fields(REQ), NOW.date()), "by": "month"}
        job = r.cfg.add_job("web_form", "СПГ", params)
        out = r.run(scheduled=True).outcomes[0]
        assert out.status == "blocked" and seen == [False]
        host, _till, reason = HB.active(r.store)[0]
        assert host == "stats.customs.gov.cn" and "ползунок" in reason
        HB.lift(r.store, host)
        r.run(job_ids=[job["id"]])
        assert seen == [False, True]
    finally:
        r.close()


def test_accepted_missing_series_are_not_asked_again(workdir):
    """«Исчезли ряды»: пользователь принял — хаб больше не ждёт эти ряды и не спрашивает каждый день снова
    (раньше спрашивал: ряды оставались в списке ожидаемых). Ряд вернулся — снова ждём."""
    from hub.core.paths import Paths
    from hub.runner import Runner
    partners = ["Russian Federation", "Qatar"]

    def site(start_url, reqs, pause_s=0, progress=None, interactive=False):
        out = []
        for _r in reqs:
            rows = "".join(f"<tr><td>2026{m:02d}</td><td>{p}</td><td>{1000 * m + i}</td></tr>"
                           for i, p in enumerate(partners) for m in range(1, 10))
            out.append("<table><thead><tr><th>Date of data</th><th>Trading partner</th><th>US dollar</th></tr></thead>"
                       f"<tbody>{rows}</tbody></table>")
        return out
    r = Runner(Paths(workdir), ctx_clients={"web_form": site}, now=NOW)
    try:
        params = {"url": "http://stats.customs.gov.cn/indexEn", "request": REQ, "from_year": 2026,
                  "period": F.detect_period(F.fields(REQ), NOW.date()), "by": "year", "periodicity": "month"}
        job = r.cfg.add_job("web_form", "СПГ", params)
        assert r.run(job_ids=[job["id"]]).outcomes[0].status == "updated"
        full = lambda: r.store.meta_set(f"full:{job['id']}", "")  # noqa: E731 — полная загрузка (раз в 30 дней)
        partners[:] = ["Russian Federation"]                      # Катар пропал
        full()
        out = r.run(job_ids=[job["id"]]).outcomes[0]
        assert out.status == "quarantine" and out.problems[0].startswith("Исчезли ряды (1)")
        qid = r.store.pending_quarantine(job["id"])[0]["id"]
        # отложено прежней версией (без списка пропавших) — пропавшие находятся по «давно не приходил»
        r.store.meta_set(f"lost:{qid}", "")
        with r.store.tx() as c:
            c.execute("UPDATE series SET last_seen='2000-01-01T00:00:00' WHERE job_id=? AND name LIKE '%Qatar%'",
                      (job["id"],))
        assert r.store.retire_lost(qid) == 1
        r.store.accept_quarantine(qid)
        full()
        out = r.run(job_ids=[job["id"]]).outcomes[0]               # на следующий день — не спрашивает снова
        assert out.status != "quarantine", out.problems
        partners[:] = ["Russian Federation", "Qatar"]              # Катар вернулся — снова ждём его
        full()
        r.run(job_ids=[job["id"]])
        assert not any(s["retired"] for s in r.store.series_of_job(job["id"]))
        partners[:] = ["Russian Federation"]
        full()
        assert r.run(job_ids=[job["id"]]).outcomes[0].status == "quarantine"
    finally:
        r.close()
