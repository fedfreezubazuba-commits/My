"""FRED: ряд по коду, постоянная ссылка на CSV (без дат графика), метаданные со страницы ряда."""
import datetime as dt
from pathlib import Path

import pytest

from hub.sources import Context, _web as W, fred as FR, get

SAMPLES = Path(__file__).parent.parent / "samples" / "web"
LINK = ("https://fred.stlouisfed.org/graph/fredgraph.csv?bgcolor=%23ebf3fb&chart_type=line&drp=0&fo=open%20sans"
        "&id=PFISHUSDM&scale=left&cosd=1992-01-01&coed=2026-09-01&fq=Monthly&vintage_date=2026-10-24"
        "&revision_date=2026-10-24&nd=1992-01-01")


@pytest.fixture
def site(monkeypatch):
    calls = []

    def fake_get(url, ctx=None, timeout=60, headers=None):
        calls.append(url)
        if url == FR.csv_url("PFISHUSDM"):
            return 200, "text/csv", (SAMPLES / "fred_PFISHUSDM.csv").read_bytes(), url
        if url == FR.page_url("PFISHUSDM"):
            return 200, "text/html; charset=utf-8", (SAMPLES / "fred_PFISHUSDM.html").read_bytes(), url
        if url == FR.csv_url("DGS10"):
            return 200, "text/csv", b"DATE,DGS10\n2026-10-19,4.01\n2026-10-20,.\n2026-10-21,3.98\n", url
        if "NOPE" in url:
            return 404, "text/html", b"<html>Not found</html>", url
        return 404, "text/html", b"", url
    monkeypatch.setattr(W, "http_get", fake_get)
    monkeypatch.setattr(FR, "PAUSE_S", 0)
    monkeypatch.setattr(FR, "_way", {})
    return calls


def test_ids_from_links_and_codes():
    assert FR.ids_from_text(LINK) == ["PFISHUSDM"]
    assert FR.ids_from_text("https://fred.stlouisfed.org/series/PFISHUSDM") == ["PFISHUSDM"]
    assert FR.ids_from_text("pfishusdm, DGS10") == ["PFISHUSDM", "DGS10"]
    assert FR.ids_from_text("https://fred.stlouisfed.org/graph/?g=1abc") == []


def test_monthly_series_with_meta_from_page(site):
    ctx = Context(now=dt.datetime(2026, 10, 24, 12))
    meta = FR.fetch_meta("PFISHUSDM", ctx)
    assert meta == {"title": "Global price of Fish meal", "units": "U.S. Dollars per Metric Ton",
                    "frequency": "Monthly", "seasonal": "Not Seasonally Adjusted"}
    res = get("fred").fetch({"ids": FR.ids_from_text(LINK), "meta": {"PFISHUSDM": meta}}, ctx)
    s = res.series[0]
    assert (s.key, s.code, s.name, s.unit, s.periodicity) == (
        "fred|PFISHUSDM", "FRED_PFISHUSDM", "Global price of Fish meal", "U.S. Dollars per Metric Ton", "month")
    obs = [(o.period_end, o.value) for o in res.obs]
    assert obs[0] == (dt.date(2025, 10, 31), 1905.12) and len(obs) == 5      # «.» — пропуск
    assert res.obs[0].source_date == "2025-10-01"
    assert all("cosd" not in u and "vintage" not in u for u in site)         # даты из ссылки сайта не берутся


def test_daily_series_without_meta_and_missing_code(site):
    res = get("fred").fetch({"ids": ["DGS10"]}, Context())
    assert res.series[0].name == "DGS10" and res.series[0].periodicity == "day"
    assert [o.period_end for o in res.obs] == [dt.date(2026, 10, 19), dt.date(2026, 10, 21)]
    from hub.core.errors import StructureError
    with pytest.raises(StructureError, match="нет ряда"):
        get("fred").fetch({"ids": ["NOPE"]}, Context())


def test_meta_from_text_file():
    txt = ("Title:               Global price of Fish meal\nSeries ID:           PFISHUSDM\n"
           "Seasonal Adjustment: Not Seasonally Adjusted\nFrequency:           Monthly\n"
           "Units:               U.S. Dollars per Metric Ton\nDate Range:          1990-01-01 to 2026-09-01\n")
    m = FR.parse_meta(txt, "PFISHUSDM")
    assert m["units"] == "U.S. Dollars per Metric Ton" and m["frequency"] == "Monthly"


class FakeBrowser:
    def __init__(self):
        self.calls = []

    def available(self):
        return True

    def download(self, url, headless=False, timeout_ms=0):
        self.calls.append(url)
        return (SAMPLES / "fred_PFISHUSDM.csv").read_bytes(), url, "text/csv"

    def get_html(self, url, timeout_ms=0, offscreen=False):
        self.calls.append(url)
        return (SAMPLES / "fred_PFISHUSDM.html").read_text(encoding="utf-8"), url


def test_hanging_requests_fall_back_and_way_is_remembered(monkeypatch, tmp_path):
    """FRED «подвешивает» запросы программ: обычный запрос и «как браузер» — тайм-аут, а встроенный браузер
    (как Chrome) получает файл. Способ запоминается — в следующий раз сразу браузер, без ожиданий."""
    import requests
    from hub.core.paths import Paths
    tried = []

    def hang(url, ctx=None, timeout=60, headers=None):
        tried.append(headers)
        raise requests.exceptions.ReadTimeout("read timed out")
    monkeypatch.setattr(W, "http_get", hang)
    monkeypatch.setattr(FR, "_way", {})
    br = FakeBrowser()
    ctx = Context(paths=Paths(tmp_path), browser=br)
    res = get("fred").fetch({"ids": ["PFISHUSDM"]}, ctx)
    assert len(res.obs) == 5 and len(tried) == 3 and br.calls == [FR.csv_url("PFISHUSDM")]
    assert (tmp_path / "cache" / "fred_way.txt").read_text() == "browser"
    assert FR.fetch_meta("PFISHUSDM", ctx)["units"] == "U.S. Dollars per Metric Ton"
    monkeypatch.setattr(FR, "_way", {})                     # новый запуск программы: способ — из файла
    tried.clear()
    get("fred").fetch({"ids": ["PFISHUSDM"]}, ctx)
    assert tried == []


def test_nothing_answers_gives_clear_error(monkeypatch):
    import requests

    def hang(url, ctx=None, timeout=60, headers=None):
        raise requests.exceptions.ConnectTimeout("connect timed out")
    monkeypatch.setattr(W, "http_get", hang)
    monkeypatch.setattr(FR, "_way", {})
    from hub.core.errors import HubError
    with pytest.raises(HubError, match="FRED не ответил") as e:
        get("fred").fetch({"ids": ["PFISHUSDM"]}, Context())
    assert "plain" in e.value.detail and "curl" in e.value.detail
