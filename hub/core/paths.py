"""
Пути проекта и рабочей папки.

Рабочая папка (по умолчанию C:\\MarketData) хранит всё, что принадлежит пользователю:
базу, посылки для моделей, отчёты, настройки и список показателей. Она вне репозитория,
поэтому обновление программы не трогает данные. Где она лежит, записано в
config/workdir.txt (файл не попадает в git). Переменная окружения HUB_WORKDIR
важнее файла — так тесты работают во временной папке.
"""
import os
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
POINTER = PROJECT / "config" / "workdir.txt"
IS_WINDOWS = sys.platform.startswith("win")


def default_workdir() -> Path:
    return Path("C:/MarketData") if IS_WINDOWS else Path.home() / "MarketData"


def is_configured() -> bool:
    return bool(os.environ.get("HUB_WORKDIR")) or POINTER.exists()


def workdir() -> Path:
    env = os.environ.get("HUB_WORKDIR")
    if env:
        return Path(env)
    try:
        text = POINTER.read_text(encoding="utf-8-sig").strip()
        if text:
            return Path(text)
    except OSError:
        pass
    return default_workdir()


def set_workdir(path) -> None:
    POINTER.parent.mkdir(parents=True, exist_ok=True)
    POINTER.write_text(str(Path(path)), encoding="utf-8")


class Paths:
    """Все папки рабочей папки. Создаются по требованию методом ensure()."""

    def __init__(self, root=None):
        self.root = Path(root) if root else workdir()
        self.db = self.root / "db" / "hub.sqlite"
        self.config = self.root / "config"
        self.jobs = self.config / "jobs.yaml"
        self.models = self.config / "models.yaml"
        self.settings = self.config / "settings.yaml"
        self.trash = self.config / "trash.yaml"
        self.packets = self.root / "Для моделей"
        self.exports = self.root / "Выгрузки"
        self.job_files = self.exports / "Показатели"
        self.raw = self.root / "raw"
        self.backups = self.root / "backups"
        self.model_backups = self.backups / "модели"
        self.reports = self.root / "reports"
        self.claude = self.reports / "для Claude"
        self.logs = self.root / "logs"
        self.browser_profile = self.root / "browser_profile"
        self.inbox = self.root / "Входящие"
        self.cache = self.root / "cache"
        self.lock = self.root / "runner.lock"

    def ensure(self):
        for p in (self.db.parent, self.config, self.packets, self.exports, self.raw,
                  self.model_backups, self.reports, self.logs, self.inbox, self.cache):
            p.mkdir(parents=True, exist_ok=True)
        return self
