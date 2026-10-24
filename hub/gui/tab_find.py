"""
«Найти данные» — один общий поиск по всем источникам и по уже добавленным показателям,
ниже — вкладки источников: ЕМИСС, Банк России, Сайт, Файл.
"""
import json
import tkinter as tk
from tkinter import ttk

from . import widgets as W


def _stems(text):
    return [w[:max(4, len(w) - 2)] for w in text.lower().split()]


def search_all(app, text, limit=300):
    """[(вид, название, где, данные)] — по моим показателям, каталогу ЦБ и каталогу ЕМИСС (из кэша)."""
    stems = _stems(text)
    if not stems:
        return []
    hits = []

    def match(s):
        s = s.lower()
        return all(st in s for st in stems)

    for job in app.cfg.jobs():
        if match(job["name"] + " " + job["id"]):
            hits.append(("Мои показатели", job["name"], "уже в обновлении", ("job", job["id"])))
    for s in app.store.all_series():
        if match(s["name"] + " " + s["code"]):
            hits.append(("Мои показатели", s["name"], f"ряд {s['code']}", ("job", s["job_id"])))
    try:
        cats = json.loads((app.paths.cache / "cbr_catalog.json").read_text("utf-8"))
    except (OSError, ValueError):
        cats = None
    if cats is None:
        hits.append(("Банк России", "Каталог ещё не загружен — откройте вкладку «Банк России» ниже", "", ("cbr_load",)))
    else:
        for c in cats:
            if match(c["indicator_name"] + " " + c["category_name"]):
                hits.append(("Банк России", c["indicator_name"], c["category_name"],
                             ("cbr", c["category_id"], c["indicator_id"], c["indicator_name"])))
    try:
        fs = json.loads((app.paths.cache / "fedstat_catalog.json").read_text("utf-8"))
    except (OSError, ValueError):
        fs = None
    if fs is None:
        hits.append(("ЕМИСС", "Каталог ещё не загружен — откройте вкладку «ЕМИСС» ниже", "", ("fedstat_load",)))
    else:
        seen = set()

        def walk(nodes, path):
            for n in nodes:
                if "children" in n:
                    walk(n["children"], path + [n.get("name", "")])
                elif n.get("id") and n["id"] not in seen and not n.get("excluded") and match(n.get("name", "")):
                    seen.add(n["id"])
                    hits.append(("ЕМИСС", n["name"], path[0] if path else "", ("fedstat", n["id"], n["name"])))
        walk(fs.get("roots", []), [])
    return hits[:limit]


class FindTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Label(top, text="Найти:", style="Title.TLabel").pack(side="left")
        self.q = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.q, width=60, font=(W.FONT, 11))
        e.pack(side="left", padx=8, fill="x", expand=True)
        e.bind("<Return>", lambda _e: self.search())
        ttk.Button(top, text="Найти везде", command=self.search).pack(side="left")
        ttk.Label(self, style="Hint.TLabel",
                  text="Ищет по каталогам Банка России и ЕМИСС и по вашим показателям. Двойной щелчок — открыть.").pack(anchor="w")
        self.results_frame, self.results = W.tree_with_scroll(self, ("kind", "name", "where"),
                                                              ("Где", "Показатель", "Раздел"),
                                                              {"kind": 130, "name": 640, "where": 380}, height=6)
        self.results.bind("<Double-1>", lambda e: self.open_hit())
        self.hits = {}

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, pady=(6, 0))
        from .nav_cbr import CbrNav
        from .nav_fedstat import FedstatNav
        self.fedstat = FedstatNav(self.nb, app)
        self.cbr = CbrNav(self.nb, app)
        self.nb.add(self.fedstat, text="ЕМИСС")
        self.nb.add(self.cbr, text="Банк России")
        self.site = None
        self.file = None
        try:
            from .tab_site import SiteTab
            self.site = SiteTab(self.nb, app)
            self.nb.add(self.site, text="Сайт")
        except ImportError:
            pass
        try:
            from .tab_file import FileTab
            self.file = FileTab(self.nb, app)
            self.nb.add(self.file, text="Файл")
        except ImportError:
            pass
        from .tab_comtrade import ComtradeTab
        self.comtrade = ComtradeTab(self.nb, app)
        self.nb.add(self.comtrade, text="ООН (Comtrade)")
        from .tab_fred import FredTab
        self.fred = FredTab(self.nb, app)
        self.nb.add(self.fred, text="FRED (США)")
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self._on_source())

    def open_fred(self, link):
        """Ссылку на FRED вставили во вкладку «Сайт» или «Файл»: у FRED своя вкладка — открыть её."""
        self.nb.select(self.fred)
        self.fred.show_link(link)

    def refresh(self):
        self._on_source()

    def _on_source(self):
        # каталог грузим, только когда вкладка «Найти данные» действительно открыта
        try:
            if self.app.nb.select() != str(self):
                return
        except AttributeError:
            return
        tab = self.nb.nametowidget(self.nb.select())
        if hasattr(tab, "ensure_loaded"):
            tab.ensure_loaded()

    def search(self):
        text = self.q.get().strip()
        self.results.delete(*self.results.get_children())
        self.hits.clear()
        if not text:
            self.results_frame.pack_forget()
            return
        self.results_frame.pack(fill="x", pady=4, before=self.nb)
        hits = search_all(self.app, text)
        if not hits:
            self.results.insert("", "end", values=("", "Ничего не найдено", ""))
        for i, (kind, name, where, data) in enumerate(hits):
            iid = str(i)
            self.hits[iid] = data
            self.results.insert("", "end", iid=iid, values=(kind, name, where))

    def open_hit(self):
        sel = self.results.selection()
        if not sel or sel[0] not in self.hits:
            return
        data = self.hits[sel[0]]
        if data[0] == "job":
            self.app.show(self.app.jobs)
            self.app.jobs.select(data[1])
        elif data[0] in ("cbr", "cbr_load"):
            self.nb.select(self.cbr)
            if data[0] == "cbr":
                self.cbr.select_indicator(data[1], data[2], data[3])
        elif data[0] in ("fedstat", "fedstat_load"):
            self.nb.select(self.fedstat)
            if data[0] == "fedstat":
                self.fedstat.select_indicator(data[1], data[2])
