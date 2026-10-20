"""
Вкладка выбора значений одного измерения — общая для навигаторов ЕМИСС и Банка России,
чтобы подбор данных выглядел и работал одинаково: фильтр списка, отметка значений,
«Выбрать видимые» / «Снять видимые», для годов — «Последние 5 / 10», счётчик «(отмечено/всего)».
"""
import tkinter as tk
from tkinter import ttk


def add_dim_tab(nb, title, items, on_change, years=False, before=None, note=""):
    """items — [(id, текст)]. Возвращает (chosen, all_ids, tab, title); по умолчанию отмечено всё.

    before — вкладка, перед которой вставить новую (например, «Данные»)."""
    tab = ttk.Frame(nb, padding=4)
    if before is not None and str(before) in nb.tabs():
        nb.insert(before, tab, text=title[:30])
    else:
        nb.add(tab, text=title[:30])
    top = ttk.Frame(tab)
    top.pack(fill="x")
    flt = tk.StringVar()
    ttk.Entry(top, textvariable=flt, width=30).pack(side="left")
    ttk.Label(top, text="← фильтр списка", foreground="#777").pack(side="left", padx=4)
    lb_frame = ttk.Frame(tab)
    lb_frame.pack(fill="both", expand=True, pady=4)
    lb = tk.Listbox(lb_frame, selectmode="multiple", activestyle="none", exportselection=False)
    sb = ttk.Scrollbar(lb_frame, command=lb.yview)
    lb.configure(yscrollcommand=sb.set)
    lb.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")

    names = {k: str(v).strip() for k, v in items}
    all_ids = [k for k, _ in items]
    chosen = set(all_ids)       # по умолчанию выбрано всё (как на сайте ЕМИСС)
    shown = []

    def redraw(*_a):
        shown.clear()
        lb.delete(0, "end")
        s = flt.get().strip().lower()
        for vid in all_ids:
            t = names[vid]
            if s in t.lower():
                shown.append(vid)
                lb.insert("end", t)
                if vid in chosen:
                    lb.selection_set("end")
        on_change()

    def on_sel(_e):
        cur = set(lb.curselection())
        for i, vid in enumerate(shown):
            (chosen.add if i in cur else chosen.discard)(vid)
        on_change()

    def set_all(flag):
        for vid in shown:
            (chosen.add if flag else chosen.discard)(vid)
        redraw()

    btns = ttk.Frame(tab)
    btns.pack(fill="x")
    ttk.Button(btns, text="Выбрать видимые", command=lambda: set_all(True)).pack(side="left")
    ttk.Button(btns, text="Снять видимые", command=lambda: set_all(False)).pack(side="left", padx=4)
    if years:   # годы: быстрый выбор последних лет
        for n in (5, 10):
            ttk.Button(btns, text=f"Последние {n}",
                       command=lambda n=n: (chosen.clear(), chosen.update(all_ids[-n:]), redraw())
                       ).pack(side="left", padx=2)
    ttk.Label(btns, text=f"всего значений: {len(all_ids)}", foreground="#777").pack(side="right")
    if note:
        ttk.Label(tab, text=note, foreground="#777", wraplength=560, justify="left").pack(anchor="w", pady=(4, 0))

    anchor = {"i": None}

    def click(e):
        anchor["i"] = lb.nearest(e.y)

    def shift_click(e):
        """Shift+щелчок — как в Windows: всё от прошлого щелчка до этого становится как прошлое значение."""
        i = lb.nearest(e.y)
        a = anchor["i"] if anchor["i"] is not None and anchor["i"] < lb.size() else i
        on = lb.selection_includes(a) if a != i else True
        lo, hi = sorted((a, i))
        (lb.selection_set if on else lb.selection_clear)(lo, hi)
        anchor["i"] = i
        on_sel(None)
        return "break"

    flt.trace_add("write", redraw)
    lb.bind("<<ListboxSelect>>", on_sel)
    lb.bind("<Button-1>", click)
    lb.bind("<Shift-Button-1>", shift_click)
    entry = (chosen, all_ids, tab, title)
    tab.after_idle(redraw)
    return entry


def chosen_years(entry):
    """Отмеченные годы числами (по возрастанию)."""
    chosen, all_ids, _t, _n = entry
    return sorted(int(str(v)[:4]) for v in all_ids if v in chosen and str(v)[:4].isdigit())
