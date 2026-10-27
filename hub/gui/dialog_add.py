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

def future_row(parent, value=False):
    """Галочка «Содержит прогнозные данные» (даты в будущем — без предупреждений). → BooleanVar."""
    var = tk.BooleanVar(value=bool(value))
    ttk.Checkbutton(parent, variable=var, text="Содержит прогнозные данные").pack(anchor="w", pady=(4, 0))
    W.autowrap(ttk.Label(parent, style="Hint.TLabel", justify="left", text=(
        "Даты в будущем (прогнозы, данные за текущий месяц, которые выходят до его конца) загружаются без "
        "предупреждений. Хаб ставит эту галочку сам, если вы приняли такие данные в окне «Отложенные данные»."))
        ).pack(fill="x")
    return var


def reload_row(parent, app, value=None):
    """Строка «Всю историю заново — раз в N дней» (для показателей с дозагрузкой). → StringVar."""
    default = app.cfg.full_reload_days(None)
    var = tk.StringVar(value="" if value is None else str(value))
    r = ttk.Frame(parent)
    r.pack(fill="x", pady=2)
    ttk.Label(r, text="Всю историю заново — раз в, дней:").pack(side="left")
    ttk.Entry(r, textvariable=var, width=6).pack(side="left", padx=6)
    W.autowrap(ttk.Label(parent, style="Hint.TLabel", justify="left", text=(
        "Обычно хаб берёт у источника только свежие данные, а раз в столько дней — всю историю заново (источник мог "
        f"исправить старые значения). Пусто — как в «Настройках» (сейчас {default or 'никогда'}"
        f"{' дн.' if default else ''}); 0 — никогда."))).pack(fill="x")
    return var


def reload_value(var):
    """Значение из reload_row: None (как в настройках) или число дней ≥ 0; ValueError — не число."""
    t = var.get().strip()
    if not t:
        return None
    n = int(t)
    if n < 0:
        raise ValueError(t)
    return n


def give_sheets(app, model, job_id, parent=None):
    """Листы файла — в модель: подписать модель и предложить сразу добавить листы в книгу (через Excel, модель
    должна быть закрыта). Без этого шага файлы готовы, но листов в модели ещё нет."""
    added = app.cfg.subscribe_sheets(model, job_id)
    app.models._rebuild()
    m = app.cfg.model(model) or {}
    if messagebox.askyesno("Листы в модель", ("Модель получает эти листы. " if added else "Модель уже получает эти "
                           "листы. ") + "Добавить листы в книгу модели сейчас? Модель должна быть закрыта в Excel — "
                           "хаб сделает резервную копию и добавит листы (остальное в модели не меняется).",
                           parent=parent or app.root):
        if m.get("path"):
            app.models._run_connect(model, m["path"])


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
        self.future = future_row(more.body, checks.get("future_ok"))
        self.reload = reload_row(more.body, app) if self.src.incremental(params) else None
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
        if self.future.get():
            checks["future_ok"] = True
        tags = [t.strip() for t in self.tags.get().split(",") if t.strip()]
        extra = {"release_dates": release} if release else {}
        if self.reload is not None:
            try:
                days = reload_value(self.reload)
            except ValueError:
                messagebox.showwarning("Вся история", "Число дней — целое число, например 30 (0 — никогда).",
                                       parent=self)
                return
            if days is not None:
                extra["full_reload_days"] = days
        job = self.app.cfg.add_job(self.source, name, self.params, schedule=schedule, checks=checks, tags=tags,
                                   extra=extra or None)
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
                text += ("\n\nЧтобы листы появились в модели, нажмите «Отдать в модель …»." if self.source == "file_sheets"
                         else "\n\nЧтобы данные шли в модель, нажмите «Отдать в модель …» и отметьте, что именно "
                              "нужно (товар, регион, период…). Показатель целиком в модель не уходит.")
            self.result.configure(text=text, style="green.TLabel")
        elif o:
            what = E.CATEGORIES.get(o.category or E.INTERNAL, E.CATEGORIES[E.INTERNAL])[1]
            self.result.configure(text=f"Показатель добавлен, но загрузить данные не удалось: {o.message}.\n{what}. "
                                       f"Хаб попробует снова сам; статус виден на «Обзоре».", style="red.TLabel")
        self._finish()

    def _select_for(self, model):
        if self.source == "file_sheets":
            give_sheets(self.app, model, self.job["id"], parent=self)
            return
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
