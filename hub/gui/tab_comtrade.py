"""Вкладка «ООН (Comtrade)»: внешняя торговля по товару и паре стран, по месяцам — без формы и без проверок."""
import datetime as dt
import re
import tkinter as tk
from tkinter import messagebox, ttk

from ..core.base_fmt import fmt
from ..sources import Context, get
from ..sources import comtrade as CT
from . import widgets as W
from .dialog_add import AddDialog

CHOICES = [f"{name} ({code})" for code, name in CT.COUNTRIES]


def code_of(text):
    """«Китай (156)» → 156; «156» → 156; иначе None."""
    m = re.search(r"\((\d+)\)\s*$", text or "") or re.fullmatch(r"\s*(\d+)\s*", text or "")
    return int(m.group(1)) if m else None


class ComtradeTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
        W.autowrap(ttk.Label(self, justify="left", style="Muted.TLabel", text=(
            "База ООН UN Comtrade: импорт и экспорт стран по товарам и странам-партнёрам, по месяцам, из отчётов самих "
            "стран (Китай отчитывается данными своей таможни). Открытый доступ без регистрации. Код товара — 6 знаков "
            "(сжиженный природный газ — 271111). Данные появляются позже, чем на сайте таможни страны."))).pack(fill="x")
        g = ttk.Frame(self)
        g.pack(fill="x", pady=8)
        self.reporter = tk.StringVar(value="Китай (156)")
        self.partner = tk.StringVar(value="Россия (643)")
        self.flow = tk.StringVar(value="M")
        self.cmd = tk.StringVar(value="271111")
        self.from_year = tk.StringVar(value="2017")
        rows = (("Чья статистика (страна):", ttk.Combobox(g, textvariable=self.reporter, values=CHOICES, width=28)),
                ("Страна-партнёр:", ttk.Combobox(g, textvariable=self.partner, values=CHOICES, width=28)),
                ("Код товара (2, 4 или 6 знаков):", ttk.Entry(g, textvariable=self.cmd, width=12)),
                ("Загружать с года:", ttk.Spinbox(g, from_=2000, to=dt.date.today().year, textvariable=self.from_year,
                                                  width=6)))
        for i, (label, w) in enumerate(rows):
            ttk.Label(g, text=label).grid(row=i, column=0, sticky="w", pady=2, padx=(0, 8))
            w.grid(row=i, column=1, sticky="w", pady=2)
        fl = ttk.Frame(g)
        fl.grid(row=0, column=2, rowspan=2, sticky="w", padx=24)
        ttk.Radiobutton(fl, text="импорт", value="M", variable=self.flow).pack(anchor="w")
        ttk.Radiobutton(fl, text="экспорт", value="X", variable=self.flow).pack(anchor="w")
        ttk.Label(g, style="Hint.TLabel", text="Страны нет в списке — впишите её код ООН (например, 699 — Индия).").grid(
            row=4, column=0, columnspan=3, sticky="w")
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        ttk.Button(bar, text="Показать последние месяцы", command=self.preview).pack(side="left")
        self.add_btn = ttk.Button(bar, text="＋ В обновления", style="Add.TButton", command=self.add)
        self.add_btn.pack(side="right")
        self.info = W.autowrap(ttk.Label(self, justify="left", style="Muted.TLabel"))
        self.info.pack(fill="x", pady=6)
        f, self.tree = W.tree_with_scroll(self, ("month", "kg", "usd", "price"),
                                          ("Месяц", "Вес нетто, кг", "Стоимость, USD", "Средняя цена, USD/т"),
                                          {"month": 100, "kg": 160, "usd": 160, "price": 140}, height=12)
        f.pack(fill="both", expand=True)

    def params(self):
        rep, par = code_of(self.reporter.get()), code_of(self.partner.get())
        cmd = re.sub(r"\D", "", self.cmd.get())
        try:
            year = int(self.from_year.get())
        except ValueError:
            year = None
        problems = [t for t, ok in (("страну отчёта", rep is not None), ("страну-партнёра", par is not None),
                                    ("код товара (2, 4 или 6 цифр)", len(cmd) in (2, 4, 6)), ("год", year)) if not ok]
        if problems:
            messagebox.showwarning("UN Comtrade", "Проверьте: " + ", ".join(problems) + ".", parent=self)
            return None
        return {"reporter": rep, "partner": par, "flow": self.flow.get(), "cmd": cmd, "from_year": year}

    def preview(self):
        p = self.params()
        if not p:
            return
        today = dt.date.today()
        p_short = dict(p, from_year=today.year - 1)

        def work():
            return get("comtrade").fetch(p_short, Context(paths=self.app.paths, now=dt.datetime.now()))

        def done(res):
            self.tree.delete(*self.tree.get_children())
            by = {}
            for o in res.obs:
                by.setdefault(o.period_end, {})[o.key.split("|")[-1]] = o.value
            for d in sorted(by, reverse=True):
                v = by[d]
                self.tree.insert("", "end", values=(f"{d:%Y-%m}", fmt(v.get("netWgt")), fmt(v.get("primaryValue")),
                                                    fmt(round(v["_price"], 1)) if v.get("_price") else "—"))
            self.info.configure(text=(f"Получено месяцев: {len(by)}. Последний — {max(by):%m.%Y}." if by else
                                      "UN Comtrade не вернул данных за последние два года — проверьте коды или "
                                      "попробуйте другой товар/страну."),
                                style="green.TLabel" if by else "yellow.TLabel")
        self.info.configure(text="Спрашиваю UN Comtrade…", style="Muted.TLabel")
        self.app.bg(work, done, "Запрос к UN Comtrade…", error=self._failed)

    def _failed(self, exc):
        from ..core import errors as E
        self.info.configure(text=f"Не получилось: {E.human_message(exc)}", style="red.TLabel")

    def add(self):
        p = self.params()
        if not p:
            return
        name = (f"UN Comtrade: {CT.country(p['reporter'])}, {CT.FLOWS[p['flow']]} {p['cmd']} — "
                f"{CT.country(p['partner'])}")
        n = len(CT.month_chunks(p["from_year"], dt.date.today()))
        AddDialog(self.app, "comtrade", p, name,
                  note=f"Первая загрузка — {n} запрос(ов) к UN Comtrade (по году). Ряды: вес нетто, стоимость и "
                       "средняя цена (стоимость ÷ вес). Страны уточняют последние месяцы — хаб берёт их заново.")
