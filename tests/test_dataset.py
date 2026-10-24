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


@pytest.mark.skipif(__import__("shutil").which("openssl") is None, reason="нужен openssl")
def test_site_without_intermediate_certificate(tmp_path, monkeypatch):
    """Сайт отдаёт только свой сертификат, без промежуточного (как sberindex.ru): Chrome докачивает
    промежуточный по адресу из сертификата — хаб теперь тоже. Проверка подлинности при этом не отключается."""
    import http.server
    import ssl
    import subprocess
    import threading
    import certifi

    def sh(*args):
        subprocess.run(args, cwd=tmp_path, check=True, capture_output=True)
    plain = http.server.ThreadingHTTPServer(("127.0.0.1", 0), http.server.SimpleHTTPRequestHandler)
    plain.RequestHandlerClass = type("H", (http.server.SimpleHTTPRequestHandler,), {
        "log_message": lambda *a: None,
        "__init__": lambda self, *a, **k: http.server.SimpleHTTPRequestHandler.__init__(self, *a, directory=str(tmp_path),
                                                                                         **k)})
    aia = f"http://127.0.0.1:{plain.server_address[1]}/inter.cer"
    (tmp_path / "ca.ext").write_text("basicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign,cRLSign\n")
    (tmp_path / "leaf.ext").write_text(f"subjectAltName=IP:127.0.0.1\nauthorityInfoAccess=caIssuers;URI:{aia}\n")
    sh("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "root.key", "-out", "root.pem",
       "-days", "2", "-subj", "/CN=Test Root")
    sh("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", "inter.key", "-out", "inter.csr", "-subj", "/CN=Test Inter")
    sh("openssl", "x509", "-req", "-in", "inter.csr", "-CA", "root.pem", "-CAkey", "root.key", "-CAcreateserial",
       "-out", "inter.pem", "-days", "2", "-extfile", "ca.ext")
    sh("openssl", "x509", "-in", "inter.pem", "-outform", "DER", "-out", "inter.cer")
    sh("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", "leaf.key", "-out", "leaf.csr", "-subj", "/CN=127.0.0.1")
    sh("openssl", "x509", "-req", "-in", "leaf.csr", "-CA", "inter.pem", "-CAkey", "inter.key", "-CAcreateserial",
       "-out", "leaf.pem", "-days", "2", "-extfile", "leaf.ext")
    (tmp_path / "data.csv").write_text("date;category;value\n2026-10-11;Продукты;1.5\n", encoding="utf-8")

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(tmp_path), **k)

        def log_message(self, *a):
            pass
    secure = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
    sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    sctx.load_cert_chain(tmp_path / "leaf.pem", tmp_path / "leaf.key")       # только свой, без промежуточного
    secure.socket = sctx.wrap_socket(secure.socket, server_side=True)
    for srv in (plain, secure):
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    bundle = tmp_path / "trusted.pem"                       # «общепризнанные» корни: certifi + наш тестовый корень
    bundle.write_text(open(certifi.where()).read() + (tmp_path / "root.pem").read_text())
    monkeypatch.setattr(certifi, "where", lambda: str(bundle))
    monkeypatch.setattr("requests.utils.DEFAULT_CA_BUNDLE_PATH", str(bundle), raising=False)
    monkeypatch.setattr("requests.adapters.DEFAULT_CA_BUNDLE_PATH", str(bundle), raising=False)
    monkeypatch.setattr(W, "_system_trust_adapter", lambda: __import__("requests").adapters.HTTPAdapter())
    try:
        url = f"https://127.0.0.1:{secure.server_address[1]}/data.csv"
        status, _ct, data, _f = W.http_get(url)
        assert status == 200 and data.startswith(b"date;category;value")
    finally:
        plain.shutdown()
        secure.shutdown()


def test_real_sberindex_format():
    """Формат настоящей выгрузки «изменение трат по категориям»: UTF-8 с BOM, «;», date;category;value,
    недели — датой воскресенья, пустые значения пропускаются."""
    data = (SAMPLE.parent / "sberindex_izmenenie_trat.csv").read_bytes()
    assert data[:3] == b"\xef\xbb\xbf"
    t = F.read_tables(data, F.detect_format(data, URL))[0]
    info = D.analyze(t)
    assert info["date"] == "date" and list(info["dims"]) == ["category"] and info["periodicity"] == "week"
    res = D.to_result(t, D.params_for(info), "SBERINDEX", URL)
    assert len(res.series) == 6 and {o.period_end.weekday() for o in res.obs} == {6}
    assert res.series[0].name == "Продукты" and res.series[0].code.startswith("SBERINDEX_")
    rows = len([ln for ln in data.decode("utf-8-sig").splitlines()[1:] if not ln.endswith(";")])
    assert len(res.obs) == rows                                       # пустые значения — не точки ряда
    from hub.sources import _forms as Fm
    assert Fm.month_of("2024-W05") == dt.date(2024, 2, 4)
