"""«Настройки» — рабочая папка, модели, формулы, уведомления, хранение, дополнительные выгрузки."""
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ..core import winsched
from ..core.paths import set_workdir
from ..excel.exports import open_file


class SettingsTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=16)
        self.app = app
        self.vars = {}
        g = ttk.Frame(self)
        g.pack(fill="x", anchor="n")
        r = 0

        def row(label, widget, hint=""):
            nonlocal r
            ttk.Label(g, text=label).grid(row=r, column=0, sticky="w", pady=6, padx=(0, 16))
            widget.grid(row=r, column=1, sticky="w")
            if hint:
                ttk.Label(g, text=hint, style="Hint.TLabel", wraplength=560, justify="left").grid(row=r, column=2, sticky="w", padx=12)
            r += 1

        self.workdir = tk.StringVar()
        wf = ttk.Frame(g)
        ttk.Entry(wf, textvariable=self.workdir, width=50, state="readonly").pack(side="left")
        ttk.Button(wf, text="Изменить…", command=self.change_workdir).pack(side="left", padx=4)
        ttk.Button(wf, text="Открыть", command=lambda: open_file(self.app.paths.root)).pack(side="left")
        row("Рабочая папка", wf, "Здесь база, файлы для моделей, отчёты и выгрузки.")

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
        self.auto_chk = ttk.Checkbutton(g, text="Обновлять в фоне каждый час и при входе в Windows",
                                        variable=self.auto, command=self.toggle_auto)
        row("Автообновление", self.auto_chk)

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

        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=16)
        ttk.Button(bar, text="Сохранить настройки", style="Accent.TButton", command=self.save).pack(side="left")
        ttk.Button(bar, text="Мастер первого запуска", command=self.app.first_run).pack(side="left", padx=8)
        ttk.Button(bar, text="Добавить примеры: Metro, Металл Торг, Fearnleys",
                   command=self.add_examples).pack(side="left")

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
        self.app.cfg.save_settings(s)
        self.app.status.set("Настройки сохранены")

    def toggle_auto(self):
        ok, msg = winsched.enable() if self.auto.get() else winsched.disable()
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

    def add_examples(self):
        from ..core.examples import add_examples
        added = add_examples(self.app.cfg)
        if not added:
            messagebox.showinfo("Примеры", "Примеры уже добавлены — они на вкладке «Мои показатели».", parent=self)
            return
        messagebox.showinfo("Примеры", f"Добавлено показателей: {len(added)}. Сейчас хаб попробует их загрузить. "
                                       "Сайты с защитой или скриптами (Metro, Fearnleys) читаются встроенным браузером.",
                            parent=self)
        self.app.update_jobs(added)
