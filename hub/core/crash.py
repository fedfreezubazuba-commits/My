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


def startup_error(details: str) -> Path:
    """Сохранить, открыть в Блокноте и показать окно-сообщение (в Windows)."""
    path = write_report(details)
    if sys.stderr is not None:            # запуск из консоли («Запуск с проверкой.bat») — текст виден сразу
        try:
            print(f"Хаб данных не смог запуститься. Подробности: {path}\n\n{details}", file=sys.stderr)
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
                None, "Хаб данных не смог запуститься.\n\nПодробности открыты в Блокноте и сохранены в файле:\n"
                      f"{path}\n\nСкопируйте этот текст и пришлите Claude.", "Хаб данных", 0x10)
        except Exception:  # noqa: BLE001
            pass
    return path
