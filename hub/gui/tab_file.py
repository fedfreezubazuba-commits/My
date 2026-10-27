"""
Вкладка «Файл» — файл по ссылке (xlsx/xls/csv/pdf) или из папки «Входящие», с превью
и выбором листа/таблицы. Для «свежего отчёта», у которого меняется адрес, можно указать
страницу и шаблон ссылки.
"""
import shutil
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ..core.config import slug
from ..core.errors import human_message
from ..excel.exports import open_file
from ..sources import _files as F
from ..sources import _tables as T
from ..sources import _web as W
from ..sources.file_link import download
from . import widgets as W2
from .dialog_add import AddDialog


class FileTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
        self.tables = []
        self.source = None          # ("url", адрес) | ("inbox", путь)
        r = ttk.Frame(self)
        r.pack(fill="x")
        ttk.Label(r, text="Ссылка на файл:", style="Title.TLabel").pack(side="left")
        self.url = tk.StringVar()
        e = ttk.Entry(r, textvariable=self.url, font=(W2.FONT, 11))
        e.pack(side="left", fill="x", expand=True, padx=8)
        e.bind("<Return>", lambda _e: self.load_url())
        ttk.Button(r, text="Открыть", style="Accent.TButton", command=self.load_url).pack(side="left")
        r2 = ttk.Frame(self)
        r2.pack(fill="x", pady=6)
        ttk.Label(r2, text="или файл, выгруженный вручную:").pack(side="left")
        ttk.Button(r2, text="Выбрать файл и положить во «Входящие»…", command=self.load_inbox).pack(side="left", padx=6)
        ttk.Button(r2, text="Открыть папку «Входящие»",
                   command=lambda: open_file(self.app.paths.inbox)).pack(side="left")
        r3 = ttk.Frame(self)
        r3.pack(fill="x")
        ttk.Label(r3, text="Если адрес файла каждый раз новый — страница со ссылками:").pack(side="left")
        self.page_url = tk.StringVar()
        ttk.Entry(r3, textvariable=self.page_url, width=40).pack(side="left", padx=6)
        ttk.Label(r3, text="слова в ссылке:").pack(side="left")
        self.pattern = tk.StringVar()
        ttk.Entry(r3, textvariable=self.pattern, width=20).pack(side="left", padx=6)
        r4 = ttk.Frame(self)
        r4.pack(fill="x", pady=(6, 0))
        ttk.Label(r4, text="Файл лежит в свежей новости или за несколькими ссылками:").pack(side="left")
        W2.icon_button(r4, "✍", "Откроется окно браузера: пройдите по ссылкам до файла и скачайте его. Хаб запишет "
                                "щелчки и покажет шаги — их можно поправить", self.record_path,
                       text="Записать путь к файлу…").pack(side="left", padx=6)
        self.info = ttk.Label(self, style="Muted.TLabel", text="Поддерживаются Excel (xlsx, xls), CSV, PDF с таблицами и архивы zip с такими файлами.")
        self.info.pack(anchor="w", pady=6)
        pw = ttk.PanedWindow(self, orient="horizontal")
        pw.pack(fill="both", expand=True)
        f, self.list = W2.tree_with_scroll(pw, ("title", "rows"), ("Лист / таблица", "Строк"), {"title": 280, "rows": 60})
        pw.add(f, weight=1)
        self.list.bind("<<TreeviewSelect>>", lambda e: self.show())
        self.pf = ttk.Frame(pw)
        pw.add(self.pf, weight=3)
        self.preview = None
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(6, 0))
        self.mode = tk.StringVar(value="history")
        ttk.Radiobutton(bar, text="История: в строках даты", value="history", variable=self.mode).pack(side="left")
        ttk.Radiobutton(bar, text="Снимок: каждая строка — ряд", value="snapshot", variable=self.mode).pack(side="left", padx=10)
        ttk.Radiobutton(bar, text="Набор данных: дата, разрезы и числа в строках (как в API)", value="dataset",
                        variable=self.mode).pack(side="left")
        ttk.Label(bar, text="Периодичность:").pack(side="left", padx=(16, 4))
        self.per = tk.StringVar(value="month")
        ttk.Combobox(bar, textvariable=self.per, values=["day", "week", "month", "quarter", "year"], width=9,
                     state="readonly").pack(side="left")
        self.add_btn = ttk.Button(bar, text="＋ В обновления", style="Add.TButton", command=self.add, state="disabled")
        self.add_btn.pack(side="right")
        self.sheets_btn = W2.icon_button(bar, "▤", "Перенести листы файла в модель как есть — на постоянные имена листов; "
                                                  "хаб обновляет их, когда файл меняется", self.to_sheets,
                                         text="Листы в модель…")
        self.sheets_btn.pack(side="right", padx=8)
        self.sheets_btn.state(["disabled"])
        self.data = None
        self.file_url = ""          # адрес файла, найденный по шагам пути

    def record_path(self):
        """✍ Записать путь: окно браузера → щелчки → шаги (окно «Путь к файлу»)."""
        start = W.clean_url(self.page_url.get() or self.url.get())
        if not start:
            from tkinter import simpledialog
            start = W.clean_url(simpledialog.askstring(
                "Путь к файлу", "Адрес страницы, с которой начинать (например, список отчётов):", parent=self) or "")
        if not start:
            return
        browser = self.app.browser()
        if browser is None or not browser.available():
            messagebox.showwarning("Путь к файлу", "Для записи нужен встроенный браузер — запустите «Установка.bat».",
                                   parent=self)
            return
        messagebox.showinfo("Путь к файлу", "Сейчас откроется окно браузера. Пройдите по ссылкам до нужного файла и "
                            "скачайте его, как обычно. Как только начнётся скачивание, окно закроется само, и хаб "
                            "покажет записанные шаги.", parent=self)

        def done(got):
            from ..sources import _path as PT
            from .dialog_path import PathDialog
            clicks = got["clicks"]
            if got["download"] and clicks and clicks[-1]["href"] != got["download"] and \
                    not PT.looks_like_file(clicks[-1]["href"]):
                clicks.append({"href": got["download"], "text": "", "near": "", "page": clicks[-1]["href"]})
            if not clicks:
                messagebox.showinfo("Путь к файлу", "Хаб не увидел ни одного щелчка по ссылке. Попробуйте ещё раз.",
                                    parent=self)
                return
            PathDialog(self.app, start, PT.guess_steps(clicks), clicks, on_done=self._path_loaded)
        self.app.bg(lambda: browser.record_path(start), done, "Записываю путь к файлу (окно браузера)…",
                    error=lambda e: messagebox.showerror("Путь к файлу", human_message(e), parent=self))

    def _path_loaded(self, path, data, url):
        fmt = F.detect_format(data, url)
        try:
            tables = F.read_tables(data, fmt)
        except Exception as e:  # noqa: BLE001
            tables = []
            if fmt not in ("xlsx", "xls", "csv"):
                messagebox.showerror("Файл", human_message(e), parent=self)
                return
        self.file_url = url
        self.url.set(url)
        self._show(("path", path), data, tables, fmt=fmt)
        self.info.configure(text=f"Файл найден по шагам ({len(path['steps'])}): {url}. Найдено таблиц: "
                                 f"{len(tables)}.", style="Muted.TLabel")

    def load_url(self):
        url = W.clean_url(self.url.get())
        if not url:
            return
        from ..sources import fred
        if fred.is_fred(url) and fred.ids_from_text(url):
            self.app.find.open_fred(url)          # у FRED постоянная ссылка на CSV по коду ряда — своя вкладка
            return
        self.url.set(url)

        from ..sources import Context
        ctx = Context(paths=self.app.paths, browser=self.app.browser())

        def work():
            data, final = download(url, ctx)
            return data, F.read_tables(data, F.detect_format(data, url))
        self.app.bg(work, lambda r: self._show(("url", url), *r, fmt=F.detect_format(r[0], url)), "Скачиваю файл…",
                    error=lambda e: self.info.configure(text="Не получилось: " + human_message(e), style="red.TLabel"))

    def load_inbox(self):
        path = filedialog.askopenfilename(parent=self, title="Файл с данными",
                                          filetypes=[("Таблицы", "*.xlsx *.xls *.csv *.pdf *.zip"), ("Все файлы", "*.*")])
        if not path:
            return
        data = Path(path).read_bytes()
        try:
            tables = F.read_tables(data, F.detect_format(data, path))
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Файл", human_message(e), parent=self)
            return
        self._show(("inbox", path), data, tables)

    def _show(self, source, data, tables, fmt=None):
        self.source = source
        self.tables = tables
        self.data, self.fmt = data, fmt or F.detect_format(data, source[1])
        ok = source[0] in ("url", "path") and self.fmt in ("xlsx", "xls", "csv")
        self.sheets_btn.state(["!disabled"] if ok else ["disabled"])
        self.list.delete(*self.list.get_children())
        for i, t in enumerate(tables):
            self.list.insert("", "end", iid=str(i), values=(t.title, len(t.rows)))
        if tables:
            self.list.selection_set("0")
        self.add_btn.configure(state="normal" if tables else "disabled")
        self.info.configure(text=f"Найдено таблиц: {len(tables)}. Выберите нужную слева.", style="Muted.TLabel")

    def show(self):
        sel = self.list.selection()
        if not sel:
            return
        t = self.tables[int(sel[0])]
        if self.preview is not None:
            self.preview.master.destroy()
        rows = t.preview(15)
        width = max(len(r) for r in rows) if rows else 1
        cols = [f"c{j}" for j in range(width)]
        f, self.preview = W2.tree_with_scroll(self.pf, cols, [str(j + 1) for j in range(width)], {c: 120 for c in cols})
        f.pack(fill="both", expand=True)
        for r in rows:
            self.preview.insert("", "end", values=list(r) + [""] * (width - len(r)))
        from ..sources import _dataset as D
        self.dataset = D.analyze(t)
        if self.dataset and self.dataset["dims"]:
            self.mode.set("dataset")
            dims = ", ".join(f"{d} ({len(v)})" for d, v in self.dataset["dims"].items())
            self.info.configure(text=f"Похоже на набор данных: дата — «{self.dataset['date']}», разрезы — {dims}, "
                                     f"числа — {', '.join(self.dataset['values'])}. «＋ В обновления» — выбрать "
                                     "значения и поля.", style="Muted.TLabel")
            return
        has_dates = any(p["date"] >= 0.6 for p in T.column_profile(t.rows))
        self.mode.set("history" if has_dates else "snapshot")

    def to_sheets(self):
        """▤ «Листы в модель» — без разбора в базу, листы как есть."""
        if not self.data or not self.source or self.source[0] not in ("url", "path"):
            messagebox.showinfo("Листы в модель", "Сначала откройте файл по ссылке (Excel или CSV).", parent=self)
            return
        from .dialog_sheets import SheetsDialog
        where = self.source[1]
        if self.source[0] == "path":
            src, where = {"path": self.source[1]}, self.file_url or self.source[1]["start"]
        elif self.page_url.get().strip() and self.pattern.get().strip():
            src = {"page_url": W.clean_url(self.page_url.get()), "link_pattern": self.pattern.get().strip()}
        else:
            src = {"url": where}
        try:
            SheetsDialog(self.app, src, self.data, self.fmt, W.site_name(where))
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Листы в модель", human_message(e), parent=self)

    def add(self):
        sel = self.list.selection()
        if not sel or not self.source:
            return
        t = self.tables[int(sel[0])]
        spec = {"sheet": t.title} if t.kind == "file" and not getattr(t, "page", None) else {"page": getattr(t, "page", None),
                                                                                          "index": t.index}
        params = {"table": spec, "mode": self.mode.get(), "periodicity": self.per.get()}
        if self.mode.get() == "dataset":
            from ..sources import _dataset as D
            from .dialog_dataset import DatasetDialog
            info = D.analyze(t)
            if not info:
                messagebox.showwarning("Набор данных", "В этой таблице не нашлось столбца с датами и столбцов с числами. "
                                       "Выберите «История» или «Снимок».", parent=self)
                return
            DatasetDialog(self.app, t, info, lambda p: self._add(t, {"table": spec, **p}))
            return
        self._add(t, params)

    def _add(self, t, params):
        kind, where = self.source
        if kind == "path":
            params["path"] = where
            where = self.file_url or where["start"]
            kind = "url"
        elif kind == "url":
            if self.page_url.get().strip() and self.pattern.get().strip():
                params.update(page_url=W.clean_url(self.page_url.get()), link_pattern=self.pattern.get().strip())
            else:
                params["url"] = where
        if kind == "url":
            title = t.title
            if params.get("mode") == "dataset" or title == "CSV" or getattr(t, "archive", False):
                title = F.name_from_url(where) or title       # у CSV нет листов — название по адресу
            name = f"{W.site_name(where).capitalize()}: {title}"
            AddDialog(self.app, "file_link", params, name,
                      note="При каждом обновлении хаб скачивает файл заново и берёт из него новые и уточнённые "
                           "периоды." if params.get("mode") == "dataset" else "")
        else:
            src = Path(where)
            folder = slug(src.stem, 24)
            params["folder"] = folder
            dst = self.app.paths.inbox / folder
            dst.mkdir(parents=True, exist_ok=True)
            if src.parent != dst:
                shutil.copy2(src, dst / src.name)
            AddDialog(self.app, "folder_inbox", params, f"{src.stem}: {t.title}",
                      note=f"Новые выгрузки кладите в папку «Входящие\\{folder}» — хаб подхватит их сам и "
                           f"объединит с историей.")
