"""
Выбор расписания показателя: «как часто проверять» (дни недели, время, N-й рабочий день…)
и необязательный график выхода данных — даты, вставленные из календаря публикаций источника.
"""
import datetime as dt
import tkinter as tk
from tkinter import messagebox, ttk

from ..core import schedule as S

MODES = [
    ("auto", "Автоматически — хаб сам поймёт, когда выходят данные"),
    ("daily", "Каждый день"),
    ("weekly", "По дням недели"),
    ("monthly", "Раз в месяц, в число"),
    ("workday", "Раз в месяц, в N-й рабочий день (с учётом праздников)"),
    ("every", "Каждые N часов"),
]


class ScheduleBox(ttk.Frame):
    """get() → (строка расписания, список дат выхода); ValueError с понятным текстом при ошибке."""

    def __init__(self, parent, schedule="auto", release_dates=None, release_hint=""):
        super().__init__(parent)
        try:
            kind, arg, at = S.parse(schedule)
        except ValueError:
            kind, arg, at = "auto", None, None
        self.release = list(release_dates or [])
        self.release_hint = release_hint
        self.mode = tk.StringVar(value=kind)
        self.days = [tk.BooleanVar(value=kind == "weekly" and d in arg) for d in range(7)]
        if kind != "weekly":
            self.days[0].set(True)
        self.day_n = tk.StringVar(value=str(arg) if kind == "monthly" else "1")
        self.work_n = tk.StringVar(value=str(arg) if kind == "workday" else "1")
        self.hours = tk.StringVar(value=str(arg) if kind == "every" else "6")
        self.time = tk.StringVar(value=f"{at[0]:02d}:{at[1]:02d}" if at else "")

        top = ttk.Frame(self)
        top.pack(fill="x")
        names = dict(MODES)
        self.combo = ttk.Combobox(top, values=[t for _, t in MODES], state="readonly", width=52)
        self.combo.set(names[kind])
        self.combo.pack(side="left")
        self.combo.bind("<<ComboboxSelected>>", lambda e: self._mode_changed())
        self.opts = ttk.Frame(self)
        self.opts.pack(fill="x", pady=(4, 0))
        self.info = ttk.Label(self, style="Hint.TLabel", wraplength=560, justify="left")
        self.info.pack(anchor="w", pady=(2, 0))

        rel = ttk.Frame(self)
        rel.pack(fill="x", pady=(6, 0))
        ttk.Button(rel, text="График выхода…", command=self.edit_release).pack(side="left")
        self.rel_info = ttk.Label(rel, style="Hint.TLabel", wraplength=420, justify="left")
        self.rel_info.pack(side="left", padx=8)
        for v in (self.day_n, self.work_n, self.hours, self.time, *self.days):
            v.trace_add("write", lambda *_: self._describe())
        self._render()

    # --- вид -------------------------------------------------------------------
    def _mode_changed(self):
        text = self.combo.get()
        self.mode.set(next(k for k, t in MODES if t == text))
        self._render()

    def _render(self):
        for w in self.opts.winfo_children():
            w.destroy()
        k = self.mode.get()
        f = self.opts
        if k == "weekly":
            for d in range(7):
                ttk.Checkbutton(f, text=S.WEEKDAY_SHORT[d], variable=self.days[d]).pack(side="left", padx=(0, 4))
        elif k == "monthly":
            ttk.Label(f, text="число:").pack(side="left")
            ttk.Spinbox(f, from_=1, to=31, textvariable=self.day_n, width=4).pack(side="left", padx=4)
        elif k == "workday":
            ttk.Label(f, text="рабочий день №").pack(side="left")
            ttk.Spinbox(f, from_=1, to=23, textvariable=self.work_n, width=4).pack(side="left", padx=4)
        elif k == "every":
            ttk.Label(f, text="каждые").pack(side="left")
            ttk.Spinbox(f, from_=1, to=168, textvariable=self.hours, width=4).pack(side="left", padx=4)
            ttk.Label(f, text="ч").pack(side="left")
        if k in ("daily", "weekly", "monthly", "workday"):
            ttk.Label(f, text="  в").pack(side="left")
            ttk.Entry(f, textvariable=self.time, width=6).pack(side="left", padx=4)
            ttk.Label(f, text="ЧЧ:ММ, пусто — в 06:00", style="Hint.TLabel").pack(side="left")
        self._describe()

    def _describe(self):
        try:
            sched, _ = self.get()
            nxt = S.next_slot(sched, dt.datetime.now())
            text = "Проверять: " + S.human(sched)
            if nxt:
                text += f". Ближайшая проверка: {nxt:%d.%m.%Y %H:%M}"
            if sched == "auto":
                text = ("Хаб сам оценит, когда выходят новые данные (по истории и графику выхода), "
                        "и будет проверять чаще в эти дни.")
            self.info.configure(text=text, foreground="")
        except ValueError as e:
            self.info.configure(text=str(e), foreground="#c62828")
        now = dt.datetime.now()
        future = [m for m in (S.parse_moment(x) for x in self.release) if m and m >= now - dt.timedelta(hours=12)]
        if self.release:
            self.rel_info.configure(text=f"Дат в графике: {len(self.release)}"
                                    + (f", ближайшая {min(future):%d.%m.%Y %H:%M}" if future else
                                       " — все уже прошли, вставьте новый график"))
        else:
            self.rel_info.configure(text=self.release_hint or "необязательно: даты выхода из календаря публикаций")

    # --- значение --------------------------------------------------------------
    def get(self):
        k = self.mode.get()
        at = S._time(self.time.get().strip()) if self.time.get().strip() and k not in ("auto", "every") else None
        if k == "weekly":
            days = [d for d in range(7) if self.days[d].get()]
            if not days:
                raise ValueError("Отметьте хотя бы один день недели")
            sched = S.make("weekly", days, at)
        elif k in ("monthly", "workday", "every"):
            raw = {"monthly": self.day_n, "workday": self.work_n, "every": self.hours}[k].get().strip()
            if not raw.isdigit():
                raise ValueError("Укажите число")
            sched = S.make(k, int(raw), at)
        else:
            sched = S.make(k, None, at)
        S.parse(sched)
        return sched, list(self.release)

    def edit_release(self):
        ReleaseDialog(self, self.release, self._set_release)

    def _set_release(self, dates):
        self.release = dates
        self._describe()


class ReleaseDialog(tk.Toplevel):
    def __init__(self, box, dates, on_done):
        super().__init__(box.winfo_toplevel())
        self.on_done = on_done
        self.title("График выхода данных")
        self.geometry("640x520")
        self.transient(box.winfo_toplevel())
        ttk.Label(self, padding=(12, 10, 12, 4), wraplength=600, justify="left", text=(
            "Вставьте даты, когда источник обещает новые данные: скопируйте календарь публикаций с сайта "
            "(Росстат, Банк России…) целиком или напишите даты сами, по одной в строке. Подойдут записи "
            "«21.10.2026», «2026-10-21», «21 октября». Время рядом с датой («21.10.2026 19:00») тоже учтётся.\n"
            "В эти моменты хаб проверит показатель, а если данных ещё нет — повторит каждые 2 часа до конца дня. "
            "Остальное расписание продолжает работать.")).pack(fill="x")
        self.text = tk.Text(self, height=14, wrap="word")
        self.text.pack(fill="both", expand=True, padx=12)
        self.text.insert("1.0", "\n".join(S.fmt_moment(d) for d in dates))
        self.found = ttk.Label(self, padding=(12, 4), style="Hint.TLabel", wraplength=600, justify="left")
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
        self.found.configure(text=f"Нашёл дат: {len(d)}" + (": " + ", ".join(S.fmt_moment(x) for x in d[:8])
                                                             + ("…" if len(d) > 8 else "") if d else ""))

    def save(self):
        d = self.dates()
        if not d and self.text.get("1.0", "end").strip():
            if not messagebox.askyesno("График", "В тексте не нашлось ни одной даты. Очистить график?", parent=self):
                return
        self.on_done(d)
        self.destroy()
