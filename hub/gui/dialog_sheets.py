"""Окно «Листы в модель»: какие листы файла переносить в модель как есть, под какими постоянными именами и как —
заменять или копить обновления. Рядом — лист «ячейки» для формул и пример формулы."""
import tkinter as tk
from tkinter import messagebox, ttk

from ..core import sheets as SH
from . import widgets as W
from .dialog_add import AddDialog


class SheetsDialog(tk.Toplevel):
    def __init__(self, app, source, data, fmt, site=""):
        """source — {"url": …} или {"page_url": …, "link_pattern": …}."""
        super().__init__(app.root)
        self.app, self.source, self.site = app, source, site
        self.sheets = SH.read_sheets(data, fmt)
        self.title("Листы файла в модель")
        self.geometry("1000x720")
        self.transient(app.root)
        bar = ttk.Frame(self, padding=12)
        bar.pack(side="bottom", fill="x")
        body = ttk.Frame(self, padding=(12, 10, 12, 0))
        body.pack(fill="both", expand=True)
        W.autowrap(ttk.Label(body, justify="left", text=(
            "Листы файла переносятся в модель как есть — на листы с постоянными именами (имя листа в самом файле может "
            "меняться, например дата в скобках). Хаб проверяет файл по расписанию и, если он изменился, обновляет листы "
            "в модели. На других листах модели с этими данными работают ваши формулы."))).pack(fill="x")

        ttk.Label(body, text="Листы", style="Title.TLabel").pack(anchor="w", pady=(10, 2))
        grid = ttk.Frame(body)
        grid.pack(fill="x")
        for c, t in enumerate(("Брать", "Лист в файле", "Строк", "Имя листа в модели")):
            ttk.Label(grid, text=t, style="Hint.TLabel").grid(row=0, column=c, sticky="w", padx=4)
        taken = set()
        self.rows = []
        for i, sh in enumerate(self.sheets):
            on = tk.BooleanVar(value=(i == 0))
            name = SH.default_name(sh.title, taken)
            taken.add(name)
            nv = tk.StringVar(value=name)
            ttk.Checkbutton(grid, variable=on, command=self._refresh).grid(row=i + 1, column=0, padx=4)
            lab = ttk.Label(grid, text=sh.title[:50], cursor="hand2")
            lab.grid(row=i + 1, column=1, sticky="w", padx=4)
            lab.bind("<Button-1>", lambda _e, k=i: self._preview(k))
            ttk.Label(grid, text=str(len(sh.grid))).grid(row=i + 1, column=2, sticky="w", padx=4)
            ttk.Entry(grid, textvariable=nv, width=24).grid(row=i + 1, column=3, sticky="w", padx=4, pady=1)
            nv.trace_add("write", lambda *_: self.after_idle(self._refresh))
            self.rows.append((on, nv, sh))

        ttk.Label(body, text="Как обновлять", style="Title.TLabel").pack(anchor="w", pady=(10, 2))
        self.mode = tk.StringVar(value="replace")
        for key, text in SH.MODES.items():
            ttk.Radiobutton(body, text=text, value=key, variable=self.mode, command=self._refresh).pack(anchor="w")
        self.cells = tk.BooleanVar(value=True)
        ttk.Checkbutton(body, variable=self.cells, command=self._refresh, text=(
            "Ещё лист «<имя> (ячейки)» — каждое число отдельной строкой: строка, столбец, дата обновления "
            "(для формул, которые не ломаются, когда столбцы в файле сдвигаются)")).pack(anchor="w", pady=(6, 0))
        self.formula = W.autowrap(ttk.Label(body, justify="left", style="Hint.TLabel"))
        self.formula.pack(fill="x", pady=(4, 0))

        ttk.Label(body, text="Лист файла (первые строки — щёлкните по имени листа выше)", style="Title.TLabel").pack(
            anchor="w", pady=(10, 2))
        self.pf = ttk.Frame(body)
        self.pf.pack(fill="both", expand=True)
        self.tree = None

        ttk.Button(bar, text="Далее — ＋ В обновления", style="Add.TButton", command=self.next).pack(side="right")
        ttk.Button(bar, text="Отмена", command=self.destroy).pack(side="right", padx=8)
        W.center(self, app.root)
        self._preview(0)
        self._refresh()

    def _preview(self, k):
        sh = self.sheets[k]
        if self.tree is not None:
            self.tree.master.destroy()
        width = min(max((len(r) for r in sh.grid), default=1), 14)
        cols = [f"c{j}" for j in range(width + 1)]
        f, self.tree = W.tree_with_scroll(self.pf, cols, ["Строка"] + [SH.col_letter(j + 1) for j in range(width)],
                                          {c: (60 if c == "c0" else 110) for c in cols}, height=8)
        f.pack(fill="both", expand=True)
        for i, r in enumerate(sh.grid[:40]):
            self.tree.insert("", "end", values=[i + 1] + [("" if v is None else str(v).replace("\n", " "))
                                                          for v in r[:width]])

    def chosen(self):
        return [(nv.get().strip(), sh) for on, nv, sh in self.rows if on.get()]

    def _refresh(self):
        if not self.winfo_exists():
            return
        picked = self.chosen()
        if not picked:
            self.formula.configure(text="Отметьте хотя бы один лист.")
            return
        name, sh = picked[0]
        cs = SH.cells(sh)
        cell = cs[len(cs) // 2] if cs else None
        if self.cells.get():
            self.formula.configure(text=(
                f"Пример формулы — последнее известное значение (строка «{(cell or {}).get('Путь', '…')}», столбец "
                f"«{(cell or {}).get('Заголовок', '…')}»; в кавычках можно ставить * — «август 2026*»):\n"
                + SH.example_formula(name or "Лист", cell, self.app.cfg.settings().get("formula_locale", "ru"))))
        else:
            self.formula.configure(text=(
                f"Лист «{name}» — таблица Лист_{SH.table_safe(name or 'Лист')}: столбец «Строка» — номер строки в файле, "
                "дальше A, B, C… как в файле."))

    def params(self):
        out = dict(self.source)
        out["mode"] = self.mode.get()
        out["sheets"] = [{"title": sh.title, "index": sh.index, "count": len(self.sheets), "name": name,
                          "cells": bool(self.cells.get())} for name, sh in self.chosen()]
        return out

    def next(self):
        picked = self.chosen()
        if not picked:
            messagebox.showwarning("Листы", "Отметьте хотя бы один лист.", parent=self)
            return
        names = [n for n, _s in picked]
        for n in names:
            why = SH.valid_name(n)
            if why:
                messagebox.showwarning("Имя листа", f"«{n}»: {why}.", parent=self)
                return
        if len(set(names)) != len(names):
            messagebox.showwarning("Имя листа", "Имена листов в модели должны быть разными.", parent=self)
            return
        params = self.params()
        name = f"{self.site.capitalize() + ': ' if self.site else ''}листы {', '.join(names)}"[:150]
        self.destroy()
        AddDialog(self.app, "file_sheets", params, name, note=(
            "Хаб проверяет файл по расписанию и обновляет листы, только если файл изменился. Чтобы листы появились в "
            "модели, после загрузки нажмите «Отдать в модель …» (модель должна быть закрыта в Excel)."))
