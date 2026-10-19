"""«Мои показатели» — все показатели в обновлении, поиск, фильтры и действия."""
import tkinter as tk
import webbrowser
from tkinter import messagebox, simpledialog, ttk

from .. import sources
from ..core import schedule as S
from ..core import status as ST
from ..core.base_fmt import fmt, fmt_date
from ..core.periods import PERIODICITY_RU
from ..excel import exports, formulas
from . import widgets as W
from .tab_overview import review_quarantine, send_claude

COLS = ("name", "source", "volume", "last_period", "last_value", "updated", "attempt", "status", "expected", "models")
HEAD = ("Название", "Источник", "Объём", "Последний период", "Последнее значение", "Обновлено",
        "Последняя попытка", "Статус", "Ожидаем новые", "Модели")
WIDTH = {"name": 240, "source": 95, "volume": 190, "last_period": 80, "last_value": 110, "updated": 110,
         "attempt": 110, "status": 170, "expected": 90, "models": 120}
FILTERS = ["Все", "Требуют внимания", "На паузе", "Банк России", "ЕМИСС", "Сайты и файлы"]


def volume(store, job_id, state):
    series = store.series_of_job(job_id)
    if not series or not state.get("first_period"):
        return "—"
    pers = sorted({PERIODICITY_RU.get(s["periodicity"], s["periodicity"] or "") for s in series})
    n = len(series)
    word = "ряд" if n % 10 == 1 and n % 100 != 11 else "ряда" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "рядов"
    a, b = state["first_period"][:4], (state.get("last_period") or "")[:4]
    return f"{n} {word}, {', '.join(pers)}, {a}–{b}" if a != b else f"{n} {word}, {', '.join(pers)}, {a}"


class JobsTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Label(top, text="Поиск:").pack(side="left")
        self.search = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.search, width=30)
        e.pack(side="left", padx=4)
        self.search.trace_add("write", lambda *_: self.refresh())
        self.flt = tk.StringVar(value=FILTERS[0])
        cb = ttk.Combobox(top, textvariable=self.flt, values=FILTERS, state="readonly", width=20)
        cb.pack(side="left", padx=8)
        cb.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        ttk.Button(top, text="Корзина…", command=self.trash).pack(side="right")

        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(8, 4))
        for text, cmd, style in (("⟳ Обновить сейчас", self.update_now, "Accent.TButton"),
                                 ("Формула", self.formula, "TButton"),
                                 ("Открыть в Excel", self.open_excel, "TButton"),
                                 ("История", self.history, "TButton"),
                                 ("Изменить", self.edit, "TButton"),
                                 ("Пауза / продолжить", self.pause, "TButton"),
                                 ("Удалить", self.delete, "TButton")):
            ttk.Button(bar, text=text, command=cmd, style=style).pack(side="left", padx=(0, 6))
        more = ttk.Menubutton(bar, text="Ещё ▾")
        menu = tk.Menu(more, tearoff=False)
        menu.add_command(label="Открыть источник", command=self.open_source)
        menu.add_command(label="Отправить Claude", command=self.claude)
        menu.add_command(label="Принять / отклонить отложенные данные", command=self.review)
        more["menu"] = menu
        more.pack(side="left")

        frame, self.tree = W.tree_with_scroll(self, COLS, HEAD, WIDTH, height=20, selectmode="extended")
        frame.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda e: self.history())
        self.tree.bind("<Button-3>", self._context)
        self.ctx = tk.Menu(self, tearoff=False)
        for label, cmd in (("Обновить сейчас", self.update_now), ("Формула", self.formula),
                           ("Открыть в Excel", self.open_excel), ("История", self.history), ("Изменить", self.edit),
                           ("Пауза / продолжить", self.pause), ("Открыть источник", self.open_source),
                           ("Отправить Claude", self.claude), ("Удалить", self.delete)):
            self.ctx.add_command(label=label, command=cmd)
        self.hint = ttk.Label(self, style="Hint.TLabel",
                              text="Двойной щелчок — история показателя. Правая кнопка — все действия.")
        self.hint.pack(anchor="w", pady=(4, 0))

    def _context(self, e):
        iid = self.tree.identify_row(e.y)
        if iid:
            self.tree.selection_set(iid)
            self.ctx.tk_popup(e.x_root, e.y_root)

    def refresh(self):
        app = self.app
        sel = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        states = app.store.states()
        text = self.search.get().strip().lower()
        flt = self.flt.get()
        models = app.cfg.models()
        for job in app.cfg.jobs():
            st = states.get(job["id"], {})
            status = ST.effective(job, st)
            try:
                src_title = sources.get(job["source"]).title
            except KeyError:
                src_title = job["source"]
            if text and text not in (job["name"] + " " + src_title + " " + job["id"]).lower():
                continue
            if flt == "Требуют внимания" and status not in ST.PROBLEM:
                continue
            if flt == "На паузе" and status != "paused":
                continue
            if flt == "Банк России" and job["source"] != "cbr" or flt == "ЕМИСС" and job["source"] != "fedstat":
                continue
            if flt == "Сайты и файлы" and job["source"] in ("cbr", "fedstat"):
                continue
            mods = ", ".join(m["name"] for m in models if m.get("active", True) and job["id"] in m["jobs"])
            val = f"{fmt(st.get('last_value'))} {st.get('last_unit') or ''}".strip() if st.get("last_value") is not None else "—"
            self.tree.insert("", "end", iid=job["id"], tags=(ST.color(status),), values=(
                job["name"], src_title, volume(app.store, job["id"], st), st.get("last_label") or "—", val,
                fmt_date(st.get("last_change") or st.get("last_success")), fmt_date(st.get("last_attempt")),
                W.status_text(status), fmt_date((st.get("expected_next") or "")[:10]) if job["source"] in ("cbr", "fedstat") else "",
                mods))
        for iid in sel:
            if self.tree.exists(iid):
                self.tree.selection_add(iid)

    def select(self, job_id):
        self.refresh()
        if self.tree.exists(job_id):
            self.tree.selection_set(job_id)
            self.tree.see(job_id)

    def selected(self, many=False):
        sel = list(self.tree.selection())
        if not sel:
            messagebox.showinfo("Выберите показатель", "Сначала выделите показатель в списке.", parent=self)
            return [] if many else None
        return sel if many else sel[0]

    # --- действия ------------------------------------------------------------
    def update_now(self):
        ids = self.selected(many=True)
        if ids:
            self.app.update_jobs(ids)

    def pause(self):
        for jid in self.selected(many=True):
            job = self.app.cfg.job(jid)
            self.app.cfg.update_job(jid, enabled=not job.get("enabled", True))
        self.refresh()

    def delete(self):
        ids = self.selected(many=True)
        if not ids:
            return
        names = ", ".join(self.app.cfg.job(i)["name"] for i in ids)
        if not messagebox.askyesno("Удалить", f"Убрать в корзину: {names}?\n\nПоказатель перестанет обновляться. "
                                              "Данные останутся, в корзине он лежит 30 дней — можно восстановить.",
                                   parent=self):
            return
        for i in ids:
            self.app.cfg.delete_job(i)
        self.app.refresh_all()

    def trash(self):
        TrashWindow(self.app)

    def open_source(self):
        jid = self.selected()
        if jid:
            job = self.app.cfg.job(jid)
            url = sources.get(job["source"]).open_url(job.get("params", {}))
            if url:
                webbrowser.open(url)

    def claude(self):
        jid = self.selected()
        if jid:
            send_claude(self.app, jid)

    def review(self):
        jid = self.selected()
        if not jid:
            return
        q = self.app.store.pending_quarantine(jid)
        if not q:
            messagebox.showinfo("Карантин", "У этого показателя нет отложенных данных.", parent=self)
            return
        review_quarantine(self.app, q[0]["id"], self.app.cfg.job(jid))

    def open_excel(self):
        jid = self.selected()
        if not jid:
            return
        if not self.app.store.series_of_job(jid):
            messagebox.showinfo("Нет данных", "Данные ещё не загружались — нажмите «Обновить сейчас».", parent=self)
            return

        def work():
            from ..core.store import Store
            st = Store(self.app.paths.db)
            try:
                return exports.export_job(self.app.cfg, st, self.app.paths, jid)
            finally:
                st.close()
        self.app.bg(work, exports.open_file, "Готовлю файл Excel…")

    def formula(self):
        jid = self.selected()
        if jid:
            FormulaWindow(self.app, [r["code"] for r in self.app.store.series_of_job(jid)])

    def history(self):
        jid = self.selected()
        if jid:
            HistoryWindow(self.app, jid)

    def edit(self):
        jid = self.selected()
        if jid:
            EditWindow(self.app, jid)


class FormulaWindow(tk.Toplevel):
    """Кнопка «Формула»: выбрать ряд → готовые формулы, копирование одним щелчком."""

    def __init__(self, app, codes, model=None):
        super().__init__(app.root)
        self.app = app
        self.title("Формула для модели")
        self.geometry("900x520")
        if not codes:
            ttk.Label(self, text="У показателя ещё нет рядов — сначала загрузите данные.", padding=20).pack()
            return
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")
        ttk.Label(top, text="Ряд:").pack(side="left")
        self.items = []
        for c in codes:
            s = app.store.series(c)
            self.items.append((c, f"{s['name']} [{s['unit']}] — {c}" if s else c))
        self.code = tk.StringVar(value=self.items[0][1])
        cb = ttk.Combobox(top, textvariable=self.code, values=[t for _, t in self.items], state="readonly", width=90)
        cb.pack(side="left", padx=6, fill="x", expand=True)
        cb.bind("<<ComboboxSelected>>", lambda e: self.fill())
        loc = ttk.Frame(self, padding=(10, 0))
        loc.pack(fill="x")
        self.locale = tk.StringVar(value=app.cfg.settings().get("formula_locale", "ru"))
        ttk.Radiobutton(loc, text="Excel на русском", value="ru", variable=self.locale, command=self.fill).pack(side="left")
        ttk.Radiobutton(loc, text="Excel на английском", value="en", variable=self.locale, command=self.fill).pack(side="left", padx=10)
        self.body = ttk.Frame(self, padding=10)
        self.body.pack(fill="both", expand=True)
        ttk.Label(self, padding=10, style="Hint.TLabel", wraplength=860, justify="left", text=(
            "Формулы ссылаются на таблицы Хаб и Хаб_Ряды на листе «Хаб» вашей модели. Замените $A10 на ячейку "
            "с датой (конец месяца, квартала или года). Если ряда нет в модели — отметьте его на вкладке «Модели».")).pack(fill="x")
        self.fill()
        W.center(self, app.root)

    def fill(self):
        for w in self.body.winfo_children():
            w.destroy()
        code = next(c for c, t in self.items if t == self.code.get())
        for title, f in formulas.variants(code, self.locale.get()):
            ttk.Label(self.body, text=title, style="Muted.TLabel").pack(anchor="w", pady=(6, 0))
            row = ttk.Frame(self.body)
            row.pack(fill="x")
            e = ttk.Entry(row, font=("Consolas", 10))
            e.insert(0, f)
            e.configure(state="readonly")
            e.pack(side="left", fill="x", expand=True)
            ttk.Button(row, text="Копировать",
                       command=lambda f=f: (W.copy_to_clipboard(self, f), self.app.status.set("Формула скопирована"))
                       ).pack(side="left", padx=6)


class HistoryWindow(tk.Toplevel):
    def __init__(self, app, job_id):
        super().__init__(app.root)
        self.app = app
        job = app.cfg.job(job_id)
        self.title(f"История: {job['name']}")
        self.geometry("1000x620")
        st = app.store.state(job_id)
        info = (f"Расписание: {S.human(job.get('schedule'))}.  Статус: {ST.word(ST.effective(job, st))}. "
                f"{st.get('message') or ''}")
        ttk.Label(self, text=info, padding=10, wraplength=960, justify="left").pack(fill="x")
        if st.get("warnings"):
            ttk.Label(self, text="Замечания: " + st["warnings"], padding=(10, 0), style="yellow.TLabel",
                      wraplength=960).pack(fill="x")
        pw = ttk.PanedWindow(self, orient="horizontal")
        pw.pack(fill="both", expand=True, padx=10, pady=6)
        lf, self.series = W.tree_with_scroll(pw, ("code", "name"), ("Код", "Ряд"), {"code": 190, "name": 260})
        pw.add(lf, weight=2)
        rf, self.obs = W.tree_with_scroll(pw, ("label", "date", "value", "state", "fetched"),
                                          ("Период", "Дата", "Значение", "Состояние", "Получено"),
                                          {"label": 80, "date": 90, "value": 110, "state": 120, "fetched": 130})
        pw.add(rf, weight=3)
        for s in app.store.series_of_job(job_id):
            self.series.insert("", "end", iid=s["code"], values=(s["code"], f"{s['name']} [{s['unit']}]"))
        self.series.bind("<<TreeviewSelect>>", lambda e: self.show())
        ttk.Label(self, text="Последние запуски", style="Title.TLabel", padding=(10, 4)).pack(anchor="w")
        rf2, runs = W.tree_with_scroll(self, ("when", "status", "new", "rev", "msg"),
                                       ("Когда", "Статус", "Новых", "Пересмотров", "Сообщение"),
                                       {"when": 130, "status": 150, "new": 60, "rev": 90, "msg": 520}, height=6)
        rf2.pack(fill="x", padx=10, pady=(0, 10))
        for r in app.store.runs(job_id, 30):
            runs.insert("", "end", values=(fmt_date(r["started"]), ST.word(r["status"]), r["new_periods"] or 0,
                                           r["revisions"] or 0, r["message"] or ""))
        kids = self.series.get_children()
        if kids:
            self.series.selection_set(kids[0])
        W.center(self, app.root)

    def show(self):
        sel = self.series.selection()
        if not sel:
            return
        self.obs.delete(*self.obs.get_children())
        names = {"current": "актуальное", "old": "пересмотрено", "quarantine": "на проверке", "rejected": "отклонено"}
        for r in reversed(self.app.store.history(sel[0])):
            self.obs.insert("", "end", values=(r["period_label"], fmt_date(r["period_end"]), fmt(r["value"]),
                                               names.get(r["state"], r["state"]), fmt_date(r["fetched_at"])))


class EditWindow(tk.Toplevel):
    def __init__(self, app, job_id):
        super().__init__(app.root)
        self.app = app
        self.job = app.cfg.job(job_id)
        self.title(f"Изменить: {self.job['name']}")
        self.transient(app.root)
        f = ttk.Frame(self, padding=16)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Название").grid(row=0, column=0, sticky="w")
        self.name = tk.StringVar(value=self.job["name"])
        ttk.Entry(f, textvariable=self.name, width=60).grid(row=0, column=1, sticky="we", pady=3)
        ttk.Label(f, text="Как часто проверять").grid(row=1, column=0, sticky="w")
        self.sched = tk.StringVar(value=self.job.get("schedule", "auto"))
        ttk.Combobox(f, textvariable=self.sched, values=[k for k, _ in S.CHOICES], width=20).grid(row=1, column=1, sticky="w", pady=3)
        ttk.Label(f, text="auto, daily, weekly:Wed, monthly:15, every:6h", style="Hint.TLabel").grid(row=2, column=1, sticky="w")
        ttk.Label(f, text="Порог скачка, %").grid(row=3, column=0, sticky="w")
        self.jump = tk.StringVar(value=str((self.job.get("checks") or {}).get("max_jump_pct") or ""))
        ttk.Entry(f, textvariable=self.jump, width=10).grid(row=3, column=1, sticky="w", pady=3)
        ttk.Label(f, text="Модели").grid(row=4, column=0, sticky="nw", pady=(8, 0))
        mf = ttk.Frame(f)
        mf.grid(row=4, column=1, sticky="w", pady=(8, 0))
        self.mvars = {}
        for m in app.cfg.models():
            if not m.get("active", True):
                continue
            v = tk.BooleanVar(value=job_id in m["jobs"])
            self.mvars[m["name"]] = v
            ttk.Checkbutton(mf, text=m["name"], variable=v).pack(anchor="w")
        if not self.mvars:
            ttk.Label(mf, text="нет подключённых моделей", style="Muted.TLabel").pack(anchor="w")
        bar = ttk.Frame(f)
        bar.grid(row=5, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(bar, text="Сохранить", style="Accent.TButton", command=self.save).pack(side="right")
        ttk.Button(bar, text="Отмена", command=self.destroy).pack(side="right", padx=8)
        W.center(self, app.root)

    def save(self):
        try:
            S.parse(self.sched.get())
        except ValueError as e:
            messagebox.showwarning("Расписание", str(e), parent=self)
            return
        checks = dict(self.job.get("checks") or {})
        j = self.jump.get().strip().replace(",", ".")
        if j:
            try:
                checks["max_jump_pct"] = float(j)
            except ValueError:
                messagebox.showwarning("Порог", "Порог — число, например 50.", parent=self)
                return
        else:
            checks.pop("max_jump_pct", None)
        self.app.cfg.update_job(self.job["id"], name=self.name.get().strip() or self.job["name"],
                                schedule=self.sched.get().strip(), checks=checks)
        models = self.app.cfg.models()
        for m in models:
            if m["name"] in self.mvars:
                want = self.mvars[m["name"]].get()
                if want and self.job["id"] not in m["jobs"]:
                    m["jobs"].append(self.job["id"])
                elif not want and self.job["id"] in m["jobs"]:
                    m["jobs"].remove(self.job["id"])
        self.app.cfg.save_models(models)
        self.destroy()
        self.app.refresh_all()


class TrashWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("Корзина")
        self.geometry("720x380")
        ttk.Label(self, padding=10, style="Muted.TLabel",
                  text="Удалённые показатели хранятся здесь 30 дней. Их данные никуда не делись.").pack(anchor="w")
        frame, self.tree = W.tree_with_scroll(self, ("name", "when"), ("Показатель", "Удалён"), {"name": 480, "when": 150})
        frame.pack(fill="both", expand=True, padx=10)
        bar = ttk.Frame(self, padding=10)
        bar.pack(fill="x")
        ttk.Button(bar, text="Восстановить", style="Accent.TButton", command=self.restore).pack(side="left")
        ttk.Button(bar, text="Закрыть", command=self.destroy).pack(side="right")
        self.fill()
        W.center(self, app.root)

    def fill(self):
        self.tree.delete(*self.tree.get_children())
        for j in self.app.cfg.trash():
            self.tree.insert("", "end", iid=j["id"], values=(j["name"], fmt_date(j.get("deleted"))))

    def restore(self):
        for jid in self.tree.selection():
            self.app.cfg.restore_job(jid)
        self.fill()
        self.app.refresh_all()


def ask_name(parent, title, prompt, initial=""):
    return simpledialog.askstring(title, prompt, initialvalue=initial, parent=parent)
