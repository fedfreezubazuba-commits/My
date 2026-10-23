"""Мастер первого запуска: рабочая папка → папка с моделями → автообновление → готово."""
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from ..core import winsched
from ..core.paths import set_workdir, workdir
from . import widgets as W


class FirstRunWizard(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("Добро пожаловать в Хаб данных")
        self.geometry("680x380")
        self.transient(app.root)
        self.step = 0
        self.folder = tk.StringVar(value=str(workdir()))
        self.models = tk.StringVar(value=app.cfg.settings().get("models_folder") or str(Path.home() / "Documents"))
        self.auto = tk.BooleanVar(value=True)
        self.body = ttk.Frame(self, padding=20)
        self.body.pack(fill="both", expand=True)
        bar = ttk.Frame(self, padding=(20, 0, 20, 16))
        bar.pack(fill="x")
        self.next_btn = ttk.Button(bar, text="Далее →", style="Accent.TButton", command=self.next)
        self.next_btn.pack(side="right")
        self.back_btn = ttk.Button(bar, text="← Назад", command=self.back)
        self.back_btn.pack(side="right", padx=8)
        self.render()
        W.center(self, app.root)
        self.grab_set()

    def render(self):
        for w in self.body.winfo_children():
            w.destroy()
        b = self.body
        self.back_btn.state(["!disabled"] if self.step else ["disabled"])
        if self.step == 0:
            ttk.Label(b, text="Шаг 1 из 3. Рабочая папка", style="Title.TLabel").pack(anchor="w")
            ttk.Label(b, wraplength=620, justify="left", text=(
                "Здесь хаб хранит загруженные данные, файлы для ваших моделей и отчёты. "
                "Обычно ничего менять не нужно.")).pack(anchor="w", pady=8)
            r = ttk.Frame(b)
            r.pack(fill="x")
            ttk.Entry(r, textvariable=self.folder, width=60).pack(side="left")
            ttk.Button(r, text="Выбрать…", command=lambda: self._pick(self.folder)).pack(side="left", padx=6)
        elif self.step == 1:
            ttk.Label(b, text="Шаг 2 из 3. Где лежат ваши модели Excel", style="Title.TLabel").pack(anchor="w")
            ttk.Label(b, wraplength=620, justify="left", text=(
                "С этой папки будет начинаться выбор файла, когда вы подключаете модель. "
                "Можно пропустить.")).pack(anchor="w", pady=8)
            r = ttk.Frame(b)
            r.pack(fill="x")
            ttk.Entry(r, textvariable=self.models, width=60).pack(side="left")
            ttk.Button(r, text="Выбрать…", command=lambda: self._pick(self.models)).pack(side="left", padx=6)
        elif self.step == 2:
            ttk.Label(b, text="Шаг 3 из 3. Автообновление", style="Title.TLabel").pack(anchor="w")
            ttk.Label(b, wraplength=620, justify="left", text=(
                "Хаб может сам обновлять данные в фоне — по расписанию каждого показателя, даже когда это окно "
                "закрыто. Выключить можно в любой момент на «Обзоре».")).pack(anchor="w", pady=8)
            ttk.Checkbutton(b, text="Включить автообновление", variable=self.auto).pack(anchor="w")
        else:
            ttk.Label(b, text="Готово!", style="Big.TLabel").pack(anchor="w")
            ttk.Label(b, wraplength=620, justify="left", text=(
                "Дальше:\n\n1. «Найти данные» → найдите показатель → «＋ В обновления».\n"
                "2. «Модели» → «Подключить модель» → «＋ Данные для модели».\n"
                "3. В модели: «Данные → Обновить всё». Данные появятся на листе «Хаб».")).pack(anchor="w", pady=8)
            self.next_btn.configure(text="Начать работу")

    def _pick(self, var):
        d = filedialog.askdirectory(parent=self, initialdir=var.get())
        if d:
            var.set(d)

    def back(self):
        self.step = max(0, self.step - 1)
        self.render()

    def next(self):
        if self.step == 0:
            folder = Path(self.folder.get().strip() or str(workdir()))
            if str(folder) != str(self.app.paths.root):
                set_workdir(folder)
                self.app.reopen()
            else:
                set_workdir(folder)
        elif self.step == 1:
            self.app.cfg.update_settings(models_folder=self.models.get().strip())
        elif self.step == 2:
            if self.auto.get() and winsched.is_enabled() is False:
                ok, msg = winsched.enable(self.app.cfg)
                if not ok:
                    self.app.status.set(msg)
        elif self.step == 3:
            self.app.cfg.update_settings(first_run_done=True)
            self.destroy()
            self.app.refresh_all()
            return
        self.step += 1
        self.render()
