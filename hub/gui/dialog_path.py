"""Окно «Путь к файлу»: шаги от стартовой страницы до файла (какую ссылку брать на каждой странице) — их можно
поправить: слова в тексте ссылки, слова рядом с ней (в карточке новости), кусок адреса, какую из подходящих
брать. «Проверить» проходит шаги и показывает, что нашлось на каждом."""
import tkinter as tk
from tkinter import messagebox, ttk

from ..core.errors import human_message
from ..sources import _path as PT
from ..sources import _web as WEB
from . import widgets as W

PICK_LABELS = dict(PT.PICKS)
PICK_KEYS = {v: k for k, v in PT.PICKS}


class PathDialog(tk.Toplevel):
    def __init__(self, app, start="", steps=None, clicks=None, job=None, on_done=None):
        """Новый путь: on_done(path, data, file_url) — когда файл скачан по шагам. job — изменить шаги показателя."""
        super().__init__(app.root)
        self.app, self.job, self.on_done = app, job, on_done
        if job:
            path = job["params"]["path"]
            start, steps = path["start"], path.get("steps") or []
        self.clicks = list(clicks or [])
        self.title("Путь к файлу")
        self.geometry("980x760")
        self.transient(app.root)
        bar = ttk.Frame(self, padding=12)
        bar.pack(side="bottom", fill="x")
        body = ttk.Frame(self, padding=(12, 10, 12, 0))
        body.pack(fill="both", expand=True)
        W.autowrap(ttk.Label(body, justify="left", text=(
            "Хаб будет каждый раз проходить этот путь сам: открыть стартовую страницу, на каждой странице взять "
            "ссылку по шагу и в конце скачать файл. Поэтому в шагах — слова, которые не меняются от месяца к месяцу "
            "(без дат). Все слова поля должны быть в тексте; регистр не важен. Пустое поле не проверяется."))).pack(
            fill="x")
        r = ttk.Frame(body)
        r.pack(fill="x", pady=(8, 4))
        ttk.Label(r, text="Стартовая страница:").pack(side="left")
        self.start = tk.StringVar(value=start)
        ttk.Entry(r, textvariable=self.start).pack(side="left", fill="x", expand=True, padx=6)

        self.sf = W.ScrollFrame(body)
        self.sf.pack(fill="both", expand=True, pady=(4, 0))
        self.rows = []
        for i, st in enumerate(steps or [{}]):
            self._add_row(st, self.clicks[i] if i < len(self.clicks) else None)
        add = ttk.Frame(body)
        add.pack(fill="x", pady=4)
        ttk.Button(add, text="＋ Шаг", command=lambda: self._add_row({}, None)).pack(side="left")
        ttk.Button(add, text="Проверить путь", style="Accent.TButton", command=self.check).pack(side="left", padx=8)
        self.out = tk.Text(body, height=8, wrap="word", font=(W.FONT, 10))
        self.out.pack(fill="x", pady=(4, 0))
        self.out.insert("1.0", "«Проверить путь» — хаб пройдёт шаги и покажет, какие ссылки нашлись.")
        self.out.configure(state="disabled")

        if job:
            ttk.Button(bar, text="Сохранить", style="Add.TButton", command=self.save).pack(side="right")
        else:
            ttk.Button(bar, text="Скачать файл по шагам — далее", style="Add.TButton",
                       command=self.finish).pack(side="right")
        ttk.Button(bar, text="Отмена", command=self.destroy).pack(side="right", padx=8)
        W.center(self, app.root)

    # --- шаги ----------------------------------------------------------------
    def _add_row(self, step, click):
        f = ttk.LabelFrame(self.sf.inner, padding=8)
        f.pack(fill="x", pady=4, padx=2)
        v = {"words": tk.StringVar(value=step.get("words") or ""), "near": tk.StringVar(value=step.get("near") or ""),
             "href": tk.StringVar(value=step.get("href") or ""),
             "pick": tk.StringVar(value=PICK_LABELS.get(step.get("pick") or "first", PT.PICKS[0][1]))}
        f.columnconfigure(1, weight=1)
        for row, (key, label) in enumerate((("words", "Слова в тексте ссылки:"),
                                            ("near", "Слова рядом со ссылкой (в карточке новости):"),
                                            ("href", "Кусок адреса ссылки (.xlsx, /document/):"))):
            ttk.Label(f, text=label).grid(row=row, column=0, sticky="w", padx=(0, 6), pady=1)
            ttk.Entry(f, textvariable=v[key]).grid(row=row, column=1, sticky="ew", pady=1)
        ttk.Label(f, text="Если подходят несколько — брать:").grid(row=3, column=0, sticky="w", padx=(0, 6), pady=1)
        ttk.Combobox(f, textvariable=v["pick"], values=[t for _k, t in PT.PICKS], state="readonly",
                     width=40).grid(row=3, column=1, sticky="w", pady=1)
        if click:
            seen = f"Вы щёлкнули: «{click.get('text') or 'значок без текста'}»"
            if click.get("near") and click.get("near") != click.get("text"):
                seen += f"; рядом: «{click['near'][:200]}»"
            W.autowrap(ttk.Label(f, text=seen, style="Hint.TLabel", justify="left")).grid(
                row=4, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        W.icon_button(f, "✕", "Убрать шаг", lambda: self._drop(f)).grid(row=0, column=2, sticky="ne", padx=(6, 0))
        self.rows.append((f, v))
        self._renumber()

    def _drop(self, frame):
        if len(self.rows) <= 1:
            return
        self.rows = [(f, v) for f, v in self.rows if f is not frame]
        frame.destroy()
        self._renumber()

    def _renumber(self):
        for n, (f, _v) in enumerate(self.rows, 1):
            f.configure(text=f"Шаг {n}" + (" — файл" if n == len(self.rows) else " — страница"))

    def path(self) -> dict:
        steps = []
        for _f, v in self.rows:
            steps.append({"words": PT.norm(v["words"].get()), "near": PT.norm(v["near"].get()),
                          "href": v["href"].get().strip(), "pick": PICK_KEYS.get(v["pick"].get(), "first")})
        return {"start": WEB.clean_url(self.start.get()), "steps": steps}

    def _valid(self, path):
        if not path["start"].startswith(("http://", "https://")):
            messagebox.showwarning("Путь к файлу", "Укажите адрес стартовой страницы (начинается с https://).",
                                   parent=self)
            return False
        for n, st in enumerate(path["steps"], 1):
            if not (st["words"] or st["near"] or st["href"]):
                messagebox.showwarning("Путь к файлу", f"В шаге {n} не заполнено ни одно поле — хаб не поймёт, "
                                       "какую ссылку брать.", parent=self)
                return False
        return True

    # --- проверка ------------------------------------------------------------
    def _say(self, text):
        if not self.winfo_exists():
            return
        self.out.configure(state="normal")
        self.out.delete("1.0", "end")
        self.out.insert("1.0", text)
        self.out.configure(state="disabled")

    def _context(self):
        from ..sources import Context
        return Context(paths=self.app.paths, browser=self.app.browser())

    def _walk(self, path):
        trace = []
        try:
            url = PT.follow(path, self._context(), trace=trace)
            return url, trace, None
        except Exception as e:  # noqa: BLE001
            return None, trace, e

    @staticmethod
    def trace_text(url, trace, err):
        lines = []
        for n, (page, count, chosen) in enumerate(trace, 1):
            line = f"Шаг {n}: страница {page}\n   подходящих ссылок: {count}"
            if chosen:
                line += f"; взята «{chosen['text'] or 'значок без текста'}» → {chosen['href']}"
            lines.append(line)
        if url:
            lines.append(f"\nФайл: {url}")
        if err is not None:
            lines.append("\nНе получилось: " + human_message(err))
        return "\n".join(lines)

    def check(self):
        path = self.path()
        if not self._valid(path):
            return
        self._say("Прохожу шаги…")
        self.app.bg(lambda: self._walk(path), lambda r: self._say(self.trace_text(*r)), "Проверяю путь к файлу…")

    # --- готово --------------------------------------------------------------
    def finish(self):
        path = self.path()
        if not self._valid(path):
            return

        def work():
            from ..sources.file_link import download
            ctx = self._context()
            url, trace, err = self._walk(path)
            if err is not None:
                return None, trace, err
            data, _final = download(url, ctx)
            return (url, data), trace, None

        def done(r):
            got, trace, err = r
            if err is not None:
                self._say(self.trace_text(None, trace, err))
                return
            url, data = got
            self.destroy()
            if self.on_done:
                self.on_done(path, data, url)
        self._say("Прохожу шаги и скачиваю файл…")
        self.app.bg(work, done, "Скачиваю файл по шагам…",
                    error=lambda e: self._say("Не получилось: " + human_message(e)))

    def save(self):
        path = self.path()
        if not self._valid(path):
            return
        params = dict(self.job["params"])
        params["path"] = path
        self.app.cfg.update_job(self.job["id"], params=params)
        self.destroy()
        self.app.refresh_all()
        if messagebox.askyesno("Путь к файлу", "Сохранено. Загрузить данные сейчас?", parent=self.app.root):
            self.app.update_jobs([self.job["id"]])
