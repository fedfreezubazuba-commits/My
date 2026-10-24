"""
Вкладка «Сайт» — мастер:
  1. вставить ссылку (рекламные параметры отрезаются сами);
  2. хаб показывает найденное: цены и числа, таблицы с превью, ссылки на файлы;
  3. при необходимости — «Выбрать на странице» во встроенном браузере;
  4. «Войти на сайт» — тоже там;
  5. «＋ В обновления».
Если мастер не справился — «Отправить Claude» формирует запрос на новый источник.
"""
import tkinter as tk
from tkinter import messagebox, ttk

from ..core import claude_pack
from ..core.base_fmt import fmt
from ..sources import Context
from ..sources import _analyze as A
from ..sources import _web as W
from . import widgets as W2
from .dialog_add import AddDialog


class SiteTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
        self.found = None
        self.page_text = ""
        r = ttk.Frame(self)
        r.pack(fill="x")
        ttk.Label(r, text="1. Ссылка на страницу:", style="Title.TLabel").pack(side="left")
        self.url = tk.StringVar()
        e = ttk.Entry(r, textvariable=self.url, font=(W2.FONT, 11))
        e.pack(side="left", fill="x", expand=True, padx=8)
        e.bind("<Return>", lambda _e: self.analyze())
        ttk.Button(r, text="Найти данные на странице", style="Accent.TButton", command=self.analyze).pack(side="left")
        tools = ttk.Frame(self)
        tools.pack(fill="x", pady=(6, 0))
        ttk.Button(tools, text="🖱 Выбрать на странице…", command=self.pick).pack(side="left")
        ttk.Button(tools, text="🔑 Войти на сайт…", command=self.login).pack(side="left", padx=6)
        W2.icon_button(tools, "✍", "Для сайтов-приложений, где данные подгружаются скриптом (история цен на графике, "
                                  "таблица после заполнения формы): откройте данные в окне браузера — хаб запомнит, "
                                  "откуда страница их берёт, и будет брать их сам (по годам, если есть период)",
                       self.record_form, text="Записать данные страницы…").pack(side="left")
        ttk.Button(tools, text="Не получилось — Отправить Claude", command=self.claude).pack(side="right")
        self.info = ttk.Label(self, style="Muted.TLabel", wraplength=1100, justify="left",
                              text="Вставьте ссылку на страницу с ценой, таблицей или файлом отчёта. "
                                   "Рекламные хвосты (utm_…, yclid…) хаб уберёт сам.")
        self.info.pack(anchor="w", pady=6)

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True)
        vf = ttk.Frame(self.nb, padding=6)
        self.nb.add(vf, text="Цены и числа")
        f, self.values = W2.tree_with_scroll(vf, ("title", "value", "unit"), ("Что", "Значение", "Единицы"),
                                             {"title": 640, "value": 140, "unit": 120}, selectmode="extended")
        f.pack(fill="both", expand=True)
        ttk.Label(vf, style="Hint.TLabel", text="Можно выделить несколько строк (Ctrl+щелчок) — они станут одним "
                                                "показателем с несколькими рядами.").pack(anchor="w")
        tf = ttk.Frame(self.nb, padding=6)
        self.nb.add(tf, text="Таблицы")
        pw = ttk.PanedWindow(tf, orient="horizontal")
        pw.pack(fill="both", expand=True)
        f, self.tables = W2.tree_with_scroll(pw, ("title", "rows"), ("Таблица", "Строк"), {"title": 360, "rows": 60})
        pw.add(f, weight=1)
        self.tables.bind("<<TreeviewSelect>>", lambda e: self.show_table())
        self.preview_frame = ttk.Frame(pw)
        pw.add(self.preview_frame, weight=2)
        self.preview = None
        mode = ttk.Frame(tf)
        mode.pack(fill="x", pady=(4, 0))
        self.mode = tk.StringVar(value="snapshot")
        ttk.Radiobutton(mode, text="Снимок: каждая строка — свой ряд, дата — с страницы или сегодняшняя",
                        value="snapshot", variable=self.mode).pack(side="left")
        ttk.Radiobutton(mode, text="История: в строках даты", value="history", variable=self.mode).pack(side="left", padx=12)
        ff = ttk.Frame(self.nb, padding=6)
        self.nb.add(ff, text="Ссылки на файлы")
        f, self.files = W2.tree_with_scroll(ff, ("text", "url"), ("Ссылка", "Адрес"), {"text": 380, "url": 600})
        f.pack(fill="both", expand=True)
        ttk.Label(ff, style="Hint.TLabel", text="Выберите файл — он откроется на вкладке «Файл» для выбора таблицы.").pack(anchor="w")

        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(8, 0))
        self.add_btn = ttk.Button(bar, text="＋ В обновления", style="Add.TButton", command=self.add, state="disabled")
        self.add_btn.pack(side="right")

    # --- шаги мастера --------------------------------------------------------
    def analyze(self):
        url = W.clean_url(self.url.get())
        if not url:
            return
        from ..sources import fred
        if fred.is_fred(url) and fred.ids_from_text(url):
            self.app.find.open_fred(url)          # у FRED постоянная ссылка на CSV по коду ряда — своя вкладка
            return
        self.url.set(url)
        ctx = Context(paths=self.app.paths, browser=self.app.browser())

        def useful(text):
            found = A.analyze(text, url)
            return bool(found.values or found.tables or found.files)

        def work():
            text, via, _final = W.fetch_page(url, ctx, "auto", need=useful)
            return text, via, A.analyze(text, url)

        self.info.configure(text="Открываю страницу…")
        self.app.bg(work, self._show, "Открываю страницу и ищу данные…", error=self._failed)

    def _failed(self, exc):
        from ..core import errors as E
        cat = E.classify(exc)
        hint = {E.LOGIN: "Нажмите «Войти на сайт», войдите и попробуйте ещё раз. Если нужные данные на сайте открываются "
                         "и без входа (публичная страница, график цен) — нажмите «✍ Записать данные страницы…».",
                E.BOT: "Нажмите «Выбрать на странице» — во встроенном браузере сайт откроется как обычно.",
                E.STRUCTURE: "Попробуйте «Выбрать на странице» и щёлкнуть по нужному числу."}.get(cat, "")
        self.info.configure(text=f"Не получилось: {E.human_message(exc)}. {hint}", style="red.TLabel")

    def _show(self, res):
        text, via, found = res
        self.page_text = text
        self.found = found
        for t in (self.values, self.tables, self.files):
            t.delete(*t.get_children())
        for i, v in enumerate(found.values):
            self.values.insert("", "end", iid=f"v{i}", values=(v["title"], fmt(v["value"]), v["unit"]))
        for i, t in enumerate(found.tables):
            self.tables.insert("", "end", iid=f"t{i}", values=(t.title or f"Таблица {i + 1} ({'плитки' if t.kind == 'cards' else 'таблица'})",
                                                              len(t.rows)))
        for i, (txt, u) in enumerate(found.files):
            self.files.insert("", "end", iid=f"f{i}", values=(txt, u))
        self.files.bind("<Double-1>", lambda e: self.open_file())
        msg = found.summary()
        if found.page_date:
            msg += f". Дата отчёта на странице: {found.page_date:%d.%m.%Y}" + (" (неделя)" if found.page_is_week else "")
        if found.profile:
            msg += f". Для сайта {found.profile.NAME} есть готовый профиль: цена за штуку, за кг и наличие"
        if via == "browser":
            msg += ". Страница прочитана встроенным браузером"
        self.info.configure(text=msg + ".", style="Muted.TLabel")
        if found.values:
            self.nb.select(0)
            self.values.selection_set(self.values.get_children())
        elif found.tables:
            self.nb.select(1)
            self.tables.selection_set("t0")
        elif found.files:
            self.nb.select(2)
        self.add_btn.configure(state="normal" if (found.values or found.tables) else "disabled")

    def show_table(self):
        sel = self.tables.selection()
        if not sel or not self.found:
            return
        t = self.found.tables[int(sel[0][1:])]
        if self.preview is not None:
            self.preview.master.destroy()
        rows = t.preview(12)
        width = max(len(r) for r in rows) if rows else 1
        cols = [f"c{j}" for j in range(width)]
        f, self.preview = W2.tree_with_scroll(self.preview_frame, cols, [f"{j + 1}" for j in range(width)],
                                              {c: 130 for c in cols})
        f.pack(fill="both", expand=True)
        for r in rows:
            self.preview.insert("", "end", values=r + [""] * (width - len(r)))
        try:
            from ..sources._tables import guess_columns
            _l, vals, d = guess_columns(t)
            self.app.status.set(f"Значения возьму из столбца {', '.join(str(v + 1) for v in vals)}"
                                + (f", дату — из столбца {d + 1}" if d is not None else ""))
        except Exception:  # noqa: BLE001
            pass

    def add(self):
        if not self.found:
            return
        tab = self.nb.index(self.nb.select())
        if tab == 0:
            keys = [self.found.values[int(i[1:])]["field"]["key"] for i in self.values.selection()]
            if not keys:
                messagebox.showinfo("Выберите", "Выделите одно или несколько значений.", parent=self)
                return
            params, name = A.value_params(self.found, keys)
            AddDialog(self.app, "web_value", params, name)
        elif tab == 1:
            sel = self.tables.selection()
            if not sel:
                messagebox.showinfo("Выберите", "Выделите таблицу слева.", parent=self)
                return
            t = self.found.tables[int(sel[0][1:])]
            params, name = A.table_params(self.found, t, self.mode.get())
            note = ("Каждая строка таблицы станет рядом. " +
                    ("Дата — дата отчёта на странице." if self.found.page_date else
                     "Если на странице нет даты, хаб ставит дату получения и сам копит историю."))
            AddDialog(self.app, "web_table", params, name, note=note)
        else:
            self.open_file()

    def open_file(self):
        sel = self.files.selection()
        if not sel or not self.found:
            return
        _txt, u = self.found.files[int(sel[0][1:])]
        ft = self.app.find.file
        if ft:
            self.app.find.nb.select(ft)
            ft.url.set(u)
            ft.load_url()

    # --- встроенный браузер --------------------------------------------------
    def _browser(self):
        br = self.app.browser()
        if br is None or not br.available():
            messagebox.showwarning("Встроенный браузер", "Встроенный браузер не установлен. Запустите «Установка.bat» "
                                                         "ещё раз — он поставится автоматически.", parent=self)
            return None
        return br

    def pick(self):
        url = W.clean_url(self.url.get())
        br = self._browser() if url else None
        if not br:
            return
        self.info.configure(text="Откроется окно браузера. Щёлкните по нужному числу (с Shift — по таблице) и "
                                 "закройте окно.", style="Muted.TLabel")
        self.app.bg(lambda: br.pick(url), self._picked, "Жду выбора в окне браузера…", error=self._failed)

    def _picked(self, pick):
        if not pick:
            self.info.configure(text="Окно закрыто без выбора.", style="Muted.TLabel")
            return
        url = W.clean_url(pick.get("url") or self.url.get())
        page_html = pick.get("page_html", "")
        if pick["kind"] == "table":
            t = A.picked_table(url, pick)
            if t is None:
                self.info.configure(text="Не удалось распознать в выбранном блоке таблицу с числами. Попробуйте "
                                         "щёлкнуть по одному числу или по блоку чуть шире.", style="red.TLabel")
                return
            # показываем всё найденное на странице и выделяем выбранную таблицу — видно, что попадёт в хаб
            self.url.set(url)
            self._show((page_html, "browser", A.analyze(page_html, url)))
            idx = next((i for i, x in enumerate(self.found.tables) if x.rows[:3] == t.rows[:3]), None)
            if idx is not None:
                self.nb.select(1)
                self.tables.selection_set(f"t{idx}")
                self.tables.see(f"t{idx}")
            params, name = A.table_params(self.found, t)
            note = (f"В строках таблицы — даты ({len(t.rows)} строк): каждый числовой столбец станет рядом, "
                    "история будет копиться с каждым обновлением." if params["mode"] == "history" else
                    f"Каждая строка выбранной таблицы ({len(t.rows)} строк) станет рядом.")
            AddDialog(self.app, "web_table", params, name, note=note)
            return
        params, label = A.picked_value_params(url, pick)
        from ..sources.web_value import extract_fields
        got = extract_fields(params["fields"], W.parse_html(pick["page_html"]), pick["page_html"], url)
        value = got["value"][0]
        if value is None:
            self.info.configure(text=f"В выбранном элементе нет числа: «{pick.get('text', '')[:60]}».", style="red.TLabel")
            return
        params["fetch_mode"] = "auto"        # сначала обычная загрузка, при неудаче — встроенный браузер
        self.info.configure(text=f"Выбрано: {fmt(value)} («{label}»).", style="green.TLabel")
        title = (self.found.title if self.found else "") or W.site_name(url)
        AddDialog(self.app, "web_value", params, f"{title} — {label}")

    def record_form(self):
        url = W.clean_url(self.url.get())
        br = self._browser() if url else None
        if not br:
            if not url:
                messagebox.showinfo("Ссылка", "Сначала вставьте ссылку на страницу с формой.", parent=self)
            return
        self.info.configure(text="Откроется окно браузера. Откройте на сайте нужные данные (если есть форма — заполните "
                                 "её и нажмите поиск). Когда таблица или график появятся — закройте окно.",
                            style="Muted.TLabel")
        self.app.bg(lambda: br.record(url), lambda c: self._recorded(url, c),
                    "Жду, пока вы заполните форму в окне браузера…", error=self._failed)

    def _recorded(self, url, cands):
        from ..sources import _forms as F
        from .dialog_form import FormDialog
        # что записали — на случай «Не получилось — Отправить Claude»
        self.page_text = "\n\n".join(f"<!-- {c['resource_type']} {c['method']} {c['url']}\n     поля: {c.get('body')}\n"
                                      f"     рамка: {c.get('frame_url')} -->\n{c['text'][:30000]}" for c in cands[-15:])
        if not cands:
            self.info.configure(text="Окно закрыто, а сайт ничего не ответил. Попробуйте ещё раз: дождитесь таблицы "
                                     "с данными и только потом закройте окно.", style="red.TLabel")
            return
        cand, table = F.best_candidate(cands)
        if cand is None:
            self.info.configure(text="В ответах сайта не нашлось таблицы или графика с числами. Нажмите «Не получилось — "
                                     "Отправить Claude» — записанные ответы сайта приложатся к запросу.",
                                style="red.TLabel")
            return
        self.info.configure(text=f"Записано, откуда страница берёт данные: таблица из {len(table.rows)} строк.",
                            style="green.TLabel")
        FormDialog(self.app, url, cand, table)

    def login(self):
        url = W.clean_url(self.url.get())
        br = self._browser() if url else None
        if not br:
            if not url:
                messagebox.showinfo("Ссылка", "Сначала вставьте ссылку на сайт.", parent=self)
            return
        self.info.configure(text="Откроется окно браузера. Войдите на сайт (или выберите магазин) и закройте окно. "
                                 "Хаб не видит и не хранит ваш пароль.", style="Muted.TLabel")
        self.app.bg(lambda: br.login(url), lambda _r: self.analyze(), "Жду, пока вы войдёте на сайт…",
                    error=self._failed)

    def login_for_job(self, job):
        """Кнопка «Войти на сайт» с «Обзора» для показателя со статусом «Нужен вход»."""
        from .. import sources
        from ..core import hostblock
        from .tab_overview import open_site_check
        p = job.get("params", {})
        src = sources.get(job["source"])
        url = p.get("url") or p.get("page_url") or src.open_url(p)      # у ЕМИСС ссылки в параметрах нет
        if not url:
            return
        host = src.host(p)

        def done():
            hostblock.lift(self.app.store, host)
            self.app.update_jobs([job["id"]])
        open_site_check(self.app, host, url, on_done=done)

    def claude(self):
        url = self.url.get().strip()
        if not url:
            messagebox.showinfo("Ссылка", "Вставьте ссылку на страницу, с которой не получилось.", parent=self)
            return
        from tkinter import simpledialog
        want = simpledialog.askstring("Что нужно извлечь", "Что нужно брать с этой страницы? (например: «цена за кг», "
                                                           "«таблица ставок фрахта»)", parent=self) or "не указано"
        page_path = None
        if self.page_text:
            self.app.paths.claude.mkdir(parents=True, exist_ok=True)
            page_path = self.app.paths.claude / f"страница_{W.site_name(url)}.html"
            page_path.write_text(claude_pack.scrub(self.page_text), encoding="utf-8")
        notes = self.info.cget("text")
        text = claude_pack.new_source_package(url, want, page_path, notes)
        path = claude_pack.save(self.app.paths, text, "новый_источник_" + W.site_name(url))
        W2.copy_to_clipboard(self.app.root, text)
        messagebox.showinfo("Отправить Claude", f"Запрос на новый источник скопирован в буфер обмена и сохранён:\n{path}"
                            + (f"\n\nСохранённая страница: {page_path}" if page_path else "")
                            + "\n\nОткройте новую сессию Claude Code для проекта и вставьте текст (Ctrl+V).", parent=self)
