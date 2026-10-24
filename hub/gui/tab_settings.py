"""«Настройки» — рабочая папка, модели, формулы, уведомления, хранение, дополнительные выгрузки."""
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ..core import winsched
from ..core.paths import set_workdir
from ..excel.exports import open_file
from . import widgets as W


class SettingsTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=16)
        self.app = app
        self.vars = {}
        g = ttk.Frame(self)
        g.pack(fill="x", anchor="n")
        r = 0

        g.columnconfigure(1, weight=1)

        def row(label, widget, hint=""):
            """Подпись | настройка; подсказка — под настройкой, по ширине окна."""
            nonlocal r
            ttk.Label(g, text=label).grid(row=r, column=0, sticky="nw", pady=(6, 0), padx=(0, 16))
            widget.grid(row=r, column=1, sticky="w", pady=(6, 0))
            r += 1
            if hint:
                W.autowrap(ttk.Label(g, text=hint, style="Hint.TLabel", justify="left")).grid(
                    row=r, column=1, sticky="ew", pady=(0, 2))
                r += 1

        self.workdir = tk.StringVar()
        wf = ttk.Frame(g)
        ttk.Entry(wf, textvariable=self.workdir, width=50, state="readonly").pack(side="left")
        ttk.Button(wf, text="Изменить…", command=self.change_workdir).pack(side="left", padx=4)
        ttk.Button(wf, text="Открыть", command=lambda: open_file(self.app.paths.root)).pack(side="left")
        row("Рабочая папка", wf, "Здесь база, файлы для моделей, отчёты и выгрузки.")

        from ..core.paths import PROJECT
        from .. import VERSION
        pf = ttk.Frame(g)
        ttk.Label(pf, text=f"{PROJECT}  (версия {VERSION})").pack(side="left")
        ttk.Button(pf, text="Открыть", command=lambda: open_file(PROJECT)).pack(side="left", padx=4)
        row("Папка программы", pf, "Обновление распаковывайте именно в эту папку (с заменой файлов). Ярлык «Хаб данных» "
                                   "на рабочем столе хаб сам направляет сюда.")

        self.vars["models_folder"] = tk.StringVar()
        mf = ttk.Frame(g)
        ttk.Entry(mf, textvariable=self.vars["models_folder"], width=50).pack(side="left")
        ttk.Button(mf, text="Выбрать…", command=self.pick_models).pack(side="left", padx=4)
        row("Папка с моделями", mf, "С неё начинается выбор файла при «Подключить модель».")

        self.vars["formula_locale"] = tk.StringVar()
        lf = ttk.Frame(g)
        ttk.Radiobutton(lf, text="Excel на русском", value="ru", variable=self.vars["formula_locale"]).pack(side="left")
        ttk.Radiobutton(lf, text="на английском", value="en", variable=self.vars["formula_locale"]).pack(side="left", padx=8)
        row("Язык формул", lf)

        self.auto = tk.BooleanVar()
        af = ttk.Frame(g)
        self.auto_chk = ttk.Checkbutton(af, text=f"Обновлять в фоне ({winsched.EVERY_TEXT}) и при входе в Windows",
                                        variable=self.auto, command=self.toggle_auto)
        self.auto_chk.pack(side="left")
        ttk.Button(af, text="Открыть Планировщик заданий", command=winsched.open_scheduler).pack(side="left", padx=8)
        row("Автообновление", af,
            f"Задача называется «{winsched.TASK_NAME}» и лежит прямо в «Библиотеке планировщика заданий» "
            "(слева вверху списка). Если её не видно — нажмите F5. Кроме запуска каждые 15 минут, в задаче есть "
            "запуски точно во время из графиков показателей; хаб переписывает их сам, когда вы меняете графики, "
            "и сам чинит задачу, если папку с программой перенесли.")

        for key, label, hint in (
                ("notifications", "Показывать уведомления Windows", "Только когда есть новые данные в моделях или проблемы."),
                ("per_job_files", "Также сохранять файл по каждому показателю",
                 "Постоянный .xlsx в «Выгрузки/Показатели» — для тех, кто хочет внешние ссылки. Основной путь — лист «Хаб» в модели."),
                ("nightly_model_refresh", "Ночью обновлять закрытые модели",
                 "Хаб откроет модель в фоне, нажмёт «Обновить всё» и сохранит (с резервной копией).")):
            self.vars[key] = tk.BooleanVar()
            row("", ttk.Checkbutton(g, text=label, variable=self.vars[key]), hint)

        self.vars["keep_raw_days"] = tk.StringVar()
        row("Хранить сырые ответы, дней", ttk.Entry(g, textvariable=self.vars["keep_raw_days"], width=8),
            "Архив ответов сайтов — пригодится, если что-то сломается.")

        self.vars["full_reload_days"] = tk.StringVar()
        row("Всю историю заново — раз в, дней", ttk.Entry(g, textvariable=self.vars["full_reload_days"], width=8),
            "Для показателей, которые хаб дозагружает (ЕМИСС, формы на сайтах, UN Comtrade): обычно берутся только "
            "свежие данные, а раз в столько дней — вся история (источник мог исправить старые значения). 0 — никогда. "
            "У отдельного показателя можно задать своё число: «Мои показатели» → ◷ → «Проверки новых данных».")

        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=16)
        ttk.Button(bar, text="Сохранить настройки", style="Accent.TButton", command=self.save).pack(side="left")

    def refresh(self):
        s = self.app.cfg.settings()
        self.workdir.set(str(self.app.paths.root))
        for k, v in self.vars.items():
            v.set(s.get(k, "") if not isinstance(v, tk.BooleanVar) else bool(s.get(k)))
        en = winsched.is_enabled()
        self.auto.set(bool(en))
        if en is None:
            self.auto_chk.state(["disabled"])

    def save(self):
        s = self.app.cfg.settings()
        for k, v in self.vars.items():
            s[k] = v.get()
        try:
            s["keep_raw_days"] = int(s["keep_raw_days"])
        except (TypeError, ValueError):
            s["keep_raw_days"] = 90
        try:
            s["full_reload_days"] = max(0, int(s["full_reload_days"]))
        except (TypeError, ValueError):
            s["full_reload_days"] = 30
        self.app.cfg.save_settings(s)
        self.app.status.set("Настройки сохранены")

    def toggle_auto(self):
        ok, msg = winsched.enable(self.app.cfg) if self.auto.get() else winsched.disable()
        if not ok:
            self.auto.set(not self.auto.get())
            messagebox.showwarning("Автообновление", msg, parent=self)
        self.app.status.set(msg)

    def pick_models(self):
        d = filedialog.askdirectory(parent=self, title="Папка с моделями")
        if d:
            self.vars["models_folder"].set(d)

    def change_workdir(self):
        d = filedialog.askdirectory(parent=self, title="Новая рабочая папка")
        if not d:
            return
        if not messagebox.askyesno("Рабочая папка", f"Переключиться на папку {d}?\n\nСтарая папка останется на месте. "
                                                    "Если хотите перенести данные — скопируйте её содержимое в новую "
                                                    "папку до переключения. Пути к файлам данных в моделях после "
                                                    "переезда нужно будет переподключить.", parent=self):
            return
        set_workdir(d)
        self.app.reopen()
        self.refresh()
