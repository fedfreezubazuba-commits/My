"""«Мои показатели» — все показатели в обновлении, поиск, фильтры и действия."""
import tkinter as tk
import webbrowser
from tkinter import messagebox, simpledialog, ttk

from .. import sources
from ..core import schedule as S
from ..core import status as ST
from ..core.overview import short
from ..core.base_fmt import fmt, fmt_date
from ..core.periods import PERIODICITY_RU
from ..excel import exports, formulas
from . import widgets as W
from .schedule_box import ScheduleBox
from .tab_overview import review_quarantine, send_claude

COLS = ("name", "source", "volume", "last_period", "last_value", "updated", "attempt", "status", "expected", "models")
HEAD = ("Название", "Источник", "Объём", "Последний период", "Последнее значение", "Обновлено",
        "Последняя попытка", "Статус", "Ожидаем новые", "Модели")
WIDTH = {"name": 320, "source": 95, "volume": 170, "last_period": 75, "last_value": 105, "updated": 100,
         "attempt": 100, "status": 160, "expected": 85, "models": 110}
FILTERS = ["Все", "Требуют внимания", "На паузе", "Банк России", "ЕМИСС", "Сайты и файлы"]


def next_check_text(app, job, st) -> str:
    """Когда хаб проверит показатель в следующий раз и почему (повторы, пока нет новых данных)."""
    import datetime as dt
    try:
        src = sources.get(job["source"])
        _exp, per = S.expected_next(app.store, job)
        return S.check_text(job, st, dt.datetime.now(), snapshot=src.snapshot, periodicity=per)
    except Exception:  # noqa: BLE001 — подсказка не должна ломать окно
        return ""


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

        bar = W.FlowBar(self)
        bar.pack(fill="x", pady=(8, 4))
        bar.add(W.icon_button(bar, "⟳", "Загрузить выделенные показатели сейчас, не дожидаясь расписания",
                              self.update_now, text="Обновить сейчас", style="Accent.TButton"))
        for i, (icon, tip, cmd) in enumerate((
                ("ƒx", "Формула — готовые формулы Excel для ряда показателя", self.formula),
                ("▦", "Открыть в Excel — все данные показателя отдельным файлом", self.open_excel),
                ("◷", "График обновления — когда проверять, статус и история значений (двойной щелчок)",
                 self.history),
                ("✎", "Изменить набор данных — регионы, товары, годы… (ЕМИСС, Банк России, форма на сайте)",
                 self.change_data),
                ("⏯", "Пауза / продолжить — временно не проверять показатель", self.pause),
                ("⚖", "Отложенные данные — посмотреть, принять или отклонить", self.review),
                ("↗", "Открыть источник — страница показателя на сайте", self.open_source),
                ("✉", "Отправить Claude — описание проблемы в буфер обмена (без паролей)", self.claude),
                ("✖", "Удалить — убрать в корзину (30 дней можно восстановить)", self.delete))):
            bar.add(W.icon_button(bar, icon, tip, cmd), gap_before=16 if i in (0, 4, 6, 8) else 0)

        frame, self.tree = W.tree_with_scroll(self, COLS, HEAD, WIDTH, height=20, selectmode="extended")
        frame.pack(fill="both", expand=True)
        W.sortable(self.tree)
        self.tree.bind("<Double-1>", lambda e: self.history())
        self.tree.bind("<Button-3>", self._context)
        self.ctx = tk.Menu(self, tearoff=False)
        for label, cmd in (("Обновить сейчас", self.update_now), ("Формула", self.formula),
                           ("Открыть в Excel", self.open_excel), ("График обновления и история", self.history),
                           ("Изменить набор данных", self.change_data),
                           ("Пауза / продолжить", self.pause), ("Открыть источник", self.open_source),
                           ("Отправить Claude", self.claude), ("Удалить", self.delete)):
            self.ctx.add_command(label=label, command=cmd)
        self.info = ttk.Label(self, wraplength=1200, justify="left", padding=(2, 6, 2, 0))
        self.info.pack(fill="x")
        self.hint = ttk.Label(self, style="Hint.TLabel",
                              text="Выделите показатель — здесь появится, что значит его статус и что делать. "
                                   "Двойной щелчок — история показателя. Правая кнопка — все действия.")
        self.hint.pack(anchor="w", pady=(2, 0))
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.explain(), add="+")

    def explain(self):
        """Под списком: что значит статус выделенного показателя и что делать."""
        sel = self.tree.selection()
        job = self.app.cfg.job(sel[0]) if len(sel) == 1 else None
        if not job:
            self.info.configure(text="")
            return
        st = self.app.store.state(job["id"])
        status = ST.effective(job, st)
        self.info.configure(text=f"{job['name']} — {ST.word(status)}. {ST.explain(job, st)} "
                                 f"{next_check_text(self.app, job, st)}".strip(),
                            foreground=ST.COLORS[ST.color(status)] if ST.color(status) != ST.GREEN else "")

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
                W.status_text(status), fmt_date((st.get("expected_next") or "")[:10])
                if job["source"] in ("cbr", "fedstat") or job.get("release_dates") or st.get("release_next") else "",
                mods))
        W.resort(self.tree)
        for iid in sel:
            if self.tree.exists(iid):
                self.tree.selection_add(iid)
        self.explain()

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

    def change_data(self):
        """Тот же показатель в навигаторе с отмеченными значениями — поменять регионы, товары, годы."""
        jid = self.selected()
        if not jid:
            return
        job = self.app.cfg.job(jid)
        find = self.app.find
        if job["source"] == "web_form":
            from .dialog_form import FormDialog
            p = job["params"]
            FormDialog(self.app, p.get("url"), p["request"], None, job=job)
            return
        if job["source"] not in ("fedstat", "cbr"):
            messagebox.showinfo("Изменить набор данных", "Для сайтов и файлов набор данных задаётся при добавлении. "
                                "Чтобы поменять его, добавьте показатель заново («Найти данные → Сайт» или «Файл»), "
                                "а этот удалите.", parent=self)
            return
        nav = find.fedstat if job["source"] == "fedstat" else find.cbr
        self.app.nb.select(find)
        find.nb.select(nav)
        nav.edit_job(job)

    def edit(self):
        jid = self.selected()
        if jid:
            EditWindow(self.app, jid)


class FormulaWindow(tk.Toplevel):
    """Кнопка «Формула»: выбрать ряд → готовые формулы, копирование одним щелчком."""

    def __init__(self, app, codes, model=None):
        super().__init__(app.root)
        self.app = app
        from ..excel.packet import layout
        self.layout = layout(app.cfg, model)
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
        W.autowrap(ttk.Label(self, padding=10, style="Hint.TLabel", justify="left", text=(
            "Формулы ссылаются на таблицы Хаб и Хаб_Ряды на листе «Хаб» вашей модели. Замените $A10 на ячейку "
            "с датой (конец месяца, квартала или года). Если ряда нет в модели — отметьте его на вкладке «Модели»."))).pack(fill="x")
        self.fill()
        W.center(self, app.root)

    def fill(self):
        for w in self.body.winfo_children():
            w.destroy()
        code = next(c for c, t in self.items if t == self.code.get())
        for title, f in formulas.variants(code, self.locale.get(), layout=self.layout):
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


class JobWindow(tk.Toplevel):
    """«График обновления»: как часто проверять (правила, даты выхода), статус простыми словами,
    история значений и запусков — в одном окне."""

    def __init__(self, app, job_id):
        super().__init__(app.root)
        self.app = app
        self.job = job = app.cfg.job(job_id)
        st = app.store.state(job_id)
        self.title(f"График обновления: {job['name']}")
        self.geometry("1080x780")

        bar = ttk.Frame(self, padding=(12, 8))          # кнопки снизу — видны при любом размере окна
        bar.pack(side="bottom", fill="x")
        ttk.Button(bar, text="Закрыть", command=self.destroy).pack(side="right")
        self.saved = ttk.Label(bar, style="green.TLabel")
        self.saved.pack(side="left")

        top = ttk.Frame(self, padding=(12, 10, 12, 0))
        top.pack(fill="x")
        top.columnconfigure(0, weight=1)
        top.columnconfigure(1, weight=1)
        left = ttk.Frame(top)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        ttk.Label(left, text="Название").pack(anchor="w")
        self.name = tk.StringVar(value=job["name"])
        ttk.Entry(left, textvariable=self.name).pack(fill="x", pady=(0, 8))
        ttk.Label(left, text="Когда проверять", style="Title.TLabel").pack(anchor="w")
        hint = ""
        if st.get("release_next") or st.get("release_note"):
            hint = "источник сообщает: " + "; ".join(x for x in (
                ("следующее обновление " + S.fmt_moment(st["release_next"])) if st.get("release_next") else "",
                st.get("release_note") or "") if x)
        self.sched_box = ScheduleBox(left, job.get("schedule", "auto"), job.get("release_dates"), hint)
        self.sched_box.pack(fill="x")
        more = W.Collapsible(left, "Проверки новых данных")
        more.pack(fill="x", pady=(6, 0))
        r = ttk.Frame(more.body)
        r.pack(fill="x")
        ttk.Label(r, text="Отложить данные, если значение изменилось больше чем на, %:").pack(side="left")
        self.jump = tk.StringVar(value=str((job.get("checks") or {}).get("max_jump_pct") or ""))
        ttk.Entry(r, textvariable=self.jump, width=8).pack(side="left", padx=6)
        self.reload = None
        try:
            from ..sources import get as get_source
            incremental = get_source(job["source"]).incremental(job.get("params") or {})
        except Exception:  # noqa: BLE001
            incremental = False
        if incremental:
            from .dialog_add import reload_row
            self.reload = reload_row(more.body, app, job.get("full_reload_days"))
        ttk.Button(left, text="Сохранить график", style="Accent.TButton", command=self.save).pack(anchor="w", pady=8)

        right = ttk.Frame(top)
        right.grid(row=0, column=1, sticky="nsew")
        ttk.Label(right, text="Статус", style="Title.TLabel").pack(anchor="w")
        status = ST.effective(job, st)
        info = f"{ST.word(status)}. {ST.explain(job, st)}"
        nxt = next_check_text(app, job, st)
        if nxt:
            info += "\n\n" + nxt
        if job["source"] == "fedstat" and not (st.get("release_next") or st.get("release_note")):
            info += ("\n\nИсточник о сроках: на странице показателя ЕМИСС срок следующего обновления не нашёлся — "
                     "хаб оценивает его по тому, когда данные выходили раньше.")
        W.autowrap(ttk.Label(right, text=info, justify="left",
                             foreground=ST.COLORS[ST.color(status)] if ST.color(status) != ST.GREEN else "")
                   ).pack(fill="x")
        if st.get("warnings"):
            W.autowrap(ttk.Label(right, text="Замечания: " + short(st["warnings"], 600), style="yellow.TLabel",
                                 justify="left")).pack(fill="x", pady=(6, 0))

        hist = ttk.Notebook(self)
        hist.pack(fill="both", expand=True, padx=12, pady=(6, 0))
        vals = ttk.Frame(hist)
        hist.add(vals, text="История значений")
        pw = ttk.PanedWindow(vals, orient="horizontal")
        pw.pack(fill="both", expand=True, pady=4)
        lf, self.series = W.tree_with_scroll(pw, ("code", "name"), ("Код", "Ряд"), {"code": 190, "name": 260})
        pw.add(lf, weight=2)
        rf, self.obs = W.tree_with_scroll(pw, ("label", "date", "value", "state", "fetched"),
                                          ("Период", "Дата", "Значение", "Состояние", "Получено"),
                                          {"label": 80, "date": 90, "value": 110, "state": 120, "fetched": 130})
        pw.add(rf, weight=3)
        for s in app.store.series_of_job(job_id)[:3000]:
            self.series.insert("", "end", iid=s["code"], values=(s["code"], f"{s['name']} [{s['unit']}]"))
        self.series.bind("<<TreeviewSelect>>", lambda e: self.show())
        runs_tab = ttk.Frame(hist)
        hist.add(runs_tab, text="Загрузки")
        rf2, runs = W.tree_with_scroll(runs_tab, ("when", "status", "new", "rev", "msg"),
                                       ("Когда", "Статус", "Новых", "Пересмотров", "Сообщение"),
                                       {"when": 130, "status": 150, "new": 60, "rev": 90, "msg": 520})
        rf2.pack(fill="both", expand=True, pady=4)
        for r in app.store.runs(job_id, 50):
            runs.insert("", "end", values=(fmt_date(r["started"]), ST.word(r["status"]), r["new_periods"] or 0,
                                           r["revisions"] or 0, short(r["message"] or "", 300)))
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

    def save(self):
        try:
            schedule, release = self.sched_box.get()
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
        extra = {}
        if self.reload is not None:
            from .dialog_add import reload_value
            try:
                extra["full_reload_days"] = reload_value(self.reload)
            except ValueError:
                messagebox.showwarning("Вся история", "Число дней — целое число, например 30 (0 — никогда).",
                                       parent=self)
                return
        self.job = self.app.cfg.update_job(self.job["id"], name=self.name.get().strip() or self.job["name"],
                                           schedule=schedule, release_dates=release, checks=checks, **extra)
        self.saved.configure(text="✔ Сохранено: " + S.human(schedule))
        self.app.refresh_all()


HistoryWindow = EditWindow = JobWindow      # прежние имена (двойной щелчок, контекстное меню, тесты)


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
