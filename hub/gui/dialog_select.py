"""
«Данные для модели» — выборка (выжимка) из загруженного показателя.

Хаб хранит показатель целиком (например, цены на сотни товаров по всем регионам за 10 лет),
а модель получает только то, что отмечено: значения полей (товар, регион, периодичность…)
и период («с 2022 года»). Новые ряды, подходящие под выборку, приходят в модель сами.
"""
import tkinter as tk
from tkinter import messagebox, ttk

from ..core.base_fmt import fmt
from ..core.store import Store
from ..excel import packet
from . import widgets as W
from .dimlist import add_dim_tab

BIG = 50          # предупреждать, если в модель уйдёт больше рядов


def parse_bound(text, end=False):
    """«2022» → 2022-01-01 (или 2022-12-31 для «по»); «2022-03» → 2022-03-01 / 2022-03-31; пусто → ""."""
    import calendar
    import re
    s = (text or "").strip()
    if not s:
        return ""
    m = re.fullmatch(r"(\d{4})(?:[-.](\d{1,2}))?(?:[-.](\d{1,2}))?", s)
    if not m:
        m2 = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", s)
        if not m2:
            raise ValueError(f"Не понял дату «{s}». Пишите 2022, 2022-03 или 01.03.2022")
        return f"{m2.group(3)}-{int(m2.group(2)):02d}-{int(m2.group(1)):02d}"
    y = int(m.group(1))
    mo = int(m.group(2) or (12 if end else 1))
    d = int(m.group(3) or (calendar.monthrange(y, mo)[1] if end else 1))
    return f"{y}-{mo:02d}-{d:02d}"


def short(bound):
    return bound[:7] if bound and bound.endswith("-01") else bound


class SelectionDialog(tk.Toplevel):
    def __init__(self, app, model_name, job_id=None, selection=None, on_done=None):
        super().__init__(app.root)
        self.app = app
        self.model_name = model_name
        self.on_done = on_done
        self.edit = selection
        self.store = Store(app.paths.db)
        self.title(f"Данные для модели «{model_name}»")
        self.geometry("1180x760")
        self.minsize(900, 600)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.fields = {}            # поле -> вкладка (chosen, all_ids, tab, title)
        self.rows = []              # [(row, fields)] рядов показателя
        self.job = None
        self.name_touched = False
        self._pending = None

        ttk.Label(self, padding=(12, 10, 12, 0), style="Muted.TLabel", wraplength=1100, justify="left", text=(
            "Хаб хранит показатель целиком, а в модель отдаёт только выжимку: отметьте нужные значения полей "
            "(товар, регион, периодичность…) и период. Каждый подходящий ряд станет столбцом таблицы «Хаб», "
            "его поля — строкой справочника «Хаб_Ряды».")).pack(fill="x")
        pw = ttk.PanedWindow(self, orient="horizontal")
        pw.pack(fill="both", expand=True, padx=10, pady=8)

        left = ttk.Frame(pw)
        pw.add(left, weight=1)
        ttk.Label(left, text="1. Показатель", style="Title.TLabel").pack(anchor="w")
        self.q = tk.StringVar()
        ttk.Entry(left, textvariable=self.q).pack(fill="x", pady=4)
        self.q.trace_add("write", lambda *_: self.fill_jobs())
        self.jobs_lb = tk.Listbox(left, activestyle="none", exportselection=False)
        self.jobs_lb.pack(fill="both", expand=True)
        self.jobs_lb.bind("<<ListboxSelect>>", lambda e: self.pick_job())
        self.job_ids = []

        right = ttk.Frame(pw)
        pw.add(right, weight=3)
        ttk.Label(right, text="2. Что отдавать в модель", style="Title.TLabel").pack(anchor="w")
        self.const = ttk.Label(right, style="Hint.TLabel", wraplength=800, justify="left")
        self.const.pack(anchor="w")
        self.nb = ttk.Notebook(right)
        self.nb.pack(fill="both", expand=True, pady=4)

        per = ttk.Frame(right)
        per.pack(fill="x", pady=(4, 0))
        ttk.Label(per, text="3. Период: с").pack(side="left")
        self.d_from = tk.StringVar(value=short((selection or {}).get("from", "")))
        ttk.Entry(per, textvariable=self.d_from, width=12).pack(side="left", padx=4)
        ttk.Label(per, text="по").pack(side="left")
        self.d_to = tk.StringVar(value=short((selection or {}).get("to", "")))
        ttk.Entry(per, textvariable=self.d_to, width=12).pack(side="left", padx=4)
        ttk.Label(per, text="например 2022 или 2022-03; пусто — вся история / по сегодня",
                  style="Hint.TLabel").pack(side="left", padx=6)
        for v in (self.d_from, self.d_to):
            v.trace_add("write", lambda *_: self.schedule())

        self.count = ttk.Label(right, font=(W.FONT, 10, "bold"))
        self.count.pack(anchor="w", pady=(8, 2))
        pf, self.preview = W.tree_with_scroll(right, ("name", "last"), ("Подходящие ряды", "Последнее значение"),
                                              {"name": 620, "last": 160}, height=7)
        pf.pack(fill="both", expand=False)

        bar = ttk.Frame(self, padding=10)
        bar.pack(fill="x")
        ttk.Label(bar, text="Название выборки:").pack(side="left")
        self.name = tk.StringVar(value=(selection or {}).get("name", ""))
        e = ttk.Entry(bar, textvariable=self.name, width=60)
        e.pack(side="left", padx=6)
        e.bind("<Key>", lambda _e: setattr(self, "name_touched", True))
        self.ok = ttk.Button(bar, text="Сохранить" if selection else "Добавить в модель", style="Accent.TButton",
                             command=self.save)
        self.ok.pack(side="right")
        ttk.Button(bar, text="Отмена", command=self.close).pack(side="right", padx=8)

        self.fill_jobs()
        start = (selection or {}).get("job") or job_id
        if start and start in self.job_ids:
            i = self.job_ids.index(start)
            self.jobs_lb.selection_set(i)
            self.jobs_lb.see(i)
            self.pick_job()
        W.center(self, app.root)
        self.lift()
        self.focus_set()

    def close(self):
        try:
            self.store.close()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()

    # --- показатели --------------------------------------------------------------
    def fill_jobs(self):
        q = self.q.get().strip().lower()
        self.jobs_lb.delete(0, "end")
        self.job_ids = []
        for j in self.app.cfg.jobs():
            if q and q not in j["name"].lower():
                continue
            if j.get("source") == "file_sheets":          # листы файла: рядов нет, в модель идут листы как есть
                k = len((j.get("params") or {}).get("sheets") or [])
                self.job_ids.append(j["id"])
                self.jobs_lb.insert("end", f"{j['name']}  (листы файла: {k})")
                continue
            n = len(self.store.series_of_job(j["id"]))
            if not n:
                continue
            self.job_ids.append(j["id"])
            self.jobs_lb.insert("end", f"{j['name']}  ({n} рядов)")
        if not self.job_ids:
            self.jobs_lb.insert("end", "Нет загруженных показателей")

    def pick_job(self):
        sel = self.jobs_lb.curselection()
        if not sel or sel[0] >= len(self.job_ids):
            return
        job_id = self.job_ids[sel[0]]
        if self.job and self.job["id"] == job_id:
            return
        self.job = self.app.cfg.job(job_id)
        self.rows = [(r, packet.series_fields(r)) for r in self.store.series_of_job(job_id)]
        for tab in self.nb.tabs():
            self.nb.forget(tab)
        for w in self.nb.winfo_children():
            w.destroy()
        self.fields.clear()
        if self.sheets_job():
            self._show_sheets()
            return
        names = []                  # поля всех рядов: у части рядов поля может не быть
        for _r, f in self.rows:
            names += [k for k in f if k not in names]
        values = {}
        for _r, f in self.rows:
            for k in names:
                v = packet.field_value(f, k)
                values.setdefault(k, [])
                if v not in values[k]:
                    values[k].append(v)
        const, prev = [], (self.edit or {}).get("filters", {}) if self.edit and self.edit.get("job") == job_id else {}
        for k, vals in values.items():
            if len(vals) <= 1:
                const.append(f"{k}: {vals[0] if vals else '—'}")
                continue
            entry = add_dim_tab(self.nb, k, [(v, v) for v in sorted(vals, key=str)], self.schedule)
            if k in prev:
                entry[0].clear()
                entry[0].update(v for v in prev[k] if v in vals)
            self.fields[k] = entry
        self.const.configure(text=("Для всех рядов: " + "; ".join(const)) if const else "")
        if not self.fields:
            ttk.Label(self.nb, text="В показателе один ряд — выбирать нечего, задайте период.").pack()
        self.after(50, self.recount)

    def sheets_job(self):
        return bool(self.job) and self.job.get("source") == "file_sheets"

    def _show_sheets(self):
        """Показатель «листы файла»: выбирать нечего — модель получает листы целиком."""
        specs = (self.job.get("params") or {}).get("sheets") or []
        have = self.job["id"] in ((self.app.cfg.model(self.model_name) or {}).get("sheets") or [])
        self.const.configure(text=(
            "Листы файла переносятся в модель как есть — выбирать значения и период не нужно. После «Добавить в "
            "модель» хаб предложит сразу добавить листы в книгу (модель должна быть закрыта в Excel)."))
        names = []
        for sp in specs:
            names.append(f"«{sp['name']}»")
            if sp.get("cells"):
                names.append(f"«{sp['name']} (ячейки)»")
        ttk.Label(self.nb, padding=12, justify="left", text="Листы в модели: " + ", ".join(names)).pack(anchor="w")
        self.preview.delete(*self.preview.get_children())
        for sp in specs:
            self.preview.insert("", "end", values=(f"{sp['name']} ← лист файла «{sp.get('title', '')}»", ""))
        self.count.configure(text="Модель уже получает эти листы." if have else f"Листов: {len(specs)}",
                             foreground="")
        if not self.name_touched:
            self.name.set(self.job["name"])

    # --- подсчёт ---------------------------------------------------------------
    def filters(self):
        """Только поля, где отмечено не всё: новые значения остальных полей придут сами."""
        out = {}
        for k, (chosen, all_ids, _t, _n) in self.fields.items():
            if len(chosen) != len(all_ids):
                out[k] = [v for v in all_ids if v in chosen]
        return out

    def schedule(self, *_a):
        if self._pending:
            self.after_cancel(self._pending)
        self._pending = self.after(200, self.recount)

    def recount(self):
        self._pending = None
        for k, (chosen, all_ids, tab, title) in self.fields.items():
            if str(tab) in self.nb.tabs():
                self.nb.tab(tab, text=f"{title[:26]} ({len(chosen)}/{len(all_ids)})")
        if not self.job or self.sheets_job():
            return
        flt = self.filters()
        hit = [r for r, f in self.rows if packet.matches(f, flt)]
        try:
            a, b = parse_bound(self.d_from.get()), parse_bound(self.d_to.get(), end=True)
            win = None if (a, b) == ("", "") else (a, b)
            bad = ""
        except ValueError as e:
            win, bad = None, str(e)
        self.preview.delete(*self.preview.get_children())
        stats = self.store.window_stats([r["code"] for r in hit], *(win or ("", "")))
        points = sum(v[0] for v in stats.values())
        for r in hit[:300]:
            f = packet.series_fields(r)
            label = " | ".join(str(packet.field_value(f, k)) for k in self.fields) or r["name"]
            st = stats.get(r["code"])
            last = f"{fmt(st[1])} за {st[2]}" if st else "нет данных в периоде"
            self.preview.insert("", "end", values=(label, last))
        text = f"Подходит рядов: {len(hit)}, значений за период: " + f"{points:,}".replace(",", " ")
        if bad:
            text = bad
        elif len(hit) > BIG:
            text += f" — много: в модели будет {len(hit)} столбцов. Сузьте выбор, если нужна выжимка."
        self.count.configure(text=text, foreground="#c62828" if bad or not hit or len(hit) > BIG else "")
        if not self.name_touched:
            self.name.set(self.auto_name(flt))

    def auto_name(self, flt):
        parts = [self.job["name"]]
        for k, vals in flt.items():
            parts.append(", ".join(map(str, vals[:2])) + ("…" if len(vals) > 2 else ""))
        a, b = self.d_from.get().strip(), self.d_to.get().strip()
        if a:
            parts.append(f"с {a}" + (f" по {b}" if b else ""))
        return " — ".join(parts)[:150]

    def save(self):
        if not self.job:
            messagebox.showinfo("Показатель", "Выберите показатель слева.", parent=self)
            return
        if self.sheets_job():
            from .dialog_add import give_sheets
            job_id = self.job["id"]
            self.close()
            give_sheets(self.app, self.model_name, job_id)
            return
        try:
            a, b = parse_bound(self.d_from.get()), parse_bound(self.d_to.get(), end=True)
        except ValueError as e:
            messagebox.showwarning("Период", str(e), parent=self)
            return
        flt = self.filters()
        if not any(packet.matches(f, flt) for _r, f in self.rows):
            messagebox.showwarning("Пусто", "Под выбор не подходит ни один ряд — отметьте хотя бы по одному значению "
                                            "в каждом поле.", parent=self)
            return
        sel = {"id": (self.edit or {}).get("id"), "job": self.job["id"], "name": self.name.get().strip()
               or self.auto_name(flt), "filters": flt, "from": a, "to": b}
        self.app.cfg.save_selection(self.model_name, sel)
        self.app.cfg.unexclude(self.model_name, [r["code"] for r, f in self.rows if packet.matches(f, flt)])
        self.close()
        if self.on_done:
            self.on_done()
