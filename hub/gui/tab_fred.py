"""Вкладка «FRED (США)»: ряд базы ФРС Сент-Луиса по ссылке на страницу ряда или по коду — вся история CSV."""
import tkinter as tk
from tkinter import messagebox, ttk

from ..core.base_fmt import fmt
from ..sources import Context, get
from ..sources import fred as FR
from . import widgets as W
from .dialog_add import AddDialog


class FredTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
        self.meta = {}
        W.autowrap(ttk.Label(self, justify="left", style="Muted.TLabel", text=(
            "FRED — база данных ФРС Сент-Луиса: ставки, инфляция, цены на сырьё (МВФ) и сотни тысяч других рядов. "
            "Вставьте ссылку на страницу ряда (например, https://fred.stlouisfed.org/series/PFISHUSDM — мировая цена "
            "рыбной муки) или его код. Можно несколько через запятую. Ссылку «Download → CSV» копировать не нужно: "
            "хаб сам берёт CSV по коду, всю историю, без дат в ссылке. Регистрация и ключ не нужны."))).pack(fill="x")
        r = ttk.Frame(self)
        r.pack(fill="x", pady=8)
        ttk.Label(r, text="Ссылка или код:").pack(side="left")
        self.text = tk.StringVar()
        e = ttk.Entry(r, textvariable=self.text, font=(W.FONT, 11))
        e.pack(side="left", fill="x", expand=True, padx=6)
        e.bind("<Return>", lambda _e: self.preview())
        ttk.Button(r, text="Показать", command=self.preview).pack(side="left")
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        self.add_btn = ttk.Button(bar, text="＋ В обновления", style="Add.TButton", command=self.add, state="disabled")
        self.add_btn.pack(side="right")
        self.info = W.autowrap(ttk.Label(self, justify="left", style="Muted.TLabel"))
        self.info.pack(fill="x", pady=6)
        f, self.tree = W.tree_with_scroll(self, ("date",), ("Период",), {"date": 110}, height=12)
        self.tree_frame = f
        f.pack(fill="both", expand=True)
        self.ids = []

    def show_link(self, text):
        """Ссылку FRED вставили в «Сайт» или «Файл» — показать её здесь."""
        self.text.set(text)
        self.preview()

    def preview(self):
        ids = FR.ids_from_text(self.text.get())
        if not ids:
            messagebox.showwarning("FRED", "Не вижу кода ряда. Вставьте ссылку вида "
                                   "https://fred.stlouisfed.org/series/PFISHUSDM или код PFISHUSDM.", parent=self)
            return
        self.add_btn.state(["disabled"])

        def work():
            ctx = Context(paths=self.app.paths, browser=self.app.browser())
            FR.get(FR.csv_url(ids[0]), ctx)       # сначала данные: так хаб узнаёт, как FRED сейчас отвечает
            metas = {sid: FR.fetch_meta(sid, ctx) for sid in ids}
            res = get("fred").fetch({"ids": ids, "meta": metas}, ctx)
            return metas, res

        def done(r):
            metas, res = r
            self.ids, self.meta = ids, metas
            cols = ["date"] + [f"s{i}" for i in range(len(ids))]
            heads = ["Период"] + [f"{s.name[:40]}{', ' + s.unit[:30] if s.unit else ''}" for s in res.series]
            self.tree.configure(columns=cols)
            for c, h in zip(cols, heads):
                self.tree.heading(c, text=h)
                self.tree.column(c, width=110 if c == "date" else 260, anchor="w" if c == "date" else "e")
            self.tree.delete(*self.tree.get_children())
            keys = [s.key for s in res.series]
            by = {}
            for o in res.obs:
                by.setdefault((o.period_end, o.period_label), {})[o.key] = o.value
            for (d, lab) in sorted(by, reverse=True)[:60]:
                self.tree.insert("", "end", values=[lab] + [fmt(by[(d, lab)].get(k)) for k in keys])
            no_meta = [s for s in ids if not metas.get(s)]
            text = res.describe() + "."
            if no_meta:
                text += (" Название и единицы ряда " + ", ".join(no_meta) + " со страницы FRED взять не удалось — "
                         "ряд будет называться кодом, единицы можно не указывать.")
            self.info.configure(text=text, style="green.TLabel" if res.obs else "yellow.TLabel")
            if res.obs:
                self.add_btn.state(["!disabled"])
        self.info.configure(text="Спрашиваю FRED…", style="Muted.TLabel")
        self.app.bg(work, done, "Запрос к FRED…", error=self._failed)

    def _failed(self, exc):
        from ..core import errors as E
        detail = getattr(exc, "detail", "") or ""
        self.info.configure(text=f"Не получилось: {E.human_message(exc)}" + (
            f"\nЧто пробовал хаб: {detail[:600]}" if detail else ""), style="red.TLabel")

    def add(self):
        if not self.ids:
            return
        params = {"ids": list(self.ids), "meta": {s: self.meta.get(s) or {} for s in self.ids}}
        name = get("fred").describe(params)[:150]
        AddDialog(self.app, "fred", params, name,
                  note="При каждом обновлении хаб берёт у FRED всю историю ряда (CSV) — уточнения прошлых месяцев "
                       "тоже попадут в хаб.")
