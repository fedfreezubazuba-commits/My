"""Страница-приложение по устройству salmonprice.com/fishpool (#/public/priceHistory): на главной — форма входа,
публичная история цен грузится скриптом одним ответом JSON (годы → месяцы → недели, EUR/kg и NOK/kg) с ключом,
который скрипт страницы получает сам. Числа выдуманы."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def history():
    years = []
    for y in (2024, 2025):
        months = []
        for mi, (name, weeks) in enumerate((("Aug", (32, 33, 34, 35)), ("Sep", (36, 37, 38, 39)))):
            ws = [{"week": w, "eur": round(6 + (w % 7) / 10 + (y - 2024), 2), "nok": round(70 + w % 9 + 10 * (y - 2024), 2)}
                  for w in weeks]
            months.append({"month": name, "weeks": f"Weeks {weeks[0]}-{weeks[-1]}",
                           "eur": round(sum(x["eur"] for x in ws) / len(ws), 2),
                           "nok": round(sum(x["nok"] for x in ws) / len(ws), 2), "weekly": ws})
        years.append({"year": y, "months": months})
    return {"title": "Sitagri Salmon Index", "years": years}


PAGE = """<html><head><meta charset="utf-8"><title>Fish Pool</title></head><body>
<form id="login"><input name="user"><input type="password" name="pass"><button>Log in</button></form>
<a href="#/public/priceHistory">Price history</a><div id="app"></div>
<script>
async function show() {
  if (location.hash !== '#/public/priceHistory') return;
  const t = await (await fetch('/fishpool/api/session')).text();             // ключ страницы
  const r = await fetch('/fishpool/api/public/priceHistory', {headers: {'Authorization': 'Bearer ' + t}});
  const d = await r.json();
  let h = '<table><tr><th>Month</th><th>EUR/kg</th></tr>';
  for (const y of d.years) for (const m of y.months) h += '<tr><td>' + m.month + '</td><td>' + m.eur + '</td></tr>';
  document.getElementById('app').innerHTML = h + '</table>';
}
window.addEventListener('hashchange', show); show();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/fishpool/api/session"):
            return self._send(200, "token-123", "text/plain")
        if self.path.startswith("/fishpool/api/public/priceHistory"):
            if self.headers.get("Authorization") != "Bearer token-123":
                return self._send(401, "{}", "application/json")
            return self._send(200, json.dumps(history()), "application/json")
        return self._send(200, PAGE, "text/html; charset=utf-8")


def start():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"
