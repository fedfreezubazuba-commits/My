"""«Набор данных» по ссылке: длинный CSV (дата + разрезы + число), отбор значений, новые периоды."""
import datetime as dt
from pathlib import Path

import pytest

from hub.sources import Context, _dataset as D, _files as F, _web as W, get

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "web" / "sberindex_dataset.csv"
URL = "https://sberindex.ru/api/dataset/deprecated/csv/ru/ver-izmenenie-trat-po-kategoriyam.csv"
NOW = dt.datetime(2026, 10, 22, 12)


def table():
    data = SAMPLE.read_bytes()
    return F.read_tables(data, F.detect_format(data, URL))[0]


def test_analyze_long_csv():
    info = D.analyze(table())
    assert info["date"] == "date" and info["values"] == ["value"] and info["periodicity"] == "week"
    assert list(info["dims"]) == ["region", "category"] and len(info["dims"]["region"]) == 4
    p = D.params_for(info, {"region": ["Москва"], "category": info["dims"]["category"]})
    assert p["filters"] == {"region": ["Москва"]}                  # «всё отмечено» не сохраняется
    assert D.count_series(table(), p) == 4


def test_dataset_fetch_with_filter_and_new_periods(monkeypatch):
    data = {"bytes": SAMPLE.read_bytes()}
    monkeypatch.setattr(W, "http_get", lambda url, ctx=None, timeout=60: (200, "text/csv", data["bytes"], url))
    info = D.analyze(table())
    params = {"url": URL, **D.params_for(info, {"region": ["Москва", "Россия"]})}
    res = get("file_link").fetch(params, Context(now=NOW))
    assert len(res.series) == 8 and {s.periodicity for s in res.series} == {"week"}
    s = next(x for x in res.series if x.dims[:2] == [("region", "Москва"), ("category", "Продукты")])
    assert s.name == "Москва | Продукты" and s.code.startswith("SBERINDEX_")
    ends = sorted(o.period_end for o in res.obs if o.key == s.key)
    assert ends[-1] == dt.date(2026, 10, 18) and ends[-1].weekday() == 6     # неделя → конец недели
    # новый период и новый регион в файле: новая неделя приходит, регион без отбора не попадает
    extra = "2026-10-19,Москва,Продукты,5.5\n2026-10-19,Казань,Продукты,1.0\n".encode()
    data["bytes"] += extra
    res = get("file_link").fetch(params, Context(now=NOW))
    assert max(o.period_end for o in res.obs) == dt.date(2026, 10, 25)
    assert not any("Казань" in x.name for x in res.series)
    # источник переименовал столбец — понятная ошибка
    data["bytes"] = data["bytes"].replace(b"category", b"cat", 1)
    with pytest.raises(Exception) as ei:
        get("file_link").fetch(params, Context(now=NOW))
    assert "нет столбцов: category" in str(ei.value)


def test_download_falls_back_to_browser(monkeypatch):
    """Обычная загрузка не прошла (Python не доверяет сертификату сайта и т. п.) — файл берётся встроенным
    браузером, как в Chrome."""
    import requests
    from hub.sources import file_link

    def fail(url, ctx=None, timeout=60):
        raise requests.exceptions.ConnectionError("certificate verify failed")
    monkeypatch.setattr(W, "http_get", fail)

    class Br:
        def available(self):
            return True

        def download(self, url):
            return b"date,value\n2026-01-31,1\n", url, "text/csv"
    data, _final = file_link.download(URL, Context(browser=Br()))
    assert data.startswith(b"date,value")
    with pytest.raises(requests.exceptions.ConnectionError):
        file_link.download(URL, Context())                         # без браузера — прежняя ошибка


@pytest.mark.skipif(not __import__("os").environ.get("HUB_CHROMIUM"), reason="нужен Chromium (HUB_CHROMIUM)")
def test_browser_download_attachment_and_inline(tmp_path):
    pytest.importorskip("playwright")
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from hub.core.paths import Paths
    from hub.sources.browser import Browser

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = b"date,value\n2026-01-31,1\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            if "attach" in self.path:
                self.send_header("Content-Disposition", 'attachment; filename="x.csv"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        br = Browser(Paths(tmp_path))
        for path in ("/attach.csv", "/inline.csv"):
            data, _u, _ct = br.download(base + path, headless=True)
            assert data.startswith(b"date,value"), path
    finally:
        srv.shutdown()
