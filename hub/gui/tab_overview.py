"""«Обзор» — стартовый экран: светофор, проблемы с одной кнопкой действия, новые данные, модели."""
import tkinter as tk
from tkinter import messagebox, ttk

from ..core import claude_pack, overview, status as ST, winsched
from ..core.base_fmt import fmt, fmt_date
from ..excel.models import models_with_new_data
from . import widgets as W


class OverviewTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=12)
        self.app = app
        head = ttk.Frame(self)
        head.pack(fill="x")
        self.big = ttk.Label(head, text="…", style="Big.TLabel")
        self.big.pack(side="left")
        self.go_btn = ttk.Button(head, text="Показать проблемы", command=self._scroll_to_problems)
        right = ttk.Frame(head)
        right.pack(side="right")
        ttk.Button(right, text="⟳ Обновить всё сейчас", style="Accent.TButton",
                   command=lambda: app.update_jobs(force=True)).pack(side="right", padx=(8, 0))
        self.auto = tk.BooleanVar()
        self.auto_chk = ttk.Checkbutton(right, text="Автообновление", variable=self.auto, command=self._toggle_auto)
        self.auto_chk.pack(side="right")
        self.auto_info = ttk.Label(self, style="Muted.TLabel")
        self.auto_info.pack(fill="x", pady=(2, 8))
        self.body = W.ScrollFrame(self)
        self.body.pack(fill="both", expand=True)

    def _scroll_to_problems(self):
        self.body.canvas.yview_moveto(0)

    def _toggle_auto(self):
        want = self.auto.get()
        ok, msg = winsched.enable() if want else winsched.disable()
        if not ok:
            self.auto.set(not want)
            messagebox.showwarning("Автообновление", msg, parent=self)
        self.refresh()

    def refresh(self):
        app = self.app
        cfg, store = app.cfg, app.store
        allp = overview.problems(cfg, store)
        probs = [p for p in allp if ST.effective(p.job, p.state) != "delayed"]
        delayed = [p for p in allp if ST.effective(p.job, p.state) == "delayed"]
        stale = overview.runner_stale(store)
        enabled = winsched.is_enabled()
        self.auto.set(bool(enabled))
        if enabled is None:
            self.auto_chk.state(["disabled"])
            self.auto_info.configure(text="Автообновление работает через планировщик Windows (здесь не Windows).")
        else:
            last = store.meta_get("runner_heartbeat")
            self.auto_info.configure(text=("Автообновление включено: каждый час и при входе в Windows. "
                                           if enabled else "Автообновление выключено — данные обновляются только по кнопке. ")
                                     + (f"Последнее обновление: {fmt_date(last)}." if last else ""))
        bad = len(probs) + (1 if stale and enabled else 0)
        if not cfg.jobs():
            self.big.configure(text="Добавьте первый показатель", style="Big.TLabel", foreground="")
        elif bad:
            self.big.configure(text=f"Требует внимания: {bad}", style="Big.TLabel", foreground=ST.COLORS[ST.RED])
        else:
            self.big.configure(text="Всё в порядке", style="Big.TLabel", foreground=ST.COLORS[ST.GREEN])
        self.body.clear()
        inner = self.body.inner

        if not cfg.jobs():
            self._section(inner, "С чего начать")
            ttk.Label(inner, wraplength=900, justify="left", text=(
                "1. Откройте вкладку «Найти данные», найдите показатель (например, ставки по вкладам в «Банк России»)\n"
                "    и нажмите «＋ В обновления».\n"
                "2. На вкладке «Модели» нажмите «Подключить модель» и выберите файл Excel.\n"
                "3. Отметьте, какие ряды получает модель. В модели нажмите «Данные → Обновить всё».")).pack(anchor="w")
            ttk.Button(inner, text="Найти данные →", command=lambda: self.app.show(self.app.find)).pack(anchor="w", pady=8)

        if stale and enabled:
            self._section(inner, "Фоновое обновление")
            ttk.Label(inner, text=stale, style="yellow.TLabel").pack(anchor="w")
        if probs:
            self._section(inner, "Проблемы")
            for p in probs:
                self._problem_row(inner, p)
        if delayed:
            self._section(inner, "Задерживаются (это не ошибка)")
            for p in delayed:
                self._problem_row(inner, p)

        self._section(inner, "Новые данные за неделю")
        ups = overview.recent_updates(cfg, store)
        if not ups:
            ttk.Label(inner, text="Пока нет.", style="Muted.TLabel").pack(anchor="w")
        for u in ups:
            row = ttk.Frame(inner)
            row.pack(fill="x", pady=1)
            val = f"{fmt(u['last_value'])} {u['unit'] or ''}".strip()
            ttk.Label(row, text=f"● {u['job']['name']}", style="green.TLabel").pack(side="left")
            ttk.Label(row, text=f"   новых значений: {u['new'] or 0}" + (f", пересмотров: {u['revisions']}" if u["revisions"] else "")
                      + (f"   последнее: {val} за {u['last_label']}" if u["last_label"] else "")
                      + f"   ({fmt_date(u['when'])})", style="Muted.TLabel").pack(side="left")

        self._section(inner, "Модели с новыми данными")
        mods = models_with_new_data(cfg, store)
        if not mods:
            ttk.Label(inner, text="Нет моделей с непросмотренными новыми данными.", style="Muted.TLabel").pack(anchor="w")
        for name, n in mods:
            row = ttk.Frame(inner)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=f"Откройте модель «{name}» — там новые данные по {n} {'ряду' if n == 1 else 'рядам'}.").pack(side="left")
            ttk.Button(row, text="Открыть модель", command=lambda nm=name: self.app.models.open_model(nm)).pack(side="left", padx=8)

    @staticmethod
    def _section(parent, title):
        ttk.Label(parent, text=title, style="Title.TLabel").pack(anchor="w", pady=(14, 4))

    def _problem_row(self, parent, p):
        row = ttk.Frame(parent, padding=(0, 3))
        row.pack(fill="x")
        st = ST.effective(p.job, p.state)
        ttk.Label(row, text=W.status_text(st), style=f"{ST.color(st)}.TLabel", width=24).pack(side="left")
        ttk.Label(row, text=p.job["name"], font=(W.FONT, 10, "bold")).pack(side="left")
        ttk.Label(row, text="  " + p.text, style="Muted.TLabel", wraplength=640, justify="left").pack(side="left")
        if p.action_label:
            ttk.Button(row, text=p.action_label, command=lambda: self.act(p)).pack(side="right")

    def act(self, p):
        if p.action == "retry":
            self.app.update_jobs([p.job["id"]])
        elif p.action == "claude":
            send_claude(self.app, p.job["id"])
        elif p.action in ("login", "browser"):
            self.app.find.site.login_for_job(p.job)
        elif p.action == "review":
            review_quarantine(self.app, p.qid, p.job)


def send_claude(app, job_id):
    text = claude_pack.job_package(app.cfg, app.store, job_id)
    path = claude_pack.save(app.paths, text, job_id)
    W.copy_to_clipboard(app.root, text)
    messagebox.showinfo("Отправить Claude",
                        "Описание проблемы скопировано в буфер обмена и сохранено в файл:\n"
                        f"{path}\n\nОткройте новую сессию Claude Code для этого проекта и вставьте текст (Ctrl+V).\n"
                        "Пароли, куки и токены в текст не попали.", parent=app.root)


def review_quarantine(app, qid, job):
    import json
    w = tk.Toplevel(app.root)
    w.title(f"Проверьте данные: {job['name']}")
    w.geometry("820x520")
    q = app.store.q1("SELECT * FROM quarantine WHERE id=?", (qid,))
    reasons = json.loads(q["reasons"] or "[]") if q else []
    ttk.Label(w, text="Новые данные отложены, потому что:", style="Title.TLabel", padding=(10, 10, 10, 2)).pack(anchor="w")
    for r in reasons:
        ttk.Label(w, text="• " + r, padding=(16, 0)).pack(anchor="w")
    frame, tree = W.tree_with_scroll(w, ("name", "label", "old", "new"), ("Ряд", "Период", "Было", "Стало"),
                                     {"name": 380, "label": 90, "old": 110, "new": 110})
    frame.pack(fill="both", expand=True, padx=10, pady=8)
    for r in app.store.quarantine_rows(qid):
        cur = app.store.current(r["code"]).get(r["period_end"])
        tree.insert("", "end", values=(r["name"], r["period_label"], fmt(cur[0]) if cur else "—", fmt(r["value"])))
    bar = ttk.Frame(w, padding=10)
    bar.pack(fill="x")

    def decide(accept):
        if accept:
            app.store.accept_quarantine(qid)
        else:
            app.store.reject_quarantine(qid)
        if not app.store.pending_quarantine(job["id"]):
            app.store.set_state(job["id"], status="updated" if accept else "unchanged", category=None,
                                message="Данные приняты" if accept else "Данные отклонены")
            app.store.refresh_summary(job["id"])
        w.destroy()
        _rebuild(app)

    ttk.Button(bar, text="Принять данные", style="Accent.TButton", command=lambda: decide(True)).pack(side="left")
    ttk.Button(bar, text="Отклонить", command=lambda: decide(False)).pack(side="left", padx=8)
    ttk.Label(bar, text="Принятые данные попадут в модели при следующей сборке (сразу).", style="Hint.TLabel").pack(side="left")
    W.center(w, app.root)


def _rebuild(app):
    from ..excel import packet

    def work():
        from ..core.store import Store
        st = Store(app.paths.db)
        try:
            return packet.rebuild(app.cfg, st, app.paths)
        finally:
            st.close()
    app.bg(work, lambda _r: app.refresh_all(), "Обновляю файлы для моделей…")
