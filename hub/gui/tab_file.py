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
        self.info = ttk.Label(self, style="Muted.TLabel", text="Поддерживаются Excel (xlsx, xls), CSV и PDF с таблицами.")
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
        ttk.Label(bar, text="Периодичность:").pack(side="left", padx=(16, 4))
        self.per = tk.StringVar(value="month")
        ttk.Combobox(bar, textvariable=self.per, values=["day", "week", "month", "quarter", "year"], width=9,
                     state="readonly").pack(side="left")
        self.add_btn = ttk.Button(bar, text="＋ В обновления", style="Add.TButton", command=self.add, state="disabled")
        self.add_btn.pack(side="right")

    def load_url(self):
        url = W.clean_url(self.url.get())
        if not url:
            return
        self.url.set(url)

        def work():
            data, final = download(url, None)
            return data, F.read_tables(data, F.detect_format(data, url))
        self.app.bg(work, lambda r: self._show(("url", url), *r), "Скачиваю файл…",
                    error=lambda e: self.info.configure(text="Не получилось: " + human_message(e), style="red.TLabel"))

    def load_inbox(self):
        path = filedialog.askopenfilename(parent=self, title="Файл с данными",
                                          filetypes=[("Таблицы", "*.xlsx *.xls *.csv *.pdf"), ("Все файлы", "*.*")])
        if not path:
            return
        data = Path(path).read_bytes()
        try:
            tables = F.read_tables(data, F.detect_format(data, path))
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Файл", human_message(e), parent=self)
            return
        self._show(("inbox", path), data, tables)

    def _show(self, source, data, tables):
        self.source = source
        self.tables = tables
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
        has_dates = any(p["date"] >= 0.6 for p in T.column_profile(t.rows))
        self.mode.set("history" if has_dates else "snapshot")

    def add(self):
        sel = self.list.selection()
        if not sel or not self.source:
            return
        t = self.tables[int(sel[0])]
        spec = {"sheet": t.title} if t.kind == "file" and not getattr(t, "page", None) else {"page": getattr(t, "page", None),
                                                                                          "index": t.index}
        params = {"table": spec, "mode": self.mode.get(), "periodicity": self.per.get()}
        kind, where = self.source
        if kind == "url":
            if self.page_url.get().strip() and self.pattern.get().strip():
                params.update(page_url=W.clean_url(self.page_url.get()), link_pattern=self.pattern.get().strip())
            else:
                params["url"] = where
            name = f"{W.site_name(where).capitalize()}: {t.title}"
            AddDialog(self.app, "file_link", params, name)
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
