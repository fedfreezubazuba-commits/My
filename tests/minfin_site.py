"""Устройство раздела minfin.gov.ru/ru/statistics/fedbud/: список карточек документов (у ссылки скачивания — только
значок, без текста; рядом — название и дата), страница документа со ссылкой «Скачать». Каждый месяц сверху
появляется новая карточка. Числа и даты выдуманы."""
import io
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RELEASES = [("2026-10-12", "q", "Краткая ежеквартальная информация об исполнении федерального бюджета"),
            ("2026-10-09", "m", "Краткая ежемесячная информация об исполнении федерального бюджета"),
            ("2026-05-05", "y", "Краткая ежегодная информация об исполнении федерального бюджета")]


def card(date, kind, title):
    d = f"{date[8:10]}.{date[5:7]}.{date[:4]}"
    return (f'<div class="card"><div class="tags">#ФЕДЕРАЛЬНЫЙ БЮДЖЕТ #СТАТИСТИКА</div>'
            f'<a class="title" href="/ru/document/?id_4={kind}{date}">Документ</a><p>{title} (млрд руб.)</p>'
            f'<span class="date">{d}</span><a class="dl" href="/common/upload/library/{kind}{date}.xlsx">'
            f'<svg><path d="M0 0"/></svg></a></div>')


def list_page(releases):
    return ("<html><body><h1>Федеральный бюджет</h1><nav><a href='/ru/'>Министерство</a></nav>"
            "<h2>Краткая информация об исполнении федерального бюджета</h2>"
            + "".join(card(*r) for r in releases) + "</body></html>")


def doc_page(kind, date):
    return (f"<html><body><h1>Документ</h1><p>Опубликовано {date[8:10]}.{date[5:7]}.{date[:4]}</p>"
            f"<a href='/ru/statistics/fedbud/'>Назад</a> "
            f"<a href='/common/upload/library/{kind}{date}.xlsx'>Скачать (xlsx, 25 Кб)</a></body></html>")


def xlsx(name) -> bytes:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Исполнение ФБ"
    ws.append(["Показатель", name])
    ws.append(["Доходы", 30000.5])
    ws.append(["Расходы", 31000.25])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class Site:
    def __init__(self):
        self.releases = list(RELEASES)

    def handler(self):
        site = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def send(self, code, body, ctype, extra=None):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path.startswith("/ru/statistics/fedbud"):
                    return self.send(200, list_page(site.releases).encode(), "text/html; charset=utf-8")
                if self.path.startswith("/ru/document/"):
                    key = self.path.split("id_4=")[-1]
                    return self.send(200, doc_page(key[0], key[1:]).encode(), "text/html; charset=utf-8")
                if self.path.startswith("/common/upload/library/"):
                    name = self.path.rsplit("/", 1)[-1]
                    return self.send(200, xlsx(name), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                     {"Content-Disposition": f'attachment; filename="{name}"'})
                return self.send(404, b"not found", "text/plain")
        return H

    def start(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), self.handler())
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv, f"http://127.0.0.1:{srv.server_address[1]}"
