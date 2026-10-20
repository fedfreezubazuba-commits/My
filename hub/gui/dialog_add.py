"""
Диалог «＋ В обновления» — два вопроса (раздел 3 ТЗ):
  1. Название (предзаполнено);
  2. Как часто проверять (по умолчанию «Автоматически»).
Что из показателя получает модель, настраивается отдельно — выборкой («Данные для модели»):
после пробной загрузки здесь есть кнопки «Отдать в модель …».
Остальное — под свёрнутым «Дополнительно». После «Добавить» сразу идёт пробная загрузка,
и в этом же окне показывается результат: «Получено 3 ряда, 2010–2025, последнее значение …».
"""
import tkinter as tk
from tkinter import messagebox, ttk

from .. import sources
from ..core import errors as E
from . import widgets as W
from .schedule_box import ScheduleBox

class AddDialog(tk.Toplevel):
    def __init__(self, app, source, params, name, note="", checks=None, schedule=None, on_added=None):
        super().__init__(app.root)
        self.app = app
        self.source = source
        self.params = params
        self.on_added = on_added
        self.src = sources.get(source)
        self.title("Добавить в обновления")
        self.resizable(True, False)
        self.transient(app.root)
        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)
        self.body = body

        ttk.Label(body, text="1. Название", style="Title.TLabel").pack(anchor="w")
        self.name = tk.StringVar(value=name[:150])
        e = ttk.Entry(body, textvariable=self.name, width=80)
        e.pack(fill="x", pady=(2, 10))
        e.focus_set()

        ttk.Label(body, text="2. Как часто проверять", style="Title.TLabel").pack(anchor="w")
        default = schedule or self.src.default_schedule(params)
        self.sched_box = ScheduleBox(body, default)
        self.sched_box.pack(fill="x", pady=(2, 6))
        hint = note or ("На сайте видно только текущее значение — хаб будет сохранять его при каждой проверке "
                        "и сам построит историю." if self.src.snapshot else "")
        if hint:
            ttk.Label(body, text=hint, style="Hint.TLabel", wraplength=560, justify="left").pack(anchor="w", pady=(0, 6))

        more = W.Collapsible(body, "Дополнительно")
        more.pack(fill="x", pady=(4, 6))
        checks = dict(checks if checks is not None else self.src.default_checks(params))
        self.jump = tk.StringVar(value=str(checks.get("max_jump_pct") or ""))
        r = ttk.Frame(more.body)
        r.pack(fill="x", pady=2)
        ttk.Label(r, text="Отложить данные на проверку, если значение изменилось больше чем на, %:").pack(side="left")
        ttk.Entry(r, textvariable=self.jump, width=8).pack(side="left", padx=6)
        r = ttk.Frame(more.body)
        r.pack(fill="x", pady=2)
        ttk.Label(r, text="Метки через запятую:").pack(side="left")
        self.tags = tk.StringVar()
        ttk.Entry(r, textvariable=self.tags, width=30).pack(side="left", padx=6)
        ttk.Label(more.body, text="Источник: " + self.src.describe(params), style="Hint.TLabel",
                  wraplength=560, justify="left").pack(anchor="w", pady=(6, 0))

        self.result = ttk.Label(body, text="", wraplength=600, justify="left")
        self.result.pack(anchor="w", pady=(8, 0))
        self.bar = ttk.Frame(body)
        self.bar.pack(fill="x", pady=(12, 0))
        self.add_btn = ttk.Button(self.bar, text="Добавить", style="Accent.TButton", command=self.add)
        self.add_btn.pack(side="right")
        self.cancel_btn = ttk.Button(self.bar, text="Отмена", command=self.destroy)
        self.cancel_btn.pack(side="right", padx=8)
        self.bind("<Return>", lambda e: self.add())
        self.bind("<Escape>", lambda e: self.destroy())
        W.center(self, app.root)
        self.grab_set()

    def add(self):
        name = self.name.get().strip()
        if not name:
            messagebox.showwarning("Название", "Введите название показателя.", parent=self)
            return
        try:
            schedule, release = self.sched_box.get()
        except ValueError as e:
            messagebox.showwarning("Расписание", str(e), parent=self)
            return
        checks = self.src.default_checks(self.params)
        j = self.jump.get().strip().replace(",", ".")
        if j:
            try:
                checks["max_jump_pct"] = float(j)
            except ValueError:
                messagebox.showwarning("Проверка", "Порог скачка — это число, например 50.", parent=self)
                return
        else:
            checks.pop("max_jump_pct", None)
        tags = [t.strip() for t in self.tags.get().split(",") if t.strip()]
        job = self.app.cfg.add_job(self.source, name, self.params, schedule=schedule, checks=checks, tags=tags,
                                   extra={"release_dates": release} if release else None)
        self.job = job
        self.add_btn.state(["disabled"])
        self.cancel_btn.state(["disabled"])
        self.result.configure(text="Показатель добавлен. Пробная загрузка…", style="Muted.TLabel")

        def work():
            r = self.app.runner()
            try:
                return r.run(job_ids=[job["id"]], write_report=False)
            finally:
                r.close()

        self.app.bg(work, self._loaded, f"Пробная загрузка: {name}", error=self._failed)

    def _loaded(self, rep):
        o = rep.outcomes[0] if rep.outcomes else None
        self.loaded = False
        if o and o.status in ("updated", "unchanged", "waiting", "delayed"):
            self.loaded = True
            text = "✔ Получено: " + (o.summary or o.message)
            if any(m.get("active", True) for m in self.app.cfg.models()):
                text += ("\n\nЧтобы данные шли в модель, нажмите «Отдать в модель …» и отметьте, что именно нужно "
                         "(товар, регион, период…). Показатель целиком в модель не уходит.")
            self.result.configure(text=text, style="green.TLabel")
        elif o:
            what = E.CATEGORIES.get(o.category or E.INTERNAL, E.CATEGORIES[E.INTERNAL])[1]
            self.result.configure(text=f"Показатель добавлен, но загрузить данные не удалось: {o.message}.\n{what}. "
                                       f"Хаб попробует снова сам; статус виден на «Обзоре».", style="red.TLabel")
        self._finish()

    def _select_for(self, model):
        from .dialog_select import SelectionDialog
        SelectionDialog(self.app, model, job_id=self.job["id"], on_done=self.app.models._rebuild)

    def _failed(self, exc):
        self.result.configure(text="Показатель добавлен, но пробная загрузка не удалась: " + E.human_message(exc),
                              style="red.TLabel")
        self._finish()

    def _finish(self):
        for w in self.bar.winfo_children():
            w.destroy()
        ttk.Button(self.bar, text="Готово", style="Accent.TButton", command=self.destroy).pack(side="right")
        models = [m["name"] for m in self.app.cfg.models() if m.get("active", True)]
        for m in models[:3] if getattr(self, "loaded", False) else []:
            ttk.Button(self.bar, text=f"Отдать в модель «{m[:30]}»…",
                       command=lambda m=m: self._select_for(m)).pack(side="left", padx=(0, 8))
        ttk.Button(self.bar, text="Открыть «Мои показатели»",
                   command=lambda: (self.destroy(), self.app.show(self.app.jobs), self.app.jobs.select(self.job["id"]))
                   ).pack(side="right", padx=8)
        self.app.refresh_all()
        if self.on_added:
            self.on_added(self.job)
