"""«Обзор» — стартовый экран: светофор, проблемы с одной кнопкой действия, новые данные, модели."""
import tkinter as tk
from tkinter import messagebox, ttk

from ..core import claude_pack, hostblock as HB, overview, status as ST, winsched
from ..core.overview import short
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
        enabled, next_run = winsched.status()
        self.auto.set(bool(enabled))
        if enabled is None:
            self.auto_chk.state(["disabled"])
            self.auto_info.configure(text="Автообновление работает через планировщик Windows (здесь не Windows).")
        else:
            last = store.meta_get("runner_heartbeat")
            self.auto_info.configure(text=(f"Автообновление включено: {winsched.EVERY_TEXT} и при входе в Windows "
                                           f"(задача «{winsched.TASK_NAME}» в Планировщике заданий"
                                           + (f", следующий запуск {next_run}). " if next_run else "). ")
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
        blocks = HB.active(store)
        if blocks:
            self._section(inner, "Сайт временно не пускает хаб")
            for host, till, _reason in blocks:
                self._block_row(inner, host, till)
        if probs:
            self._section(inner, "Проблемы")
            self._problem_rows(inner, probs)
        if delayed:
            self._section(inner, "Задерживаются (это не ошибка)")
            self._problem_rows(inner, delayed)

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

    def _block_row(self, parent, host, till):
        box = ttk.Frame(parent, padding=(0, 2, 0, 6))
        box.pack(fill="x")
        ttk.Label(box, wraplength=900, justify="left", style="yellow.TLabel", text=(
            f"{HB.title(host)} перестал отвечать хабу («доступ запрещён»). Обычно так сайт защищается, когда к нему "
            f"было много запросов, и через несколько часов снимает ограничение сам. Хаб не обращается к этому сайту "
            f"до {till:%H:%M %d.%m} и потом попробует снова — делать ничего не нужно.")).pack(anchor="w")
        ttk.Label(box, wraplength=900, justify="left", style="Muted.TLabel", text=(
            "Если данные нужны раньше: «Открыть сайт» — откроется окно встроенного браузера. Если сайт покажет "
            "проверку «я не робот», пройдите её и закройте окно — хаб запомнит это и сразу попробует снова. "
            "Если сайт открылся сразу без проверки, просто закройте окно.")).pack(anchor="w", pady=(2, 4))
        row = ttk.Frame(box)
        row.pack(anchor="w")
        ttk.Button(row, text="Открыть сайт", command=lambda: self.site_check(host)).pack(side="left")
        ttk.Button(row, text="Попробовать сейчас", command=lambda: self.retry_host(host)).pack(side="left", padx=8)

    def _host_jobs(self, host):
        from .. import sources
        out = []
        for j in self.app.cfg.jobs():
            try:
                if j.get("enabled", True) and sources.get(j["source"]).host(j.get("params") or {}) == host:
                    out.append(j["id"])
            except KeyError:
                pass
        return out

    def retry_host(self, host):
        HB.lift(self.app.store, host)
        self.app.update_jobs(self._host_jobs(host))

    def site_check(self, host):
        open_site_check(self.app, host, HB.site_url(host), on_done=lambda: self.retry_host(host))

    MAX_ROWS = 40

    def _problem_rows(self, parent, items):
        for p in items[:self.MAX_ROWS]:
            self._problem_row(parent, p)
        if len(items) > self.MAX_ROWS:
            ttk.Label(parent, text=f"…и ещё {len(items) - self.MAX_ROWS} — все на вкладке «Мои показатели».",
                      style="Muted.TLabel").pack(anchor="w")

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
        elif p.action == "reselect":
            find = self.app.find
            self.app.nb.select(find)
            find.nb.select(find.fedstat)
            find.fedstat.select_indicator(str(p.job["params"]["id"]), p.job["name"].split(" — ")[0])
            messagebox.showinfo("Выбрать заново", "Показатель открыт в навигаторе ЕМИСС. Отметьте меньше регионов, "
                                "товаров или лет и нажмите «＋ В обновления». Старый показатель потом удалите на "
                                "вкладке «Мои показатели».", parent=self)


def send_claude(app, job_id):
    text = claude_pack.job_package(app.cfg, app.store, job_id, app.paths)
    path = claude_pack.save(app.paths, text, job_id)
    W.copy_to_clipboard(app.root, text)
    messagebox.showinfo("Отправить Claude",
                        "Описание проблемы скопировано в буфер обмена и сохранено в файл:\n"
                        f"{path}\n\nОткройте новую сессию Claude Code для этого проекта и вставьте текст (Ctrl+V).\n"
                        "Пароли, куки и токены в текст не попали.", parent=app.root)


def review_quarantine(app, qid, job):
    """Отложенные данные показателя: что случилось, что изменится, решение. Решение касается всех
    отложенных загрузок показателя; выполняется в фоне (значений могут быть сотни тысяч)."""
    import json
    from ..core import checks as CH
    MAX_REASONS, MAX_ROWS = 6, 1000
    qids = [r["id"] for r in app.store.pending_quarantine(job["id"])] or [qid]
    q = app.store.q1("SELECT * FROM quarantine WHERE id=?", (qids[-1],))
    reasons = json.loads(q["reasons"] or "[]") if q else []
    w = tk.Toplevel(app.root)
    w.title(f"Отложенные данные: {job['name']}")
    w.geometry("900x640")
    w.transient(app.root)

    # кнопки — первыми и снизу, чтобы их было видно при любом размере окна
    bar = ttk.Frame(w, padding=12)
    bar.pack(side="bottom", fill="x")
    offs = []                                # проверки, которые можно выключить для этого показателя
    seen = set()
    for r in reasons:
        _t, key, off = CH.explain(r)
        if key and key not in seen:
            seen.add(key)
            offs.append((key, off, r.split(" у ")[0].split(":")[0]))
    off_vars = []
    if offs:
        of = ttk.Frame(w, padding=(12, 0))
        of.pack(side="bottom", fill="x")
        for key, off, what in offs:
            v = tk.BooleanVar(value=False)
            off_vars.append((v, key, off))
            ttk.Checkbutton(of, variable=v, text=f"Больше не откладывать данные этого показателя из-за "
                                                 f"«{what.lower()}»").pack(anchor="w")

    body = ttk.Frame(w, padding=(12, 10, 12, 0))
    body.pack(fill="both", expand=True)
    when = fmt_date(q["created"]) if q else ""
    ttk.Label(body, text="Что случилось", style="Title.TLabel").pack(anchor="w")
    ttk.Label(body, wraplength=860, justify="left", text=(
        f"При загрузке {when} хаб заметил необычное и не стал сразу отдавать новые данные в модели — "
        "чтобы не испортить ваши расчёты. Сейчас в моделях прежние данные.")).pack(anchor="w")
    for r in reasons[:MAX_REASONS]:
        text, _k, _o = CH.explain(r)
        ttk.Label(body, text="• " + short(r, 300), wraplength=840, justify="left").pack(anchor="w", padx=(10, 0))
        if text:
            ttk.Label(body, text=text, style="Muted.TLabel", wraplength=820, justify="left").pack(anchor="w", padx=(26, 0))
    if len(reasons) > MAX_REASONS:
        ttk.Label(body, text=f"…и ещё {len(reasons) - MAX_REASONS} замечаний", style="Muted.TLabel").pack(anchor="w")

    n_new = n_changed = 0
    for one in qids:
        a_, b_ = app.store.quarantine_counts(one)
        n_new, n_changed = n_new + a_, n_changed + b_
    ttk.Label(body, text="Что изменится, если принять", style="Title.TLabel").pack(anchor="w", pady=(10, 0))
    if n_new or n_changed:
        ttk.Label(body, wraplength=860, justify="left", text=(
            f"Новых значений: {n_new:,}, изменённых прежних: {n_changed:,}. Ниже — примеры "
            f"(«Было» — что сейчас в моделях).").replace(",", " ")).pack(anchor="w")
        frame, tree = W.tree_with_scroll(body, ("name", "label", "old", "new"), ("Ряд", "Период", "Было", "Стало"),
                                         {"name": 420, "label": 90, "old": 120, "new": 120}, height=8)
        frame.pack(fill="both", expand=True, pady=(4, 0))
        shown = 0
        for one in qids:
            for r in app.store.quarantine_rows(one):
                if shown >= MAX_ROWS:
                    break
                cur = app.store.current(r["code"]).get(r["period_end"])
                tree.insert("", "end", values=(r["name"], r["period_label"], fmt(cur[0]) if cur else "—", fmt(r["value"])))
                shown += 1
    else:
        ttk.Label(body, wraplength=860, justify="left", text=(
            "Числа не меняются — меняются только подписи (единицы измерения или названия). "
            "Принять безопасно, если новые подписи вам подходят.")).pack(anchor="w")

    def decide(accept):
        for b in bar.winfo_children():
            b.state(["disabled"])
        checks = dict(job.get("checks") or {})
        for v, key, off in off_vars:
            if v.get():
                checks = CH.switch_off(checks, key, off)

        def work():
            from ..core.store import Store
            st = Store(app.paths.db)
            try:
                n = 0
                for one in qids:
                    n += st.accept_quarantine(one) if accept else 0
                    if not accept:
                        st.reject_quarantine(one)
                st.set_state(job["id"], status="updated" if accept else "unchanged", category=None,
                             message="Отложенные данные приняты" if accept else "Отложенные данные отклонены")
                st.refresh_summary(job["id"])
                from ..excel import packet
                packet.rebuild(app.cfg, st, app.paths)
                return n
            finally:
                st.close()

        def done(n):
            if checks != (job.get("checks") or {}):
                app.cfg.update_job(job["id"], checks=checks)
            w.destroy()
            app.refresh_all()
            if accept:
                text = (f"Готово: данные приняты ({n:,} значений).".replace(",", " ") +
                        " Они уже в файлах для моделей — в модели нажмите «Данные → Обновить всё». "
                        "Статус показателя — «Обновлено».")
            else:
                text = ("Готово: отложенные данные отклонены, в моделях остаются прежние. Статус показателя — "
                        "«Без изменений». При следующей загрузке хаб снова сравнит данные.")
            messagebox.showinfo("Отложенные данные", text, parent=app.root)
        app.bg(work, done, "Принимаю данные…" if accept else "Отклоняю данные…")

    ttk.Button(bar, text="Принять — отдать в модели", style="Accent.TButton",
               command=lambda: decide(True)).pack(side="left")
    ttk.Button(bar, text="Отклонить — оставить прежние", command=lambda: decide(False)).pack(side="left", padx=8)
    ttk.Button(bar, text="Решить позже", command=w.destroy).pack(side="right")
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


def open_site_check(app, host, url, on_done=None):
    """Встроенный браузер на сайте: пользователь сам проходит проверку «я не робот» (или входит),
    после закрытия окна куки сайта сохраняются для хаба (без паролей)."""
    import json
    br = app.browser()
    if br is None or not br.available():
        messagebox.showwarning("Встроенный браузер", "Встроенный браузер не установлен. Запустите «Установка.bat» "
                                                     "ещё раз — он поставится автоматически.", parent=app.root)
        return

    def work():
        got = br.login(url) or {}
        if got.get("cookies"):
            d = app.paths.cache / "cookies"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{host}.json").write_text(json.dumps(got, ensure_ascii=False), encoding="utf-8")
        return got

    def done(_got):
        app.status.set("Окно браузера закрыто — пробую загрузить снова")
        if on_done:
            on_done()
    app.bg(work, done, "Открыл сайт во встроенном браузере — пройдите проверку, если она есть, и закройте окно…")
