"""
Ошибки, которые иначе никто не увидит: окно запускается через pythonw (без консоли), и если программа
падает при запуске, сообщение пропадает. Здесь оно записывается в «Ошибка запуска.txt» в папке программы
(и в журнал рабочей папки), файл открывается в Блокноте, а в Windows ещё и показывается окно-сообщение.

Модуль намеренно почти ничего не импортирует: он должен работать, даже когда сломано остальное.
"""
import datetime as dt
import os
import platform
import sys
import threading
import traceback
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
FILE_NAME = "Ошибка запуска.txt"


def _version():
    try:
        from .. import VERSION
        return VERSION
    except Exception:  # noqa: BLE001
        return "?"


def write_report(details: str, title="Хаб данных не запустился") -> Path:
    text = (f"{title}\n"
            f"Время: {dt.datetime.now():%d.%m.%Y %H:%M:%S}\n"
            f"Версия хаба: {_version()}\n"
            f"Python: {sys.version.split()[0]} ({sys.executable})\n"
            f"Система: {platform.platform()}\n"
            f"Папка программы: {PROJECT}\n\n"
            "Скопируйте этот текст целиком и пришлите Claude.\n\n"
            f"{details}\n")
    path = PROJECT / FILE_NAME
    try:
        path.write_text(text, encoding="utf-8-sig")
    except OSError:
        import tempfile
        path = Path(tempfile.gettempdir()) / FILE_NAME
        path.write_text(text, encoding="utf-8-sig")
    try:                                   # копия в журнал рабочей папки — её увидит «Отправить Claude»
        from .paths import Paths
        logs = Paths().logs
        logs.mkdir(parents=True, exist_ok=True)
        with open(logs / "ошибки запуска.log", "a", encoding="utf-8") as fh:
            fh.write(text + "\n" + "-" * 70 + "\n")
    except Exception:  # noqa: BLE001
        pass
    return path


def startup_error(details: str, title="Хаб данных не смог запуститься") -> Path:
    """Сохранить, открыть в Блокноте и показать окно-сообщение (в Windows)."""
    path = write_report(details, title=title)
    if sys.stderr is not None:            # запуск из консоли («Запуск с проверкой.bat») — текст виден сразу
        try:
            print(f"{title}. Подробности: {path}\n\n{details}", file=sys.stderr, flush=True)
        except Exception:  # noqa: BLE001
            pass
    if os.name == "nt":
        try:
            os.startfile(str(path))       # noqa: S606 — открыть файл в Блокноте
        except OSError:
            pass
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None, f"{title}.\n\nПодробности открыты в Блокноте и сохранены в файле:\n"
                      f"{path}\n\nСкопируйте этот текст и пришлите Claude.", "Хаб данных", 0x10)
        except Exception:  # noqa: BLE001
            pass
    return path


class StartupWatch:
    """Сторож запуска: если окно не стало отвечать за N секунд (программа где-то зависла), записывает,
    на каком шаге и в каком месте кода она стоит, — тот же файл «Ошибка запуска.txt»."""

    def __init__(self, seconds=40):
        self.seconds = seconds
        self.stage = "запуск"
        self.ready = threading.Event()
        threading.Thread(target=self._watch, name="сторож запуска", daemon=True).start()

    def _watch(self):
        if self.ready.wait(self.seconds):
            return
        me = threading.get_ident()
        main = threading.main_thread().ident
        names = {t.ident: t.name for t in threading.enumerate()}
        parts = []
        for tid, frame in sys._current_frames().items():
            if tid == me:
                continue
            head = names.get(tid, str(tid)) + (" (главный)" if tid == main else "")
            parts.append(f"--- поток {head} ---\n" + "".join(traceback.format_stack(frame)))
        startup_error(f"Окно не открылось за {self.seconds} с. Шаг: {self.stage}\n\n" + "\n".join(parts),
                      title="Хаб данных завис при запуске")
