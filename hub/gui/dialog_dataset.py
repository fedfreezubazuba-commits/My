"""Окно «Набор данных»: какие значения разрезов и какие числа брать из файла — как в навигаторе ЕМИСС."""
import tkinter as tk
from tkinter import messagebox, ttk

from ..core.periods import PERIODICITY_RU
from ..sources import _dataset as D
from . import widgets as W
from .dimlist import add_dim_tab

PER_CHOICES = [("auto", "как в файле"), ("day", "дневная"), ("week", "недельная"), ("month", "месячная"),
               ("quarter", "квартальная"), ("year", "годовая")]


class DatasetDialog(tk.Toplevel):
    def __init__(self, app, table, info, finish):
        """finish(params) — что делать с готовыми параметрами (открыть «＋ В обновления»)."""
        super().__init__(app.root)
        self.app, self.table, self.info, self.finish = app, table, info, finish
        self.title("Набор данных: поля и отбор")
        self.geometry("900x620")
        self.transient(app.root)
        bar = ttk.Frame(self, padding=12)
        bar.pack(side="bottom", fill="x")
        body = ttk.Frame(self, padding=(12, 10, 12, 0))
        body.pack(fill="both", expand=True)
        per = PERIODICITY_RU.get(info["periodicity"], info["periodicity"])
        span = (f", с {info['first']:%d.%m.%Y} по {info['last']:%d.%m.%Y}" if info.get("first") else "")
        W.autowrap(ttk.Label(body, justify="left", text=(
            f"В файле — набор данных: в строках дата («{info['date']}»), разрезы и числа; периодичность — {per}{span}. "
            "Отметьте нужные значения разрезов (по умолчанию — всё) и числа. Каждое сочетание станет рядом. "
            "При каждом обновлении хаб скачивает файл заново и берёт из него новые периоды; новые значения разреза, "
            "где отмечено всё, подхватываются сами."))).pack(fill="x")
        self.nb = ttk.Notebook(body)
        self.nb.pack(fill="both", expand=True, pady=(8, 0))
        self.lists = {}
        for dim, values in info["dims"].items():
            self.lists[dim] = add_dim_tab(self.nb, dim, [(v, v) for v in values], self._count)
        self.lists["__values"] = add_dim_tab(self.nb, "Числа", [(v, v) for v in info["values"]], self._count,
                                             note="Столбцы с числами — каждый станет отдельным рядом.")
        row = ttk.Frame(body)
        row.pack(fill="x", pady=(8, 0))
        ttk.Label(row, text="Периодичность:").pack(side="left")
        self.per = tk.StringVar(value=PER_CHOICES[0][1])
        ttk.Combobox(row, textvariable=self.per, values=[t for _k, t in PER_CHOICES], state="readonly",
                     width=16).pack(side="left", padx=6)
        self.count = ttk.Label(row, style="Hint.TLabel")
        self.count.pack(side="left", padx=16)
        ttk.Button(bar, text="Далее — ＋ В обновления", style="Add.TButton", command=self.done).pack(side="right")
        ttk.Button(bar, text="Отмена", command=self.destroy).pack(side="right", padx=8)
        W.center(self, app.root)
        self.after(200, self._count)

    def params(self):
        filters = {dim: [v for v in all_ids if v in chosen]
                   for dim, (chosen, all_ids, _t, _n) in self.lists.items() if dim != "__values"}
        chosen, all_ids, _t, _n = self.lists["__values"]
        values = [v for v in all_ids if v in chosen]
        per = next((k for k, t in PER_CHOICES if t == self.per.get()), "auto")
        return D.params_for(self.info, filters, values, per)

    def _count(self):
        if not hasattr(self, "count"):
            return
        p = self.params()
        empty = [d for d, v in p["filters"].items() if not v] + ([] if p["values"] else ["числа"])
        if empty:
            self.count.configure(text="Ничего не отмечено: " + ", ".join(empty), style="red.TLabel")
            return
        try:
            n = D.count_series(self.table, p)
        except Exception:  # noqa: BLE001
            n = 0
        self.count.configure(text=f"Рядов будет: {n:,}".replace(",", " "), style="Hint.TLabel")

    def done(self):
        p = self.params()
        if not p["values"] or any(not v for v in p["filters"].values()):
            messagebox.showwarning("Набор данных", "В каждом разрезе и в «Числах» отметьте хотя бы одно значение.",
                                   parent=self)
            return
        self.destroy()
        self.finish(p)
