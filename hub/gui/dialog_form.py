"""Окно «Данные из формы на сайте»: что хаб записал (таблица ответа, поля запроса) и с какого года грузить."""
import datetime as dt
import tkinter as tk
from tkinter import messagebox, ttk
from urllib.parse import urlparse

from ..sources import _forms as F
from ..sources import web_form as WF
from . import widgets as W
from .dialog_add import AddDialog

# подсказки к полям известных сайтов (что пользователь выбрал в форме)
FIELD_HINTS = {
    "iEType": "импорт / экспорт — как выбрано в форме", "currencyType": "валюта (usd — доллары, rmb — юани)",
    "outerValue1": "код товара (ТН ВЭД) — как в форме", "outerValue2": "код страны-партнёра — как в форме",
    "outerValue3": "режим торговли — как в форме", "outerValue4": "провинция — как в форме",
    "monthFlag": "разбивка по месяцам", "codeLength": "длина кода товара (2, 4, 6, 8 знаков)",
}


def job_name(url, table) -> str:
    lay = F.layout(table)
    first = lay["rows"][0] if lay["rows"] else []
    words = [first[j] for j in lay["labels"] if j < len(first) and first[j]
             and not F.CODE_HEAD.search(lay["head"][j]) and not first[j].isdigit()]
    host = urlparse(url).netloc
    return f"{WF.TITLES.get(host, host)}: {', '.join(words[:3]) or table.title or 'данные формы'}"[:150]


class FormDialog(tk.Toplevel):
    def __init__(self, app, start_url, cand, table, job=None):
        """job — уже добавленный показатель («Изменить набор данных»): тогда без превью ответа, «Сохранить»."""
        super().__init__(app.root)
        self.app, self.start_url, self.table, self.job = app, start_url, table, job
        self.cand = {k: v for k, v in cand.items() if k != "text"}     # ответ сайта в настройках не храним
        old = (job or {}).get("params", {}).get("values") or {}
        cand = with_values = F.with_fields(self.cand, old) if old else self.cand
        self.title("Данные страницы")
        self.geometry("980x720")
        self.transient(app.root)
        self.flds = F.fields(with_values)
        self.base = {n: v for _w, n, v in F.fields(self.cand)}
        self.period = (job or {}).get("params", {}).get("period") or F.detect_period(self.flds)
        roles = F.roles(self.flds, self.period)
        lay = F.layout(table) if table is not None else None

        bar = ttk.Frame(self, padding=(14, 0, 14, 14))
        bar.pack(side="bottom", fill="x")           # кнопки внизу видны при любой высоте окна
        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        W.autowrap(ttk.Label(body, justify="left", text=(
            "Хаб запомнил запрос, который отправила форма, и ответ сайта. Дальше он будет повторять этот запрос "
            "сам — по году (или по месяцу), с того года, который вы укажете ниже. Поля периода хаб подставляет "
            "сам; остальные поля — как вы выбрали в форме (их можно поменять здесь)."))).pack(fill="x")

        if lay is not None:
            ttk.Label(body, text="Ответ сайта (первые строки)", style="Title.TLabel").pack(anchor="w", pady=(10, 2))
            head = lay["head"]
            cols = [f"c{j}" for j in range(len(head))]
            f, tree = W.tree_with_scroll(body, cols, head, {c: 110 for c in cols}, height=6)
            f.pack(fill="x")
            for r in lay["rows"][:8]:
                tree.insert("", "end", values=r)
            what = ", ".join(head[j] for j in lay["values"]) or "—"
            when = (f"месяц — столбец «{head[lay['date']]}»" if lay["date"] is not None
                    else "в ответе нет столбца с датой — хаб будет спрашивать каждый месяц отдельно")
            ttk.Label(body, style="Hint.TLabel", text=f"Ряды: {what}. Дата: {when}.").pack(anchor="w")

        ttk.Label(body, text="Поля запроса", style="Title.TLabel").pack(anchor="w", pady=(10, 2))
        sf = W.ScrollFrame(body)
        sf.canvas.configure(height=150)            # список полей прокручивается; год и кнопки всегда видны
        sf.pack(fill="both", expand=True)
        grid = sf.inner
        grid.columnconfigure(2, weight=1)
        self.vars, self._keep = {}, []
        for i, (_where, name, value) in enumerate(self.flds):
            ttk.Label(grid, text=name).grid(row=i, column=0, sticky="w", padx=(0, 8))
            var = tk.StringVar(value=value)
            self._keep.append(var)                 # иначе переменную соберёт сборщик мусора и поле опустеет
            e = ttk.Entry(grid, textvariable=var, width=28)
            e.grid(row=i, column=1, sticky="w", pady=1)
            role = roles.get(name)
            if role:
                e.state(["readonly"])
            else:
                self.vars[name] = var
            ttk.Label(grid, text=role or FIELD_HINTS.get(name, ""), style="Hint.TLabel").grid(
                row=i, column=2, sticky="w", padx=8)

        opts = ttk.Frame(body)
        opts.pack(fill="x", pady=(10, 0))
        this_year = dt.date.today().year
        rec_year = next((int(v) for _w, n, v in self.flds if self.period and n == self.period.get("year")
                         and str(v).isdigit()), this_year)
        jp = (job or {}).get("params", {})
        self.from_year = tk.StringVar(value=str(jp.get("from_year") or min(rec_year, this_year - 5)))
        # по месяцу — мелкие запросы: сайты реже предупреждают о «большом периоде» и реже просят проверку
        months_in_query = (self.period or {}).get("kind") == "year_months" and (self.period.get("start")
                                                                                 or self.period.get("end"))
        self.by = tk.StringVar(value=jp.get("by") or ("month" if months_in_query or lay is not None and lay["date"] is None
                                                       else "year"))
        if self.period:
            ttk.Label(opts, text="Загружать с года:").pack(side="left")
            ttk.Spinbox(opts, from_=1990, to=this_year, textvariable=self.from_year, width=6,
                        command=self._count).pack(side="left", padx=6)
            ttk.Radiobutton(opts, text="запрос на месяц (бережнее к сайту)", value="month", variable=self.by,
                            command=self._count).pack(side="left", padx=(16, 0))
            ttk.Radiobutton(opts, text="запрос на год (быстрее)", value="year", variable=self.by,
                            command=self._count).pack(side="left", padx=6)
        else:
            W.autowrap(ttk.Label(opts, justify="left", text=(
                "Период в запросе не задаётся — при каждом обновлении хаб откроет эту страницу (окно за краем экрана) "
                "и возьмёт те же данные, что получает она сама; обычно это вся история."))).pack(side="left", fill="x",
                                                                                                expand=True)
        self.count = W.autowrap(ttk.Label(body, style="Hint.TLabel", justify="left"))
        self.count.pack(fill="x", pady=(4, 0))
        self.from_year.trace_add("write", lambda *_: self._count())
        self._count()

        ttk.Button(bar, text="Сохранить" if job else "Далее — ＋ В обновления", style="Add.TButton",
                   command=self.save if job else self.next).pack(side="right")
        ttk.Button(bar, text="Отмена", command=self.destroy).pack(side="right", padx=8)
        W.center(self, app.root)

    def _year(self):
        try:
            return max(1990, min(int(self.from_year.get()), dt.date.today().year))
        except ValueError:
            return None

    def _count(self):
        y = self._year()
        if not self.period or y is None:
            self.count.configure(text="")
            return
        n = len(F.chunks(self.period, y, dt.date.today(), self.by.get()))
        from ..core.overview import plural
        days = self.app.cfg.full_reload_days(self.job)
        fresh = ("последние дни (с последней загруженной даты минус неделю)" if self.period.get("kind") == "dates"
                 else "последние месяцы")
        full = f"всю историю — раз в {days} дн." if days else "всю историю заново не берёт (так задано в настройках)"
        self.count.configure(text=f"Первая загрузка — {n} {plural(n, 'запрос', 'запроса', 'запросов')} к сайту (с паузой {WF.PAUSE_S} с между ними). "
                                  f"Дальше хаб спрашивает только {fresh}, а {full}.")

    def params(self):
        values = {n: var.get() for n, var in self.vars.items() if var.get() != self.base.get(n)}
        if self.job:
            return {**self.job["params"], "from_year": self._year(), "by": self.by.get(), "values": values}
        return {"url": self.start_url, "request": self.cand, "period": self.period, "from_year": self._year(),
                "by": self.by.get(), "values": values, "periodicity": "auto",
                "table": {"title": self.table.title, "index": self.table.index},
                "code_prefix": WF.prefix_for(self.cand["url"]), "fetch_mode": "browser"}

    def save(self):
        """«Изменить набор данных»: тот же показатель, другие поля; при следующем обновлении — вся история."""
        if self.period and self._year() is None:
            messagebox.showwarning("Год", "Укажите год числом, например 2020.", parent=self)
            return
        from ..core import impact
        self.app.cfg.update_job(self.job["id"], params=self.params())
        impact.mark_edited(self.app.store, self.job["id"], dt.datetime.now().isoformat(timespec="seconds"))
        self.destroy()
        self.app.refresh_all()
        if messagebox.askyesno("Изменить набор данных", "Сохранено. Загрузить данные сейчас?", parent=self.app.root):
            self.app.update_jobs([self.job["id"]])

    def next(self):
        if self.period and self._year() is None:
            messagebox.showwarning("Год", "Укажите год числом, например 2020.", parent=self)
            return
        params = self.params()
        name = job_name(self.cand["url"], self.table)
        self.destroy()
        AddDialog(self.app, "web_form", params, name,
                  note="Сайт с формой читается встроенным браузером (окно за краем экрана), по одному запросу с "
                       "паузой. Первая загрузка может занять несколько минут.")
