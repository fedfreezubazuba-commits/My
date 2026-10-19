"""
Главное окно «Хаба данных»: одно окно с вкладками (раздел 11 ТЗ).

  Обзор · Найти данные · Мои показатели · Модели · Отчёты · Настройки

Долгие операции идут в фоне (app.bg), окно не замирает; внизу — строка состояния с индикатором.
"""
import queue
import threading
import tkinter as tk
import traceback
from tkinter import messagebox, ttk

from .. import APP_NAME, VERSION
from ..core import errors as E
from ..core.config import Config
from ..core.log import get_logger
from ..core.paths import Paths, is_configured
from ..core.store import Store
from . import widgets as W


class HubApp:
    def __init__(self, root):
        self.root = root
        root.title(f"{APP_NAME}")
        root.geometry("1320x820")
        root.minsize(980, 620)
        W.apply_style(root)
        self.q = queue.Queue()
        self.tasks = 0
        self.clients = {}       # подмена сетевых клиентов (тесты)
        self.open_paths()
        self._build()
        root.after(100, self._poll)
        if not self.cfg.settings().get("first_run_done") or not is_configured():
            root.after(300, self.first_run)
        else:
            root.after(200, self.refresh_all)

    # --- сервисы -------------------------------------------------------------
    def open_paths(self):
        self.paths = Paths().ensure()
        self.cfg = Config(self.paths)
        self.store = Store(self.paths.db)
        self.log = get_logger(self.paths)

    def runner(self):
        from ..runner import Runner
        return Runner(self.paths, log=self.log, browser=self.browser(), ctx_clients=self.clients)

    def browser(self):
        try:
            from ..sources.browser import Browser
            return Browser(self.paths)
        except Exception:  # noqa: BLE001 — встроенный браузер не обязателен
            return None

    # --- окно ----------------------------------------------------------------
    def _build(self):
        top = ttk.Frame(self.root, padding=(12, 8, 12, 0))
        top.pack(fill="x")
        ttk.Label(top, text=APP_NAME, style="Title.TLabel").pack(side="left")
        ttk.Label(top, text=f"  версия {VERSION}", style="Hint.TLabel").pack(side="left")
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=8, pady=6)

        from .tab_find import FindTab
        from .tab_jobs import JobsTab
        from .tab_models import ModelsTab
        from .tab_overview import OverviewTab
        from .tab_reports import ReportsTab
        from .tab_settings import SettingsTab
        self.overview = OverviewTab(self.nb, self)
        self.find = FindTab(self.nb, self)
        self.jobs = JobsTab(self.nb, self)
        self.models = ModelsTab(self.nb, self)
        self.reports = ReportsTab(self.nb, self)
        self.settings = SettingsTab(self.nb, self)
        for tab, title in ((self.overview, "Обзор"), (self.find, "Найти данные"), (self.jobs, "Мои показатели"),
                           (self.models, "Модели"), (self.reports, "Отчёты"), (self.settings, "Настройки")):
            self.nb.add(tab, text=title)
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self._on_tab())

        bar = ttk.Frame(self.root, padding=(12, 0, 12, 6))
        bar.pack(fill="x")
        self.status = tk.StringVar(value="Готово")
        ttk.Label(bar, textvariable=self.status, style="Muted.TLabel").pack(side="left")
        self.pb = ttk.Progressbar(bar, mode="indeterminate", length=180)

    def _on_tab(self):
        tab = self.nb.nametowidget(self.nb.select())
        if hasattr(tab, "refresh"):
            tab.refresh()

    def show(self, tab):
        self.nb.select(tab)

    def refresh_all(self):
        for tab in (self.overview, self.jobs, self.models, self.reports):
            try:
                tab.refresh()
            except Exception:  # noqa: BLE001
                self.log.exception("Не удалось обновить вкладку")

    # --- фоновые задачи ------------------------------------------------------
    def bg(self, work, done=None, msg="Работаю…", error=None):
        """Выполнить work() в фоне; done(result) и error(exc) — в окне."""
        self.tasks += 1
        self.status.set(msg)
        self.pb.pack(side="right")
        self.pb.start(12)

        def target():
            try:
                self.q.put((done, error, work(), None))
            except Exception as exc:  # noqa: BLE001
                exc.hub_traceback = traceback.format_exc()
                self.q.put((done, error, None, exc))
        threading.Thread(target=target, daemon=True).start()

    def _poll(self):
        try:
            while True:
                done, error, res, exc = self.q.get_nowait()
                self.tasks -= 1
                if self.tasks <= 0:
                    self.tasks = 0
                    self.pb.stop()
                    self.pb.pack_forget()
                    self.status.set("Готово")
                if exc is not None:
                    self.log.error("Фоновая задача: %s", getattr(exc, "hub_traceback", exc))
                    if error:
                        error(exc)
                    else:
                        self.error(exc)
                elif done:
                    try:
                        done(res)
                    except Exception as e:  # noqa: BLE001
                        self.log.exception("Ошибка в окне")
                        self.error(e)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def error(self, exc, title="Не получилось"):
        from ..runner import Busy
        if isinstance(exc, Busy):
            messagebox.showinfo("Подождите", "Обновление уже идёт в фоне. Подождите минуту и посмотрите «Обзор».",
                                parent=self.root)
            return
        messagebox.showerror(title, E.human_message(exc), parent=self.root)

    # --- обновления ----------------------------------------------------------
    def update_jobs(self, job_ids=None, force=False, done=None):
        def work():
            r = self.runner()
            try:
                return r.run(job_ids=job_ids, force=force)
            finally:
                r.close()

        def finished(rep):
            self.refresh_all()
            if done:
                done(rep)
        n = "всех показателей" if force else f"показателей: {len(job_ids or [])}"
        self.bg(work, finished, f"Обновление {n}…")

    def first_run(self):
        from .wizard import FirstRunWizard
        FirstRunWizard(self)

    def reopen(self):
        """После смены рабочей папки."""
        try:
            self.store.close()
        except Exception:  # noqa: BLE001
            pass
        self.open_paths()
        self.refresh_all()


def main():
    try:
        root = tk.Tk()
    except tk.TclError as e:
        print("Не удалось открыть окно:", e)
        return 1
    HubApp(root)
    root.mainloop()
    return 0
