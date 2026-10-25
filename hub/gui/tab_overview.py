"""«Обзор» — стартовый экран: светофор, проблемы с одной кнопкой действия, новые данные, модели."""
import calendar
import datetime as dt
import tkinter as tk
from tkinter import messagebox, ttk

from ..core import claude_pack, hostblock as HB, overview, periods as P, status as ST, winsched
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
        W.icon_button(head, "⟳", "Сейчас же проверить все показатели (без учёта расписания)",
                      lambda: app.update_jobs(force=True), text="Обновить всё сейчас",
                      style="Accent.TButton").pack(side="right")
        self.auto_info = W.autowrap(ttk.Label(self, style="Muted.TLabel", justify="left"))
        self.auto_info.pack(fill="x", pady=(2, 8))
        self.body = W.ScrollFrame(self)
        self.body.pack(fill="both", expand=True)
        today = dt.date.today()
        self.cal_month = (today.year, today.month)
        self.groups = {}                  # кнопки-фильтры источников: ключ → BooleanVar

    def refresh(self):
        app = self.app
        cfg, store = app.cfg, app.store
        allp = overview.problems(cfg, store)
        probs = [p for p in allp if ST.effective(p.job, p.state) != "delayed"]
        delayed = [p for p in allp if ST.effective(p.job, p.state) == "delayed"]
        stale = overview.runner_stale(store)
        enabled, next_run, last_code = winsched.status()
        last = store.meta_get("runner_heartbeat")
        if enabled is None:
            auto = "Автообновление работает через планировщик Windows (здесь не Windows)."
        elif enabled:
            auto = (f"Автообновление включено ({winsched.EVERY_TEXT}"
                    + (f", следующий запуск {next_run}). " if next_run else "). "))
            if getattr(app, "sched_fixed", None):
                auto += (f"Задача планировщика исправлена при запуске хаба ({', '.join(app.sched_fixed)} "
                         "не совпадали). ")
        else:
            auto = "Автообновление выключено — данные обновляются только по кнопке (включить — в «Настройках»). "
        self.auto_info.configure(text=auto + (f"Последнее обновление: {fmt_date(last)}." if last else ""))
        sched = overview.scheduler_stale(store, app.started) if enabled else None
        failed = enabled and last_code not in winsched.OK_RESULTS and last_code is not None \
            and not getattr(app, "sched_fixed", None)
        if failed:
            sched = (f"Планировщик Windows не смог запустить фоновое обновление: {winsched.result_text(last_code)}. "
                     "Нажмите «Пересоздать задачу» — хаб пропишет в ней правильный путь к программе.")
        elif sched and last_code is not None:
            sched += f" Последний запуск задачи: {winsched.result_text(last_code)}."
        bad = len(probs) + (1 if stale and enabled or sched else 0)
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
            W.autowrap(ttk.Label(inner, justify="left", text=(
                "1. Откройте вкладку «Найти данные», найдите показатель (например, ставки по вкладам в «Банк России»)\n"
                "    и нажмите «＋ В обновления».\n"
                "2. На вкладке «Модели» нажмите «Подключить модель» и выберите файл Excel.\n"
                "3. Отметьте, какие ряды получает модель. В модели нажмите «Данные → Обновить всё»."))).pack(fill="x")
            ttk.Button(inner, text="Найти данные →", command=lambda: self.app.show(self.app.find)).pack(anchor="w", pady=8)

        if stale and enabled or sched:
            self._section(inner, "Фоновое обновление")
            W.autowrap(ttk.Label(inner, text=sched or stale, style="yellow.TLabel", justify="left")).pack(fill="x")
            row = ttk.Frame(inner)
            row.pack(anchor="w", pady=(4, 0))
            ttk.Button(row, text="Пересоздать задачу", command=self.recreate_task).pack(side="left")
            ttk.Button(row, text="Открыть Планировщик заданий", command=winsched.open_scheduler).pack(side="left", padx=8)
        blocks = HB.active(store)
        if blocks:
            self._section(inner, "Сайт временно не пускает хаб")
            for host, till, reason in blocks:
                self._block_row(inner, host, till, reason)
        if probs:
            self._section(inner, "Проблемы")
            self._problem_rows(inner, probs)
        if delayed:
            self._section(inner, "Задерживаются (это не ошибка)")
            self._problem_rows(inner, delayed)

        self._news_header(inner)
        self.news = ttk.Frame(inner)
        self.news.pack(fill="x")
        self._fill_news()

        self._section(inner, "Модели с новыми данными")
        mods = models_with_new_data(cfg, store)
        if not mods:
            ttk.Label(inner, text="Нет моделей с непросмотренными новыми данными.", style="Muted.TLabel").pack(anchor="w")
        for name, n in mods:
            row = ttk.Frame(inner)
            row.pack(fill="x", pady=2)
            crit = "критичному " if n == 1 else "критичным "
            crit = crit if (cfg.model(name) or {}).get("critical") else ""
            ttk.Label(row, text=f"Откройте модель «{name}» — там новые данные по {n} {crit}{'ряду' if n == 1 else 'рядам'}.").pack(side="left")
            ttk.Button(row, text="Открыть модель", command=lambda nm=name: self.app.models.open_model(nm)).pack(side="left", padx=8)

    def recreate_task(self):
        ok, msg = winsched.enable(self.app.cfg)
        if ok:
            msg += ". Первый запуск — в течение 15 минут; это сообщение исчезнет, когда планировщик запустит хаб."
            self.app.sched_fixed = ["задача пересоздана вручную"]
            self.app._synced_times = winsched.rule_times(self.app.cfg)
            self.refresh()
        (messagebox.showinfo if ok else messagebox.showwarning)("Автообновление", msg, parent=self)

    # --- новые данные: фильтры, список, календарь ------------------------------------------
    def _news_header(self, parent):
        """Заголовок «Новые данные за неделю» и в одну линию с ним — сплошной ряд кнопок-фильтров."""
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(14, 4))
        ttk.Label(row, text="Новые данные за неделю", style="Title.TLabel").pack(side="left")
        chosen = set(self.app.cfg.settings().get("overview_sources") or [])
        seg = ttk.Frame(row)
        seg.pack(side="left", padx=(18, 0))
        self.groups = {}
        for key, title in overview.GROUPS:
            var = tk.BooleanVar(value=key in chosen)
            b = W.toggle_button(seg, title, var, self._filter_changed)
            b.pack(side="left")              # вплотную — сплошной ряд
            b.tooltip = W.Tooltip(b, f"Показывать только «{title}» (можно нажать несколько). "
                                     "Ничего не нажато — показываются все источники.")
            self.groups[key] = (var, b, title)
        self._label_filters()

    def _label_filters(self):
        for var, b, title in self.groups.values():
            b.configure(text=("✓ " if var.get() else "") + title)

    def chosen_groups(self):
        return {k for k, (var, _b, _t) in self.groups.items() if var.get()}

    def _filter_changed(self):
        self._label_filters()
        s = self.app.cfg.settings()
        s["overview_sources"] = [k for k, _t in overview.GROUPS if k in self.chosen_groups()]
        self.app.cfg.save_settings(s)          # нажатые фильтры сохраняются и после закрытия программы
        self._fill_news()

    def _fill_news(self):
        for w in self.news.winfo_children():
            w.destroy()
        cfg, store = self.app.cfg, self.app.store
        groups = self.chosen_groups()
        self.news.columnconfigure(0, weight=1)
        left = ttk.Frame(self.news)
        left.grid(row=0, column=0, sticky="new")
        self.cal = ttk.Frame(self.news, padding=(10, 0, 0, 0))
        self.cal.grid(row=0, column=1, sticky="ne")
        ups = overview.recent_updates(cfg, store, groups=groups)
        if not ups:
            ttk.Label(left, text="Пока нет." if not groups else "По выбранным источникам за неделю ничего нового.",
                      style="Muted.TLabel").pack(anchor="w")
        left.columnconfigure(0, weight=2, minsize=140)
        left.columnconfigure(1, weight=3, minsize=160)
        for i, u in enumerate(ups):
            W.autowrap(ttk.Label(left, text=f"● {u['job']['name']}", style="green.TLabel", justify="left"),
                       initial=300).grid(row=i, column=0, sticky="new", padx=(0, 10), pady=1)
            W.autowrap(ttk.Label(left, text=overview.update_text(u), style="Muted.TLabel", justify="left"),
                       initial=300).grid(row=i, column=1, sticky="new", pady=1)
        self._fill_calendar()

    def _shift_month(self, step):
        y, m = self.cal_month
        m += step
        self.cal_month = (y + (m - 1) // 12, (m - 1) % 12 + 1)
        self._fill_calendar()

    def _fill_calendar(self):
        """Календарь месяца: закрашены дни, когда приходили новые данные; наведите мышь — что именно."""
        for w in self.cal.winfo_children():
            w.destroy()
        y, m = self.cal_month
        days = overview.month_updates(self.app.cfg, self.app.store, y, m, self.chosen_groups())
        bg = ttk.Style(self).lookup("TFrame", "background") or W.BG
        top = ttk.Frame(self.cal)
        top.grid(row=0, column=0, columnspan=7, sticky="ew", pady=(0, 4))
        W.icon_button(top, "◀", "Предыдущий месяц", lambda: self._shift_month(-1)).pack(side="left")
        W.icon_button(top, "▶", "Следующий месяц", lambda: self._shift_month(1)).pack(side="right")
        ttk.Label(top, text=f"{P.MONTHS_RU[m - 1].capitalize()} {y}", font=(W.FONT, 10, "bold"),
                  anchor="center").pack(side="left", fill="x", expand=True)
        for j, name in enumerate(("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")):
            tk.Label(self.cal, text=name, width=4, bg=bg, fg=W.MUTED, font=(W.FONT, 9)).grid(row=1, column=j)
        today = dt.date.today()
        for r, week in enumerate(calendar.Calendar().monthdatescalendar(y, m)):
            for j, d in enumerate(week):
                if d.month != m:
                    tk.Label(self.cal, text="", width=4, bg=bg).grid(row=r + 2, column=j)
                    continue
                ups = days.get(d)
                lab = tk.Label(self.cal, text=str(d.day), width=4, pady=3, bg=W.UPDATED_BG if ups else bg,
                               fg=W.UPDATED_FG if ups else ("#9aa5b1" if d > today else "#1f2933"),
                               font=(W.FONT, 9, "bold" if ups or d == today else "normal"),
                               relief="solid" if d == today else "flat", borderwidth=1 if d == today else 0)
                lab.grid(row=r + 2, column=j, padx=1, pady=1)
                if ups:
                    lines = [f"• {u['job']['name']} — {overview.update_text(u)}" for u in ups[:15]]
                    if len(ups) > 15:
                        lines.append(f"…и ещё {len(ups) - 15}")
                    lab.tooltip = W.Tooltip(lab, f"{d:%d.%m.%Y}: новые данные\n" + "\n".join(lines), delay=200)
        n = len(days)
        ttk.Label(self.cal, text=(f"Дней с обновлениями: {n}" if n else "В этом месяце обновлений не было"),
                  style="Hint.TLabel").grid(row=9, column=0, columnspan=7, pady=(4, 0))

    @staticmethod
    def _section(parent, title):
        ttk.Label(parent, text=title, style="Title.TLabel").pack(anchor="w", pady=(14, 4))

    def _block_row(self, parent, host, till, reason=""):
        box = ttk.Frame(parent, padding=(0, 2, 0, 6))
        box.pack(fill="x")
        check = "ползун" in (reason or "") or "я не робот" in (reason or "")
        if check:
            main = (f"{HB.title(host)} просит пройти проверку «я не робот» (сдвинуть ползунок). Хаб не проходит её "
                    f"сам и до {till:%H:%M %d.%m} к сайту не обращается.")
            more = ("Нажмите «Пройти проверку» — откроется сайт. Выполните поиск в форме, сдвиньте ползунок, когда "
                    "сайт его покажет, дождитесь таблицы и закройте окно — хаб сразу продолжит загрузку. Обычно после "
                    "этого сайт какое-то время пускает без проверки.")
        else:
            main = (f"{HB.title(host)} перестал отвечать хабу («доступ запрещён»). Обычно так сайт защищается, когда к "
                    f"нему было много запросов, и через несколько часов снимает ограничение сам. Хаб не обращается к "
                    f"этому сайту до {till:%H:%M %d.%m} и потом попробует снова — делать ничего не нужно.")
            more = ("Если данные нужны раньше: «Открыть сайт» — откроется окно встроенного браузера. Если сайт покажет "
                    "проверку «я не робот», пройдите её и закройте окно — хаб запомнит это и сразу попробует снова. "
                    "Если сайт открылся сразу без проверки, просто закройте окно.")
        W.autowrap(ttk.Label(box, justify="left", style="yellow.TLabel", text=main)).pack(fill="x")
        W.autowrap(ttk.Label(box, justify="left", style="Muted.TLabel", text=more)).pack(fill="x", pady=(2, 4))
        row = ttk.Frame(box)
        row.pack(anchor="w")
        ttk.Button(row, text="Пройти проверку" if check else "Открыть сайт",
                   command=lambda: self.site_check(host, form=check)).pack(side="left")
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

    def site_check(self, host, form=False):
        open_site_check(self.app, host, HB.site_url(host), on_done=lambda: self.retry_host(host), form=form)

    MAX_ROWS = 40

    def _problem_rows(self, parent, items):
        table = ttk.Frame(parent)
        table.pack(fill="x")
        for p in items[:self.MAX_ROWS]:
            self._problem_row(table, p)
        if len(items) > self.MAX_ROWS:
            ttk.Label(parent, text=f"…и ещё {len(items) - self.MAX_ROWS} — все на вкладке «Мои показатели».",
                      style="Muted.TLabel").pack(anchor="w")

    def _problem_row(self, parent, p):
        """Строка проблемы: статус | показатель | что случилось | кнопка — ровными столбцами."""
        if not getattr(parent, "_grid_ready", False):
            parent.columnconfigure(1, weight=2, minsize=160)
            parent.columnconfigure(2, weight=3, minsize=200)
            parent._grid_ready = True
        r = parent.grid_size()[1]
        st = ST.effective(p.job, p.state)
        ttk.Label(parent, text=W.status_text(st), style=f"{ST.color(st)}.TLabel").grid(
            row=r, column=0, sticky="nw", padx=(0, 10), pady=4)
        W.autowrap(ttk.Label(parent, text=p.job["name"], font=(W.FONT, 10, "bold"), justify="left")).grid(
            row=r, column=1, sticky="new", padx=(0, 10), pady=4)
        W.autowrap(ttk.Label(parent, text=p.text, style="Muted.TLabel", justify="left")).grid(
            row=r, column=2, sticky="new", padx=(0, 10), pady=4)
        if p.action_label:
            ttk.Button(parent, text=p.action_label, command=lambda: self.act(p)).grid(row=r, column=3, sticky="ne", pady=2)

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
        if key and key not in seen and key != "future_ok":
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
    W.autowrap(ttk.Label(body, justify="left", text=(
        f"При загрузке {when} хаб заметил необычное и не стал сразу отдавать новые данные в модели — "
        "чтобы не испортить ваши расчёты. Сейчас в моделях прежние данные."))).pack(fill="x")
    for r in reasons[:MAX_REASONS]:
        text, _k, _o = CH.explain(r)
        W.autowrap(ttk.Label(body, text="• " + short(r, 300), justify="left")).pack(fill="x", padx=(10, 0))
        if text:
            W.autowrap(ttk.Label(body, text=text, style="Muted.TLabel", justify="left")).pack(fill="x", padx=(26, 0))
    if len(reasons) > MAX_REASONS:
        ttk.Label(body, text=f"…и ещё {len(reasons) - MAX_REASONS} замечаний", style="Muted.TLabel").pack(anchor="w")

    n_new = n_changed = 0
    for one in qids:
        a_, b_ = app.store.quarantine_counts(one)
        n_new, n_changed = n_new + a_, n_changed + b_
    lost_only = all(r.startswith("Исчезли ряды") for r in reasons) and reasons
    ttk.Label(body, text="Что изменится, если принять", style="Title.TLabel").pack(anchor="w", pady=(10, 0))
    if n_new or n_changed:
        W.autowrap(ttk.Label(body, justify="left", text=(
            f"Новых значений: {n_new:,}, изменённых прежних: {n_changed:,}. Ниже — примеры "
            f"(«Было» — что сейчас в моделях).").replace(",", " "))).pack(fill="x")
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
    elif lost_only:
        W.autowrap(ttk.Label(body, justify="left", text=(
            "Новых чисел нет. Принять — значит согласиться, что этих рядов у источника больше нет: хаб перестанет "
            "их ждать и больше не будет спрашивать о них. Их прежние значения останутся в базе и в моделях. Если ряды "
            "снова появятся у источника — хаб подхватит их сам. Если ряды пропасть не должны (например, вы их "
            "по-прежнему видите на сайте) — «Отклонить» и «Отправить Claude» на этом показателе."))).pack(fill="x")
    else:
        W.autowrap(ttk.Label(body, justify="left", text=(
            "Числа не меняются — меняются только подписи (единицы измерения или названия). "
            "Принять безопасно, если новые подписи вам подходят."))).pack(fill="x")

    def decide(accept):
        for b in bar.winfo_children():
            b.state(["disabled"])
        checks = dict(job.get("checks") or {})
        for v, key, off in off_vars:
            if v.get():
                checks = CH.switch_off(checks, key, off)
        if accept and any(r.startswith("Дата из будущего") for r in reasons):
            checks["future_ok"] = True       # подтвердил, что даты в будущем — это нормально: больше не спрашивать

        def work():
            from ..core.store import Store
            st = Store(app.paths.db)
            try:
                n = gone = 0
                for one in qids:
                    if accept:
                        gone = max(gone, st.retire_lost(one))     # исчезнувшие ряды больше не ждём
                        n += st.accept_quarantine(one)
                    else:
                        st.reject_quarantine(one)
                st.set_state(job["id"], status="updated" if accept else "unchanged", category=None,
                             message="Отложенные данные приняты" if accept else "Отложенные данные отклонены")
                st.refresh_summary(job["id"])
                from ..excel import packet
                packet.rebuild(app.cfg, st, app.paths)
                return n, gone
            finally:
                st.close()

        def done(res):
            n, gone = res
            if checks != (job.get("checks") or {}):
                app.cfg.update_job(job["id"], checks=checks)
            w.destroy()
            app.refresh_all()
            if accept and not n and gone:
                text = (f"Готово: хаб больше не ждёт исчезнувшие ряды ({gone:,}) и не будет спрашивать о них снова. "
                        "Их прежние значения остались в моделях. Статус показателя — «Обновлено».").replace(",", " ")
            elif accept:
                text = (f"Готово: данные приняты ({n:,} значений).".replace(",", " ") +
                        (f" Исчезнувшие ряды ({gone}) хаб больше не ждёт." if gone else "") +
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


FORM_CHECK_BANNER = ("Хаб данных: выполните поиск в форме (как при добавлении показателя). Если сайт покажет "
                     "ползунок — сдвиньте его. Дождитесь таблицы и закройте окно — хаб продолжит сам.")


def open_site_check(app, host, url, on_done=None, form=False):
    """Встроенный браузер на сайте: пользователь сам проходит проверку «я не робот» (или входит),
    после закрытия окна куки сайта сохраняются для хаба (без паролей)."""
    import json
    br = app.browser()
    if br is None or not br.available():
        messagebox.showwarning("Встроенный браузер", "Встроенный браузер не установлен. Запустите «Установка.bat» "
                                                     "ещё раз — он поставится автоматически.", parent=app.root)
        return

    def work():
        got = br.login(url, banner=FORM_CHECK_BANNER if form else None) or {}
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
