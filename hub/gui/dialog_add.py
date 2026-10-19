"""
Диалог «＋ В обновления» — три вопроса (раздел 3 ТЗ):
  1. Название (предзаполнено);
  2. В какие модели отдавать (галки, можно пропустить);
  3. Как часто проверять (по умолчанию «Автоматически»).
Остальное — под свёрнутым «Дополнительно». После «Добавить» сразу идёт пробная загрузка,
и в этом же окне показывается результат: «Получено 3 ряда, 2010–2025, последнее значение …».
"""
import tkinter as tk
from tkinter import messagebox, ttk

from .. import sources
from ..core import errors as E
from ..core import schedule as S
from . import widgets as W


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

        ttk.Label(body, text="2. В какие модели отдавать", style="Title.TLabel").pack(anchor="w")
        self.model_vars = {}
        models = [m for m in app.cfg.models() if m.get("active", True)]
        mf = ttk.Frame(body)
        mf.pack(fill="x", pady=(2, 10))
        if not models:
            ttk.Label(mf, text="Моделей пока нет — можно пропустить и подключить модель позже на вкладке «Модели».",
                      style="Muted.TLabel").pack(anchor="w")
        for i, m in enumerate(models):
            v = tk.BooleanVar(value=False)
            self.model_vars[m["name"]] = v
            ttk.Checkbutton(mf, text=m["name"], variable=v).grid(row=i // 3, column=i % 3, sticky="w", padx=(0, 24))

        ttk.Label(body, text="3. Как часто проверять", style="Title.TLabel").pack(anchor="w")
        default = schedule or self.src.default_schedule(params)
        self.choices = list(S.CHOICES)
        if default not in dict(self.choices):
            self.choices.append((default, S.human(default)))
        self.sched = tk.StringVar(value=dict(self.choices)[default])
        ttk.Combobox(body, textvariable=self.sched, values=[t for _, t in self.choices], state="readonly",
                     width=45).pack(anchor="w", pady=(2, 6))
        hint = note or ("На сайте видно только текущее значение — хаб будет сохранять его каждый день "
                        "и сам построит историю." if self.src.snapshot else
                        "Хаб сам поймёт, когда выходят новые данные, и будет проверять чаще в эти дни.")
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
        ttk.Label(r, text="Своё расписание (daily, weekly:Wed, monthly:15, every:6h):").pack(side="left")
        self.custom = tk.StringVar()
        ttk.Entry(r, textvariable=self.custom, width=16).pack(side="left", padx=6)
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

    def _schedule(self):
        custom = self.custom.get().strip()
        if custom:
            S.parse(custom)
            return custom
        text = self.sched.get()
        return next(k for k, t in self.choices if t == text)

    def add(self):
        name = self.name.get().strip()
        if not name:
            messagebox.showwarning("Название", "Введите название показателя.", parent=self)
            return
        try:
            schedule = self._schedule()
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
        job = self.app.cfg.add_job(self.source, name, self.params, schedule=schedule, checks=checks, tags=tags)
        for m, v in self.model_vars.items():
            if v.get():
                self.app.cfg.subscribe(m, job_id=job["id"])
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
        if o and o.status in ("updated", "unchanged", "waiting", "delayed"):
            text = "✔ Получено: " + (o.summary or o.message)
            if rep.models_new:
                text += "\nВ моделях " + ", ".join(n for n, _ in rep.models_new) + " появятся новые столбцы после «Данные → Обновить всё»."
            self.result.configure(text=text, style="green.TLabel")
        elif o:
            what = E.CATEGORIES.get(o.category or E.INTERNAL, E.CATEGORIES[E.INTERNAL])[1]
            self.result.configure(text=f"Показатель добавлен, но загрузить данные не удалось: {o.message}.\n{what}. "
                                       f"Хаб попробует снова сам; статус виден на «Обзоре».", style="red.TLabel")
        self._finish()

    def _failed(self, exc):
        self.result.configure(text="Показатель добавлен, но пробная загрузка не удалась: " + E.human_message(exc),
                              style="red.TLabel")
        self._finish()

    def _finish(self):
        for w in self.bar.winfo_children():
            w.destroy()
        ttk.Button(self.bar, text="Готово", style="Accent.TButton", command=self.destroy).pack(side="right")
        ttk.Button(self.bar, text="Открыть «Мои показатели»",
                   command=lambda: (self.destroy(), self.app.show(self.app.jobs), self.app.jobs.select(self.job["id"]))
                   ).pack(side="right", padx=8)
        self.app.refresh_all()
        if self.on_added:
            self.on_added(self.job)
