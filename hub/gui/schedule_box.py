"""
Расписание показателя — как повторяющиеся встречи в календаре:
  • «Автоматически» — хаб сам оценивает, когда выходят данные;
  • «По расписанию» — список правил: каждый день, по дням недели, N-го числа, N-й рабочий день,
    раз в квартал (N дней после окончания квартала), раз в год, каждые N часов. Время — по Москве.
Отдельно — «Даты выхода»: конкретные даты из календаря публикаций (вставить списком).
"""
import datetime as dt
import tkinter as tk
from tkinter import messagebox, ttk

from ..core import schedule as S

KINDS = [
    ("daily", "Каждый день"),
    ("weekly", "По дням недели"),
    ("monthly", "Каждый месяц, в число"),
    ("workday", "Каждый месяц, в рабочий день"),
    ("quarterly", "Каждый квартал"),
    ("yearly", "Каждый год"),
    ("every", "Каждые N часов"),
]
DEFAULT_TIME = (10, 0)


def fmt_slots(slots) -> str:
    """Моменты проверок по московскому времени (как в правилах)."""
    return ", ".join(f"{S.to_msk(x):%d.%m %H:%M}" for x in slots)


class ScheduleBox(ttk.Frame):
    """get() → (строка расписания, список дат выхода); ValueError с понятным текстом при ошибке."""

    def __init__(self, parent, schedule="auto", release_dates=None, release_hint=""):
        super().__init__(parent)
        try:
            rules = S.parse_rules(schedule)
        except ValueError:
            rules = [("auto", None, None)]
        self.rules = [] if rules[0][0] == "auto" else list(rules)
        self.release = list(release_dates or [])
        self.release_hint = release_hint
        self.mode = tk.StringVar(value="rules" if self.rules else "auto")

        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Radiobutton(top, text="Автоматически — хаб сам поймёт, когда выходят данные", value="auto",
                        variable=self.mode, command=self._render).pack(anchor="w")
        ttk.Radiobutton(top, text="По расписанию (время — по Москве)", value="rules",
                        variable=self.mode, command=self._render).pack(anchor="w")

        self.rules_frame = ttk.Frame(self)
        lf = ttk.Frame(self.rules_frame)
        lf.pack(fill="x", padx=(22, 0), pady=(2, 0))
        self.lb = tk.Listbox(lf, height=4, activestyle="none", exportselection=False)
        self.lb.pack(side="left", fill="x", expand=True)
        self.lb.bind("<Double-1>", lambda e: self.edit_rule())
        btns = ttk.Frame(lf)
        btns.pack(side="left", padx=6, anchor="n")
        ttk.Button(btns, text="＋ Правило", command=self.add_rule).pack(fill="x")
        ttk.Button(btns, text="Изменить", command=self.edit_rule).pack(fill="x", pady=2)
        ttk.Button(btns, text="Удалить", command=self.del_rule).pack(fill="x")

        self.info = ttk.Label(self, style="Hint.TLabel", wraplength=560, justify="left")
        rel = ttk.Frame(self)
        ttk.Button(rel, text="Даты выхода…", command=self.edit_release).pack(side="left")
        self.rel_info = ttk.Label(rel, style="Hint.TLabel", wraplength=420, justify="left")
        self.rel_info.pack(side="left", padx=8)
        self.rel = rel
        self._render()

    # --- вид -------------------------------------------------------------------
    def _render(self):
        for w in (self.rules_frame, self.info, self.rel):
            w.pack_forget()
        if self.mode.get() == "rules":
            self.rules_frame.pack(fill="x")
            if not self.rules:
                self.rules.append(("weekly", (0,), DEFAULT_TIME))
        self.info.pack(anchor="w", pady=(2, 0))
        self.rel.pack(fill="x", pady=(6, 0))
        self.lb.delete(0, "end")
        for r in self.rules:
            self.lb.insert("end", S.human_rule(*r))
        self._describe()

    def _describe(self):
        if self.mode.get() == "auto":
            self.info.configure(text="Хаб оценит, когда выходят новые данные (по истории и датам выхода), "
                                     "и будет проверять чаще в эти дни.")
        else:
            try:
                sched, _ = self.get()
                nxt = S.next_slot(sched, dt.datetime.now(), count=3)
                every = [r for r in self.rules if r[0] == "every"]
                text = ("Ближайшие проверки (по Москве): " + fmt_slots(nxt)) if nxt else ""
                if every:
                    text += ("; " if text else "") + f"и каждые {every[0][1]} ч"
                self.info.configure(text=text, foreground="")
            except ValueError as e:
                self.info.configure(text=str(e), foreground="#c62828")
        now = dt.datetime.now()
        future = [m for m in S.release_moments({"release_dates": self.release}) if m >= now - dt.timedelta(hours=12)]
        if self.release:
            self.rel_info.configure(text=f"Дат выхода: {len(self.release)}"
                                    + (f", ближайшая {S.to_msk(min(future)):%d.%m.%Y %H:%M}" if future else
                                       " — все уже прошли, добавьте новые"))
        else:
            self.rel_info.configure(text=self.release_hint or "необязательно: конкретные даты из календаря "
                                                              "публикаций источника — в эти дни хаб проверит сразу")

    # --- правила ---------------------------------------------------------------
    def _selected(self):
        sel = self.lb.curselection()
        return sel[0] if sel else None

    def add_rule(self):
        RuleDialog(self, None, self._rule_saved)

    def edit_rule(self):
        i = self._selected()
        if i is None:
            if len(self.rules) == 1:
                i = 0
            else:
                messagebox.showinfo("Правило", "Выделите правило в списке.", parent=self)
                return
        RuleDialog(self, (i, self.rules[i]), self._rule_saved)

    def del_rule(self):
        i = self._selected()
        if i is not None:
            del self.rules[i]
            if not self.rules:
                self.mode.set("auto")
            self._render()

    def _rule_saved(self, index, rule):
        if index is None:
            self.rules.append(rule)
        else:
            self.rules[index] = rule
        self._render()

    # --- значение --------------------------------------------------------------
    def get(self):
        if self.mode.get() == "auto" or not self.rules:
            return "auto", list(self.release)
        sched = S.join_rules(self.rules)
        S.parse_rules(sched)
        return sched, list(self.release)

    def edit_release(self):
        ReleaseDialog(self, self.release, self._set_release)

    def _set_release(self, dates):
        self.release = dates
        self._describe()


class RuleDialog(tk.Toplevel):
    """Одно правило: что выбрать — видно сразу, лишних полей нет."""

    def __init__(self, box, current, on_done):
        super().__init__(box.winfo_toplevel())
        self.on_done = on_done
        self.index = current[0] if current else None
        kind, arg, at = current[1] if current else ("weekly", (0,), DEFAULT_TIME)
        self.title("Правило расписания")
        self.transient(box.winfo_toplevel())
        f = ttk.Frame(self, padding=16)
        f.pack(fill="both", expand=True)
        self.kind = tk.StringVar(value=dict(KINDS)[kind])
        cb = ttk.Combobox(f, textvariable=self.kind, values=[t for _, t in KINDS], state="readonly", width=40)
        cb.pack(anchor="w")
        cb.bind("<<ComboboxSelected>>", lambda e: self._render())
        self.opts = ttk.Frame(f)
        self.opts.pack(fill="x", pady=8)
        self.days = [tk.BooleanVar(value=kind == "weekly" and d in arg) for d in range(7)]
        if kind != "weekly":
            self.days[0].set(True)
        self.day_n = tk.StringVar(value=str(arg) if kind == "monthly" and arg != "last" else "15")
        self.month_last = tk.BooleanVar(value=kind == "monthly" and arg == "last")
        self.work_n = tk.StringVar(value=str(arg) if kind == "workday" and arg != "last" else "5")
        self.work_last = tk.BooleanVar(value=kind == "workday" and arg == "last")
        self.q_n = tk.StringVar(value=str(arg[1]) if kind == "quarterly" else "10")
        self.q_kind = tk.StringVar(value="календарный день" if kind == "quarterly" and arg[0] == "d"
                                   else "рабочий день")
        self.y_day = tk.StringVar(value=str(arg[1]) if kind == "yearly" else "1")
        self.y_month = tk.StringVar(value=S.MONTHS_GEN[(arg[0] if kind == "yearly" else 1) - 1])
        self.hours = tk.StringVar(value=str(arg) if kind == "every" else "6")
        self.time = tk.StringVar(value=f"{(at or DEFAULT_TIME)[0]:02d}:{(at or DEFAULT_TIME)[1]:02d}")
        self.preview = ttk.Label(f, style="Hint.TLabel", wraplength=480, justify="left")
        self.preview.pack(anchor="w")
        for v in (self.day_n, self.month_last, self.work_n, self.work_last, self.q_n, self.q_kind, self.y_day,
                  self.y_month, self.hours, self.time, *self.days):
            v.trace_add("write", lambda *_: self._preview())
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(12, 0))
        ttk.Button(bar, text="Сохранить", style="Accent.TButton", command=self.save).pack(side="right")
        ttk.Button(bar, text="Отмена", command=self.destroy).pack(side="right", padx=8)
        self._render()
        self.grab_set()

    def _kind(self):
        return next(k for k, t in KINDS if t == self.kind.get())

    def _render(self):
        for w in self.opts.winfo_children():
            w.destroy()
        k = self._kind()
        f = ttk.Frame(self.opts)
        f.pack(anchor="w")
        if k == "weekly":
            for d in range(7):
                ttk.Checkbutton(f, text=S.WEEKDAY_SHORT[d], variable=self.days[d]).pack(side="left", padx=(0, 4))
        elif k == "monthly":
            ttk.Spinbox(f, from_=1, to=31, textvariable=self.day_n, width=4).pack(side="left")
            ttk.Label(f, text="-го числа   или").pack(side="left", padx=4)
            ttk.Checkbutton(f, text="в последний день месяца", variable=self.month_last).pack(side="left")
        elif k == "workday":
            ttk.Spinbox(f, from_=1, to=23, textvariable=self.work_n, width=4).pack(side="left")
            ttk.Label(f, text="-й рабочий день   или").pack(side="left", padx=4)
            ttk.Checkbutton(f, text="в последний рабочий день", variable=self.work_last).pack(side="left")
        elif k == "quarterly":
            ttk.Spinbox(f, from_=1, to=90, textvariable=self.q_n, width=4).pack(side="left")
            ttk.Label(f, text="-й").pack(side="left")
            ttk.Combobox(f, textvariable=self.q_kind, values=["рабочий день", "календарный день"], state="readonly",
                         width=16).pack(side="left", padx=4)
            ttk.Label(f, text="после окончания квартала").pack(side="left")
        elif k == "yearly":
            ttk.Spinbox(f, from_=1, to=31, textvariable=self.y_day, width=4).pack(side="left")
            ttk.Combobox(f, textvariable=self.y_month, values=S.MONTHS_GEN, state="readonly",
                         width=10).pack(side="left", padx=4)
        elif k == "every":
            ttk.Label(f, text="каждые").pack(side="left")
            ttk.Spinbox(f, from_=1, to=168, textvariable=self.hours, width=4).pack(side="left", padx=4)
            ttk.Label(f, text="ч").pack(side="left")
        if k != "every":
            t = ttk.Frame(self.opts)
            t.pack(anchor="w", pady=(6, 0))
            ttk.Label(t, text="в").pack(side="left")
            ttk.Entry(t, textvariable=self.time, width=6).pack(side="left", padx=4)
            ttk.Label(t, text="по Москве (ЧЧ:ММ)", style="Hint.TLabel").pack(side="left")
        self._preview()

    def rule(self):
        k = self._kind()

        def num(var, lo, hi, what):
            v = var.get().strip()
            if not v.isdigit() or not lo <= int(v) <= hi:
                raise ValueError(f"{what}: число от {lo} до {hi}")
            return int(v)

        at = None if k == "every" else S._time(self.time.get().strip() or "10:00")
        if k == "weekly":
            days = tuple(d for d in range(7) if self.days[d].get())
            if not days:
                raise ValueError("Отметьте хотя бы один день недели")
            return "weekly", days, at
        if k == "monthly":
            return "monthly", "last" if self.month_last.get() else num(self.day_n, 1, 31, "Число"), at
        if k == "workday":
            return "workday", "last" if self.work_last.get() else num(self.work_n, 1, 23, "Рабочий день"), at
        if k == "quarterly":
            return "quarterly", ("wd" if self.q_kind.get() == "рабочий день" else "d",
                                 num(self.q_n, 1, 90, "День после квартала")), at
        if k == "yearly":
            return "yearly", (S.MONTHS_GEN.index(self.y_month.get()) + 1, num(self.y_day, 1, 31, "Число")), at
        if k == "every":
            return "every", num(self.hours, 1, 168, "Часы"), None
        return "daily", None, at

    def _preview(self):
        try:
            r = self.rule()
            nxt = S.next_slot(S.make(*r), dt.datetime.now(), count=3)
            self.preview.configure(text=S.human_rule(*r).capitalize() + (". Ближайшие: " + fmt_slots(nxt) if nxt else ""),
                                   foreground="")
        except ValueError as e:
            self.preview.configure(text=str(e), foreground="#c62828")

    def save(self):
        try:
            r = self.rule()
        except ValueError as e:
            messagebox.showwarning("Правило", str(e), parent=self)
            return
        self.destroy()
        self.on_done(self.index, r)


class ReleaseDialog(tk.Toplevel):
    def __init__(self, box, dates, on_done):
        super().__init__(box.winfo_toplevel())
        self.on_done = on_done
        self.title("Даты выхода данных")
        self.geometry("560x400")
        self.transient(box.winfo_toplevel())
        ttk.Label(self, padding=(12, 10, 12, 4), wraplength=530, justify="left", text=(
            "Конкретные даты, когда источник обещает новые данные — например, из календаря публикаций Росстата "
            "или Банка России. В эти моменты хаб проверит показатель (если данных ещё нет — повторит каждые "
            "2 часа до конца дня). Время — по Москве.\n\n"
            "Пишите даты по одной в строке или вставьте кусок календаря с сайта целиком — даты найдутся сами:")
                  ).pack(fill="x")
        self.text = tk.Text(self, height=6, wrap="word")
        self.text.pack(fill="both", expand=True, padx=12)
        self.text.insert("1.0", "\n".join(S.fmt_moment(d) for d in dates))
        ttk.Label(self, padding=(12, 2), style="Hint.TLabel", text="Например:  21.10.2026 19:00   или   "
                                                                 "18 ноября   или   2026-12-16").pack(anchor="w")
        self.found = ttk.Label(self, padding=(12, 4), wraplength=530, justify="left")
        self.found.pack(fill="x")
        self.text.bind("<<Modified>>", self._changed)
        bar = ttk.Frame(self, padding=12)
        bar.pack(fill="x")
        ttk.Button(bar, text="Сохранить", style="Accent.TButton", command=self.save).pack(side="right")
        ttk.Button(bar, text="Отмена", command=self.destroy).pack(side="right", padx=8)
        ttk.Button(bar, text="Очистить", command=lambda: (self.text.delete("1.0", "end"), self._changed())).pack(side="left")
        self._changed()
        self.grab_set()

    def dates(self):
        return S.parse_release_text(self.text.get("1.0", "end"))

    def _changed(self, *_):
        self.text.edit_modified(False)
        d = self.dates()
        self.found.configure(text=(f"Нашёл дат: {len(d)}: " + ", ".join(S.fmt_moment(x) for x in d[:8])
                                   + ("…" if len(d) > 8 else "")) if d else "Дат пока нет.")

    def save(self):
        d = self.dates()
        if not d and self.text.get("1.0", "end").strip():
            if not messagebox.askyesno("Даты выхода", "В тексте не нашлось ни одной даты. Очистить список?",
                                       parent=self):
                return
        self.on_done(d)
        self.destroy()
