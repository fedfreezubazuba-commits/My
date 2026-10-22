"""Сайт-образец по устройству таможни КНР (stats.customs.gov.cn) — для тестов «Формы на сайте».

Как у настоящего: внешняя страница с <iframe>, форма внутри рамки; при первом открытии рамка ставит
куку и перезагружается (так делает защита сайта); данные выдаются только с этой кукой; ответ — таблица
по месяцам, но не больше pageSize строк. Числа выдуманы: kg = 1000·(год−2000)+месяц, $ = 2·kg."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

OUTER = """<html><head><title>Customs statistics</title></head><body>
<h1>Customs statistics</h1><iframe id="iframe" src="/queryDataForEN/queryDataByWhereEn" style="width:90%;height:600px"></iframe>
</body></html>"""

FORM = """<html><head><meta charset="utf-8"><script>
if (!document.cookie.includes('ts=1')) { document.cookie = 'ts=1; path=/'; location.reload(); }
function query() {
  var q = new URLSearchParams({pageSize: '10', iEType: document.getElementById('iEType').value,
    currencyType: document.getElementById('cur').value, year: document.getElementById('year').value,
    startMonth: document.getElementById('sm').value, endMonth: document.getElementById('em').value,
    monthFlag: document.getElementById('mf').checked ? '1' : '', unitFlag: 'false', codeLength: '8',
    outerField1: 'CODE_TS', outerField2: 'ORIGIN_COUNTRY', outerValue1: document.getElementById('code').value,
    outerValue2: document.getElementById('partner').value, orderType: 'CODE ASC DEFAULT'});
  var x = new XMLHttpRequest();
  x.open('GET', '/queryDataForEN/queryDataListEn?' + q.toString());
  x.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
  x.onload = function () { document.getElementById('result').innerHTML = x.responseText; };
  x.send();
}
</script></head><body>
<select id="iEType"><option value="1">Import</option><option value="0">Export</option></select>
<select id="cur"><option value="usd">USD</option><option value="rmb">RMB</option></select>
<select id="year"><option>2026</option><option>2025</option><option>2024</option><option>2023</option></select>
<select id="sm"><option>1</option><option>2</option></select> - <select id="em"><option>12</option><option>3</option></select>
<label><input type="checkbox" id="mf" checked> By month</label>
<input id="code" value=""> <input id="partner" value="">
<button id="search" onclick="query()">Search</button>
<div id="result"></div>
</body></html>"""


def table(q):
    year, sm, em = int(q["year"]), int(q["startMonth"]), int(q["endMonth"])
    size = int(q.get("pageSize", "10"))
    ie = q.get("iEType", "1")
    rows = []
    for m in range(sm, em + 1):
        kg = 1000 * (year - 2000) + m + (0 if ie == "1" else 500000)
        rows.append(f"<tr><td>{year}{m:02d}</td><td>{q.get('outerValue1')}</td><td>Liquefied natural gas</td>"
                    f"<td>{q.get('outerValue2')}</td><td>Russian Federation</td><td>{kg:,}</td><td>Kilogram</td>"
                    f"<td>{2 * kg:,}</td></tr>")
    return ("<table class='table'><thead><tr><th>Date of data</th><th>Commodity code</th><th>Commodity</th>"
            "<th>Trading partner code</th><th>Trading partner</th><th>First quantity</th><th>First unit</th>"
            "<th>US dollar</th></tr></thead><tbody>" + "".join(rows[:size]) + "</tbody></table>")


CHECK = """<html><body><div class="nc-container"><p>请向右滑动验证 — slide to verify</p>
<button id="slide" onclick="document.cookie='human=1; path=/'; location.reload()">&rarr;</button></div></body></html>"""


class Handler(BaseHTTPRequestHandler):
    calls = []
    check_after = None           # после стольких запросов сайт просит проверку «я не робот» (ползунок)

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlsplit(self.path)
        if u.path in ("/", "/indexEn"):
            return self._send(200, OUTER)
        if u.path == "/queryDataForEN/queryDataByWhereEn":
            return self._send(200, FORM)
        if u.path == "/queryDataForEN/queryDataListEn":
            if "ts=1" not in (self.headers.get("Cookie") or ""):
                return self._send(403, "<html>forbidden</html>")
            q = {k: v[0] for k, v in parse_qs(u.query, keep_blank_values=True).items()}
            if Handler.check_after is not None and len(Handler.calls) >= Handler.check_after \
                    and "human=1" not in (self.headers.get("Cookie") or ""):
                return self._send(200, CHECK)
            Handler.calls.append(q)
            return self._send(200, table(q))
        return self._send(404, "not found")


def start():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"
