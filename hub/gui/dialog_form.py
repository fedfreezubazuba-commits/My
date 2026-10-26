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


def job_name(url, table, over=None) -> str:
    lay = F.layout(table, over)
    first = lay["rows"][0] if lay["rows"] else []
    words = [first[j] for j in lay["labels"] if j < len(first) and first[j]
             and not F.CODE_HEAD.search(lay["head"][j]) and not first[j].isdigit()]
    if not words:                       # подписей нет — по названиям рядов («Ставка по депозитам»)
        names = (over or {}).get("names") or {}
        words = [names.get(lay["head"][j]) for j in lay["values"] if names.get(lay["head"][j])]
    host = urlparse(url).netloc
    return f"{WF.TITLES.get(host, host)}: {', '.join(words[:3]) or table.title or 'данные формы'}"[:150]


def last_answer_table(app, job):
    """Таблица из последнего сохранённого ответа сайта (папка raw) — чтобы и у добавленного показателя можно
    было поправить, как понимать таблицу. None — ответа нет."""
    folder = app.paths.raw / job["id"]
    try:
        files = sorted(folder.glob("*.*"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    for f in files[:3]:
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        first = text.split("\n\n<!-- ", 1)[0]          # ответы на разные запросы склеены — берём первый
        t = F.result_table(first, (job.get("params") or {}).get("table"))
        if t is not None:
            return t
    return None


class VaryDialog(tk.Toplevel):
    """«Несколько значений» поля формы: какие значения перебирать (например, коды валют) — для каждого хаб
    повторит запрос за все даты, каждое значение станет своим рядом."""

    def __init__(self, parent, field, current, options, vary, on_done):
        super().__init__(parent)
        self.field, self.on_done = field, on_done
        self.title(f"Несколько значений: {field}")
        self.geometry("620x560")
        self.transient(parent)
        bar = ttk.Frame(self, padding=12)
        bar.pack(side="bottom", fill="x")
        body = ttk.Frame(self, padding=(12, 10, 12, 0))
        body.pack(fill="both", expand=True)
        opts = (options or {}).get("options") or []
        names = {str(v): t for v, t in opts}
        W.autowrap(ttk.Label(body, justify="left", text=(
            "Отметьте значения, которые нужно загружать. Для каждого хаб повторит запрос за все даты, и каждое станет "
            "своим рядом (например, курс каждой валюты)." + ("" if opts else
            " Списка значений на странице не нашлось — впишите их сами, по одному в строке; можно с названием: "
            "«R01235 = Доллар США».")))).pack(fill="x")
        r = ttk.Frame(body)
        r.pack(fill="x", pady=(8, 4))
        ttk.Label(r, text="Как назвать поле (столбец в Excel):").pack(side="left")
        self.label = tk.StringVar(value=(vary or {}).get("label") or (options or {}).get("label") or "Вариант")
        ttk.Entry(r, textvariable=self.label, width=24).pack(side="left", padx=6)
        chosen_now = [str(x[0]) for x in (vary or {}).get("values") or []] or [str(current)]
        self.entry = self.text = None
        if opts:
            from .dimlist import add_dim_tab
            nb = ttk.Notebook(body)
            nb.pack(fill="both", expand=True, pady=(4, 0))
            items = [(str(v), f"{t} ({v})" if t != str(v) else str(v)) for v, t in opts]
            self.entry = add_dim_tab(nb, "Значения", items, lambda: None)
            self.entry[0].clear()
            self.entry[0].update(v for v in chosen_now if v in names)
            self.names = names
        else:
            self.text = tk.Text(body, height=14, width=60)
            self.text.pack(fill="both", expand=True, pady=(4, 0))
            given = (vary or {}).get("values") or [[current, current]]
            self.text.insert("1.0", "\n".join(f"{v} = {t}" if str(t) != str(v) else str(v) for v, t in given))
        ttk.Button(bar, text="Готово", style="Accent.TButton", command=self.done).pack(side="right")
        ttk.Button(bar, text="Только одно значение (как в форме)", command=self.single).pack(side="right", padx=8)
        W.center(self, parent)

    def values(self):
        if self.entry is not None:
            chosen, all_ids, _t, _n = self.entry
            return [[v, self.names.get(v, v)] for v in all_ids if v in chosen]
        out = []
        for line in self.text.get("1.0", "end").splitlines():
            if not line.strip():
                continue
            v, _sep, t = line.partition("=")
            out.append([v.strip(), t.strip() or v.strip()])
        return out

    def done(self):
        vals = self.values()
        if not vals:
            messagebox.showwarning("Несколько значений", "Отметьте хотя бы одно значение.", parent=self)
            return
        self.destroy()
        self.on_done(self.field, {"field": self.field, "label": self.label.get().strip() or "Вариант", "values": vals}
                     if len(vals) > 1 else None)

    def single(self):
        self.destroy()
        self.on_done(self.field, None)


class FormDialog(tk.Toplevel):
    def __init__(self, app, start_url, cand, table, job=None, pages=None):
        """job — уже добавленный показатель («Изменить набор данных»): тогда без превью ответа, «Сохранить».
        pages — тексты записанных страниц (в них — выпадающие списки формы с вариантами значений полей)."""
        super().__init__(app.root)
        self.app, self.start_url, self.table, self.job = app, start_url, table, job
        jp0 = (job or {}).get("params", {})
        names_in_request = {n for _w, n, _v in F.fields(cand)}
        found = F.select_options(cand.get("text"), *(pages or [])) if not job else {}
        self.choices = {k: v for k, v in found.items() if k in names_in_request} or dict(jp0.get("vary_choices") or {})
        self.vary = {v["field"]: v for v in jp0.get("vary") or []}
        if job and "vary_choices" not in jp0 and start_url:
            # показатель добавлен до 0.8.8 — списков значений нет: прочитать их со страницы (без браузера, в фоне)
            def load():
                from ..sources import _web as Wb
                status, ct, data, _f = Wb.http_get(start_url, None, timeout=30)
                return F.select_options(Wb.decode(data, ct)) if status == 200 else {}
            app.bg(load, lambda found: self.choices.update(
                {k: v for k, v in (found or {}).items() if k in names_in_request}),
                "Читаю списки значений со страницы…", error=lambda _e: None)
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
        self.over = jp0.get("layout")                    # поправки «Как понимать таблицу»
        self.periodicity = jp0.get("periodicity") or "auto"
        if table is None and job:
            table = self.table = last_answer_table(app, job)     # для уже добавленного — по последнему ответу сайта
        lay = F.layout(table, self.over) if table is not None else None

        bar = ttk.Frame(self, padding=(14, 0, 14, 14))
        bar.pack(side="bottom", fill="x")           # кнопки внизу видны при любой высоте окна
        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        W.autowrap(ttk.Label(body, justify="left", text=(
            "Хаб запомнил запрос, который отправила форма, и ответ сайта. Дальше он будет повторять этот запрос "
            "сам — по году (или по месяцу), с того года, который вы укажете ниже. Поля периода хаб подставляет "
            "сам; остальные поля — как вы выбрали в форме (их можно поменять здесь)."))).pack(fill="x")

        if lay is not None:
            ttk.Label(body, text="Ответ сайта (первые строки)" if not job else "Последний ответ сайта (первые строки)",
                      style="Title.TLabel").pack(anchor="w", pady=(10, 2))
            head = lay["head"]
            cols = [f"c{j}" for j in range(len(head))]
            f, tree = W.tree_with_scroll(body, cols, head, {c: 110 for c in cols}, height=6)
            f.pack(fill="x")
            for r in lay["rows"][:8]:
                tree.insert("", "end", values=r)
            srow = ttk.Frame(body)
            srow.pack(fill="x", pady=(2, 0))
            ttk.Button(srow, text="Как понимать таблицу…", command=self.open_columns).pack(side="right", padx=(8, 0))
            self.summary = W.autowrap(ttk.Label(srow, style="Hint.TLabel", justify="left"))
            self.summary.pack(side="left", fill="x", expand=True)
            self._show_summary()

        ttk.Label(body, text="Поля запроса", style="Title.TLabel").pack(anchor="w", pady=(10, 2))
        ttk.Label(body, style="Hint.TLabel", text="«☰ Несколько…» — загружать сразу несколько значений поля "
                  "(например, несколько валют): для каждого хаб повторит запрос за все даты.").pack(anchor="w")
        sf = W.ScrollFrame(body)
        sf.canvas.configure(height=150)            # список полей прокручивается; год и кнопки всегда видны
        sf.pack(fill="both", expand=True)
        grid = sf.inner
        grid.columnconfigure(3, weight=1)
        self.vars, self._keep, self.vary_hints = {}, [], {}
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
                more = "Несколько…" + (f" ({len(self.choices[name]['options'])})" if name in self.choices else "")
                W.icon_button(grid, "☰", "Загружать несколько значений этого поля",
                              lambda n=name: self.open_vary(n), text=more).grid(row=i, column=2, sticky="w", padx=(6, 0))
            hint = ttk.Label(grid, text=role or FIELD_HINTS.get(name, ""), style="Hint.TLabel")
            hint.grid(row=i, column=3, sticky="w", padx=8)
            self.vary_hints[name] = (hint, role or FIELD_HINTS.get(name, ""))
            self._show_vary(name)

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

    def open_columns(self):
        from .dialog_columns import ColumnsDialog
        ColumnsDialog(self, self.table, self.over, self.periodicity, self._columns_done)

    def _columns_done(self, over, periodicity):
        self.over, self.periodicity = over, periodicity
        self._show_summary()

    def _show_summary(self):
        from .dialog_columns import summary
        if getattr(self, "summary", None) is not None and self.table is not None:
            text = summary(self.table, self.over, self.periodicity)
            self.summary.configure(text=text + ("  (с вашими поправками)" if self.over else ""))

    def open_vary(self, name):
        VaryDialog(self, name, self.vars[name].get(), self.choices.get(name), self.vary.get(name), self._vary_done)

    def _vary_done(self, name, vary):
        if vary:
            self.vary[name] = vary
        else:
            self.vary.pop(name, None)
        self._show_vary(name)
        self._count()

    def _show_vary(self, name):
        hint, base = self.vary_hints.get(name, (None, ""))
        if hint is None:
            return
        v = self.vary.get(name)
        if v:
            titles = [t for _v, t in v["values"]]
            hint.configure(text=f"перебирать {len(titles)}: " + ", ".join(titles[:4]) + ("…" if len(titles) > 4 else ""),
                           style="green.TLabel")
        else:
            hint.configure(text=base, style="Hint.TLabel")

    def _year(self):
        try:
            return max(1990, min(int(self.from_year.get()), dt.date.today().year))
        except ValueError:
            return None

    def _count(self):
        if not hasattr(self, "count"):
            return
        y = self._year()
        if not self.period or y is None:
            self.count.configure(text="")
            return
        n = len(F.chunks(self.period, y, dt.date.today(), self.by.get())) * len(F.vary_combos(list(self.vary.values())))
        from ..core.overview import plural
        days = self.app.cfg.full_reload_days(self.job)
        fresh = ("последние дни (с последней загруженной даты минус неделю)" if self.period.get("kind") == "dates"
                 else "последние месяцы")
        full = f"всю историю — раз в {days} дн." if days else "всю историю заново не берёт (так задано в настройках)"
        self.count.configure(text=f"Первая загрузка — {n} {plural(n, 'запрос', 'запроса', 'запросов')} к сайту (с паузой {WF.PAUSE_S} с между ними). "
                                  f"Дальше хаб спрашивает только {fresh}, а {full}.")

    def params(self):
        values = {n: var.get() for n, var in self.vars.items() if var.get() != self.base.get(n)}
        vary = [self.vary[n] for _w, n, _v in self.flds if n in self.vary]
        more = {"vary": vary, "vary_choices": self.choices, "layout": self.over, "periodicity": self.periodicity}
        if self.job:
            return {**self.job["params"], "from_year": self._year(), "by": self.by.get(), "values": values, **more}
        return {"url": self.start_url, "request": self.cand, "period": self.period, "from_year": self._year(),
                "by": self.by.get(), "values": values,
                "table": {"title": self.table.title, "index": self.table.index},
                "code_prefix": WF.prefix_for(self.cand["url"]), "fetch_mode": "browser", **more}

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
        name = job_name(self.cand["url"], self.table, self.over)
        if params["vary"]:
            titles = [t for v in params["vary"] for _x, t in v["values"]]
            name = f"{name.split(':')[0]}: {', '.join(titles[:4])}{'…' if len(titles) > 4 else ''}"[:150]
        self.destroy()
        AddDialog(self.app, "web_form", params, name,
                  note="Сайт с формой читается встроенным браузером (окно за краем экрана), по одному запросу с "
                       "паузой. Первая загрузка может занять несколько минут.")
