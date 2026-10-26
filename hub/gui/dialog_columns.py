"""Окно «Как понимать таблицу»: что хаб понял в таблице сайта (дата, числа, подписи) и как это поправить —
роль каждого столбца, название ряда, единицы, формат дат и периодичность. Внизу — что получится."""
import tkinter as tk
from tkinter import ttk

from ..core.base_fmt import fmt
from ..core.periods import PERIODICITY_RU
from ..sources import _forms as F
from . import widgets as W

PER_CHOICES = [("auto", "как поймёт хаб (по шагу дат)"), ("day", "дневная"), ("week", "недельная"),
               ("decade", "декадная"), ("month", "месячная"), ("quarter", "квартальная"), ("halfyear", "полугодовая"),
               ("year", "годовая")]


def preview(table, over, periodicity):
    """(ряды [SeriesInfo], строки [(дата, период, ряд, единицы, значение)], ошибка)."""
    from ..sources.base import FetchResult
    from ..sources.web_form import WebForm
    res = FetchResult()
    params = {"request": {"method": "GET", "url": "http://preview/"}, "layout": over, "periodicity": periodicity,
              "code_prefix": "X"}
    try:
        WebForm()._rows(table, params, None, None, res, {})
    except Exception as e:  # noqa: BLE001 — показать человеку, что не так
        return [], [], str(e)
    names = {s.key: s for s in res.series}
    rows = sorted(((o.period_end, o.period_label, names[o.key].name, names[o.key].unit, o.value) for o in res.obs),
                  key=lambda x: x[0], reverse=True)
    return res.series, rows, ""


def summary(table, over, periodicity) -> str:
    """Одна строка: какие ряды получатся, периодичность, даты."""
    series, rows, err = preview(table, over, periodicity)
    if err:
        return f"Не получается разобрать таблицу: {err}"
    if not series:
        return "В таблице не нашлось чисел — откройте «Как понимать таблицу…» и укажите, какой столбец — число."
    pers = ", ".join(sorted({PERIODICITY_RU.get(s.periodicity, s.periodicity) for s in series}))
    names = ", ".join(f"«{s.name}»" + (f" ({s.unit})" if s.unit else "") for s in series[:4])
    more = f" и ещё {len(series) - 4}" if len(series) > 4 else ""
    span = f", даты {rows[-1][0]:%d.%m.%Y} — {rows[0][0]:%d.%m.%Y}" if rows else ""
    return f"Ряды: {names}{more}; периодичность — {pers}{span}."


class ColumnsDialog(tk.Toplevel):
    def __init__(self, parent, table, over, periodicity, on_done):
        """over — текущие поправки (params.layout) или None; on_done(over, periodicity)."""
        super().__init__(parent)
        self.table, self.on_done = table, on_done
        self.title("Как понимать таблицу")
        self.geometry("980x700")
        self.transient(parent)
        over = dict(over or {})
        auto = F.layout(table, {"date_format": over.get("date_format") or "auto"})
        self.head = auto["head"]
        rows = auto["rows"]
        bar = ttk.Frame(self, padding=12)
        bar.pack(side="bottom", fill="x")
        body = ttk.Frame(self, padding=(12, 10, 12, 0))
        body.pack(fill="both", expand=True)
        W.autowrap(ttk.Label(body, justify="left", text=(
            "Так хаб понял таблицу. Если что-то не так — поправьте: какой столбец — дата, какие — числа (каждый станет "
            "рядом), какие — подписи (товар, регион…). Название ряда и единицы можно задать свои. Внизу сразу видно, "
            "что получится. Коды рядов от названий не зависят — переименовать можно в любой момент."))).pack(fill="x")

        grid = ttk.Frame(body)
        grid.pack(fill="x", pady=(8, 4))
        for c, t in enumerate(("Столбец", "Примеры", "Что это", "Название ряда", "Единицы")):
            ttk.Label(grid, text=t, style="Title.TLabel").grid(row=0, column=c, sticky="w", padx=4)
        role_text = dict(F.ROLES)
        role_of = {t: k for k, t in F.ROLES}
        cur = F.layout(table, over)
        names, units = over.get("names") or {}, over.get("units") or {}
        self.vars = {}
        for j, h in enumerate(self.head):
            r = j + 1
            role = ("date" if j == cur["date"] else "unit" if j == cur["unit"] else "value" if j in cur["values"]
                    else "label" if j in cur["labels"] else "skip")
            samples = ", ".join([x[j] for x in rows if j < len(x) and x[j]][:3])
            ttk.Label(grid, text=h[:40]).grid(row=r, column=0, sticky="w", padx=4, pady=1)
            ttk.Label(grid, text=samples[:50], style="Hint.TLabel").grid(row=r, column=1, sticky="w", padx=4)
            rv = tk.StringVar(value=role_text[role])
            cb = ttk.Combobox(grid, textvariable=rv, values=[t for _k, t in F.ROLES], state="readonly", width=18)
            cb.grid(row=r, column=2, sticky="w", padx=4)
            nv, uv = tk.StringVar(value=names.get(h, "")), tk.StringVar(value=units.get(h, ""))
            ttk.Entry(grid, textvariable=nv, width=30).grid(row=r, column=3, sticky="w", padx=4)
            ttk.Entry(grid, textvariable=uv, width=12).grid(row=r, column=4, sticky="w", padx=4)
            self.vars[h] = (rv, nv, uv, role)
            for v in (rv, nv, uv):
                v.trace_add("write", lambda *_: self.after_idle(self.refresh))
        self.role_of = role_of

        row = ttk.Frame(body)
        row.pack(fill="x", pady=(6, 0))
        ttk.Label(row, text="Даты в таблице:").pack(side="left")
        fmt_text = dict(F.DATE_FORMATS)
        self.fmt = tk.StringVar(value=fmt_text.get(over.get("date_format") or "auto"))
        ttk.Combobox(row, textvariable=self.fmt, values=[t for _k, t in F.DATE_FORMATS], state="readonly",
                     width=48).pack(side="left", padx=6)
        ttk.Label(row, text="Периодичность:").pack(side="left", padx=(12, 0))
        self.per = tk.StringVar(value=dict(PER_CHOICES).get(periodicity or "auto", PER_CHOICES[0][1]))
        ttk.Combobox(row, textvariable=self.per, values=[t for _k, t in PER_CHOICES], state="readonly",
                     width=26).pack(side="left", padx=6)
        self.fmt.trace_add("write", lambda *_: self.after_idle(self.refresh))
        self.per.trace_add("write", lambda *_: self.after_idle(self.refresh))

        ttk.Label(body, text="Что получится", style="Title.TLabel").pack(anchor="w", pady=(10, 2))
        self.result = W.autowrap(ttk.Label(body, justify="left"))
        self.result.pack(fill="x")
        f, self.tree = W.tree_with_scroll(body, ("date", "label", "name", "unit", "value"),
                                          ("Дата", "Период", "Ряд", "Единицы", "Значение"),
                                          {"date": 95, "label": 90, "name": 380, "unit": 80, "value": 110}, height=10)
        f.pack(fill="both", expand=True)

        ttk.Button(bar, text="Готово", style="Accent.TButton", command=self.done).pack(side="right")
        ttk.Button(bar, text="Отмена", command=self.destroy).pack(side="right", padx=8)
        ttk.Button(bar, text="Как понял хаб (сбросить поправки)", command=self.reset).pack(side="left")
        W.center(self, parent)
        self.refresh()

    def value(self):
        """(поправки для params.layout или None, периодичность)."""
        fmt_key = next((k for k, t in F.DATE_FORMATS if t == self.fmt.get()), "auto")
        per = next((k for k, t in PER_CHOICES if t == self.per.get()), "auto")
        columns, names, units = {}, {}, {}
        for h, (rv, nv, uv, auto_role) in self.vars.items():
            role = self.role_of.get(rv.get(), auto_role)
            if role != auto_role:
                columns[h] = role
            if nv.get().strip():
                names[h] = nv.get().strip()
            if uv.get().strip():
                units[h] = uv.get().strip()
        over = {k: v for k, v in (("columns", columns), ("names", names), ("units", units)) if v}
        if fmt_key != "auto":
            over["date_format"] = fmt_key
        return (over or None), per

    def refresh(self):
        if not self.winfo_exists():
            return
        over, per = self.value()
        series, rows, err = preview(self.table, over, per)
        self.tree.delete(*self.tree.get_children())
        for d, lab, name, unit, v in rows[:200]:
            self.tree.insert("", "end", values=(f"{d:%d.%m.%Y}", lab, name, unit, fmt(v)))
        self.result.configure(text=summary(self.table, over, per),
                              style="red.TLabel" if err or not series else "TLabel")

    def reset(self):
        self.destroy()
        self.on_done(None, "auto")

    def done(self):
        over, per = self.value()
        self.destroy()
        self.on_done(over, per)
