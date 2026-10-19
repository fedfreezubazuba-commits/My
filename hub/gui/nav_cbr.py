"""
Вкладка «Банк России» — навигатор «Сервиса получения данных» ЦБ (перенесён из legacy/cbr_gui.py).

Слева — дерево «раздел → категория → показатель» и поиск; справа — разрезы, период,
описание. «Скачать…» сохраняет файл, «＋ В обновления» добавляет выбранное в обновления
с теми же параметрами, что строка «Повторить».
"""
import json
import os
import queue
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

from ..core.errors import human_message
from .dimlist import add_dim_tab, chosen_years
from ..sources.cbr_api import CBR, LONG_COLS, SITE, export, series_name

SERVICE_URL = SITE + "/statistics/data-service/"
PREVIEW_ROWS = 1000


def stems(text):
    """Грубые основы слов, чтобы «ипотека» находила «ипотечным»."""
    return [w[:max(4, len(w) - 2)] for w in text.lower().split()]


class CbrNav(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.root = self
        self.settings_path = app.paths.cache / "cbr_gui_settings.json"
        self.loaded = False
        self.cbr = CBR()
        self.q = queue.Queue()
        self.roots = None        # дерево публикаций
        self.updated = {}        # id категории -> дата обновления
        self.node_of = {}        # iid дерева -> узел
        self.cur = None          # выбранный узел
        self.lists = {}          # ключ -> (chosen, all_ids, tab, title)
        self.busy = False
        self.pending = None      # узел, выбранный во время загрузки
        self.settings = self._load_settings()

        self._build()
        self.root.after(100, self._poll)

    def ensure_loaded(self):
        """Каталог грузится при первом открытии вкладки, а не при запуске программы."""
        if not self.loaded:
            self.loaded = True
            self.run_bg(self._load_catalog, self._on_catalog, "Загрузка каталога показателей ЦБ…")

    # --- настройки ---------------------------------------------------------
    def _load_settings(self):
        try:
            with open(self.settings_path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _save_settings(self):
        try:
            with open(self.settings_path, "w", encoding="utf-8") as fh:
                json.dump(self.settings, fh, ensure_ascii=False, indent=1)
        except OSError:
            pass

    # --- интерфейс ---------------------------------------------------------
    def _build(self):
        pw = ttk.PanedWindow(self, orient="horizontal")
        pw.pack(side="top", fill="both", expand=True, padx=6, pady=(6, 0))
        self._pw = pw

        # Левая панель: поиск + дерево
        left = ttk.Frame(pw)
        pw.add(left, weight=2)
        bar = ttk.Frame(left)
        bar.pack(fill="x")
        ttk.Label(bar, text="Поиск:").pack(side="left")
        self.search_var = tk.StringVar()
        e = ttk.Entry(bar, textvariable=self.search_var)
        e.pack(side="left", fill="x", expand=True, padx=4)
        e.bind("<Return>", lambda _e: self.fill_tree())
        ttk.Button(bar, text="Найти", command=self.fill_tree).pack(side="left")
        ttk.Button(bar, text="Сброс", command=self._reset_search).pack(side="left", padx=(4, 0))

        opts = ttk.Frame(left)
        opts.pack(fill="x", pady=(4, 2))
        ttk.Button(opts, text="Развернуть всё", command=lambda: self._expand(True)).pack(side="left")
        ttk.Button(opts, text="Свернуть", command=lambda: self._expand(False)).pack(side="left", padx=4)
        ttk.Button(opts, text="Обновить каталог", command=self._refresh_catalog).pack(side="right")

        tf = ttk.Frame(left)
        tf.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tf, columns=("years", "id"), selectmode="browse")
        self.tree.heading("#0", text="Раздел / категория / показатель")
        self.tree.heading("years", text="Данные")
        self.tree.heading("id", text="ID")
        self.tree.column("#0", width=430)
        self.tree.column("years", width=90, anchor="center", stretch=False)
        self.tree.column("id", width=60, anchor="e", stretch=False)
        sb = ttk.Scrollbar(tf, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.tag_configure("section", foreground="#444444")
        self.tree.tag_configure("cat", foreground="#1a4d8f")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        # Правая панель: карточка + вкладки
        right = ttk.Frame(pw)
        pw.add(right, weight=3)
        self.title_var = tk.StringVar(value="Выберите показатель или категорию в дереве слева")
        title = ttk.Label(right, textvariable=self.title_var, font=("Segoe UI", 11, "bold"),
                          wraplength=500, justify="left")
        title.pack(fill="x")
        title.bind("<Configure>", lambda e: title.configure(wraplength=max(e.width - 10, 200)))
        self.sub_var = tk.StringVar()
        sub = ttk.Frame(right)
        sub.pack(fill="x", pady=(2, 4))
        ttk.Label(sub, textvariable=self.sub_var, foreground="#555", wraplength=520,
                  justify="left").pack(side="left", fill="x", expand=True)
        self.site_btn = ttk.Button(sub, text="Методология", state="disabled",
                                   command=self._open_site)
        self.site_btn.pack(side="right")

        self.nb = ttk.Notebook(right)
        self.nb.pack(side="top", fill="both", expand=True)
        self.desc_tab = ttk.Frame(self.nb, padding=4)
        self._build_desc(self.desc_tab)
        self.preview_tab = ttk.Frame(self.nb)
        self._build_preview(self.preview_tab)

        dl = ttk.Frame(right)
        dl.pack(side="bottom", fill="x", pady=6)
        ttk.Label(dl, text="Формат:").pack(side="left")
        self.fmt = tk.StringVar(value=self.settings.get("fmt", "xlsx"))
        for v, t in (("xlsx", "Excel (.xlsx)"), ("csv", "CSV — длинная"), ("csv_wide", "CSV — даты × ряды")):
            ttk.Radiobutton(dl, text=t, value=v, variable=self.fmt).pack(side="left", padx=4)
        self.sel_var = tk.StringVar()
        ttk.Label(dl, textvariable=self.sel_var, foreground="#555").pack(side="left", padx=10)
        act = ttk.Frame(right)
        act.pack(side="bottom", fill="x", pady=(0, 6))
        self.add_btn = ttk.Button(act, text="＋ В обновления", style="Add.TButton", state="disabled",
                                  command=self._add_to_updates)
        self.add_btn.pack(side="right", padx=(6, 0))
        self.dl_btn = ttk.Button(act, text="Скачать файл…", state="disabled", command=self._download)
        self.dl_btn.pack(side="right")
        act.pack_forget()
        dl.pack_forget()
        self.nb.pack_forget()
        act.pack(side="bottom", fill="x", pady=(0, 6))
        dl.pack(side="bottom", fill="x", pady=6)
        self.nb.pack(side="top", fill="both", expand=True)

        st = ttk.Frame(self)
        self._pw.pack_forget()
        st.pack(side="bottom", fill="x", padx=6, pady=(0, 4))
        self._pw.pack(side="top", fill="both", expand=True, padx=6, pady=(6, 0))
        self.status = tk.StringVar()
        ttk.Label(st, textvariable=self.status).pack(side="left")
        self.pb = ttk.Progressbar(st, mode="indeterminate", length=160)

    def _build_desc(self, parent):
        self.desc = tk.Text(parent, wrap="word", relief="flat", font=("Segoe UI", 10),
                            background=self.app.root.cget("background"))
        sb = ttk.Scrollbar(parent, command=self.desc.yview)
        self.desc.configure(yscrollcommand=sb.set, state="disabled")
        sb.pack(side="right", fill="y")
        self.desc.pack(fill="both", expand=True)

    def _build_preview(self, parent):
        self.pv = ttk.Treeview(parent, show="headings")
        ys = ttk.Scrollbar(parent, command=self.pv.yview)
        xs = ttk.Scrollbar(parent, orient="horizontal", command=self.pv.xview)
        self.pv.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        xs.pack(side="bottom", fill="x")
        ys.pack(side="right", fill="y")
        self.pv.pack(fill="both", expand=True)

    # --- фоновые задачи ----------------------------------------------------
    def run_bg(self, work, done, msg):
        if self.busy:
            messagebox.showinfo("Подождите", "Предыдущий запрос ещё выполняется.")
            return
        self.busy = True
        self.status.set(msg)
        self.pb.pack(side="right")
        self.pb.start(12)

        def target():
            try:
                self.q.put((done, work(), None))
            except Exception as exc:  # noqa: BLE001
                self.q.put((done, None, exc))
        threading.Thread(target=target, daemon=True).start()

    def _poll(self):
        try:
            while True:
                done, res, err = self.q.get_nowait()
                self.busy = False
                self.pb.stop()
                self.pb.pack_forget()
                if err:
                    self.status.set("Ошибка")
                    messagebox.showerror("Не получилось", human_message(err))
                else:
                    done(res)
                if self.pending is not None and not self.busy:
                    node, self.pending = self.pending, None
                    self._load_node(node)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    # --- каталог и дерево --------------------------------------------------
    def _load_catalog(self):
        self.cbr._cats = None
        tree, inds = self.cbr.tree(), self.cbr.indicators()
        try:   # для общего поиска на вкладке «Найти данные»
            (self.app.paths.cache / "cbr_catalog.json").write_text(json.dumps(inds, ensure_ascii=False), "utf-8")
        except OSError:
            pass
        return tree, inds

    def _on_catalog(self, res):
        self.roots, inds = res
        self.updated = {}

        def walk(nodes):
            for n in nodes:
                if n["section"]:
                    walk(n["children"])
                else:
                    self.updated[n["id"]] = n["updated"]
        walk(self.roots)
        self.status.set(f"Каталог загружен: {len(self.updated)} категорий, {len(inds)} показателей")
        self.fill_tree()

    def _refresh_catalog(self):
        self.run_bg(self._load_catalog, self._on_catalog, "Обновление каталога с сайта…")

    def _reset_search(self):
        self.search_var.set("")
        self.fill_tree()

    def _expand(self, flag):
        def walk(iid):
            for c in self.tree.get_children(iid):
                self.tree.item(c, open=flag)
                walk(c)
        walk("")

    @staticmethod
    def _years(inds):
        return f"{min(c['begin_dt'] for c in inds)}–{max(c['end_dt'] for c in inds)}" if inds else ""

    def _insert_ind(self, parent, ind, children, text=None):
        iid = self.tree.insert(parent, "end", text=text or ind["indicator_name"],
                               values=(f"{ind['begin_dt']}–{ind['end_dt']}", ind["indicator_id"]))
        self.node_of[iid] = {"kind": "ind", "ind": ind}
        for ch in children.get(ind["indicator_id"], []):
            # у дочерних показателей имя начинается с имени родителя — укорачиваем
            name = ch["indicator_name"]
            pref = ind["indicator_name"] + "-"
            self._insert_ind(iid, ch, children, name[len(pref):] if name.startswith(pref) else name)
        return iid

    def _insert_cat(self, parent, node):
        inds = self.cbr.indicators(node["id"])
        iid = self.tree.insert(parent, "end", text=f"{node['name']}  ({len(inds)} пок.)",
                               values=(self._years(inds), node["id"]), tags=("cat",))
        self.node_of[iid] = {"kind": "cat", "id": node["id"], "name": node["name"], "inds": inds}
        children = {}
        for c in inds:
            children.setdefault(c["indicator_parent"], []).append(c)
        for c in sorted(children.get(-1, []), key=lambda c: c["indicator_name"]):
            self._insert_ind(iid, c, children)

    def fill_tree(self):
        if not self.roots:
            return
        self.tree.delete(*self.tree.get_children())
        self.node_of.clear()
        text = self.search_var.get().strip()
        if not text:
            def walk(parent, nodes):
                for n in nodes:
                    if n["section"]:
                        iid = self.tree.insert(parent, "end", text=n["name"], tags=("section",))
                        self.node_of[iid] = {"kind": "section"}
                        walk(iid, n["children"])
                    else:
                        self._insert_cat(parent, n)
            walk("", self.roots)
            for iid in self.tree.get_children(""):
                self.tree.item(iid, open=True)
            return
        # Поиск: плоский список совпадений с категорией в скобках
        ss = stems(text)
        found = 0
        for c in sorted(self.cbr.indicators(), key=lambda c: (c["category_name"], c["indicator_name"])):
            hay = (c["indicator_name"] + " " + c["category_name"]).lower()
            if all(s in hay for s in ss):
                found += 1
                cat = c["category_name"].split("-")
                self._insert_ind("", c, {}, f"{c['indicator_name']}   ⟨{' / '.join(cat[-2:])}⟩")
        self.status.set(f"Найдено показателей: {found}")

    # --- выбор узла --------------------------------------------------------
    def _on_select(self, _e):
        sel = self.tree.selection()
        node = self.node_of.get(sel[0]) if sel else None
        if not node or node["kind"] == "section":
            return
        if self.busy:
            self.pending = node
            return
        self._load_node(node)

    def _load_node(self, node):
        if node["kind"] == "cat":
            self.run_bg(lambda: (node, self.cbr.updated(node["id"])), self._show_category,
                        f"Загрузка категории {node['id']}…")
            return
        ind = node["ind"]
        self.title_var.set(ind["indicator_name"])
        self.sub_var.set(f"ID {ind['indicator_id']} — загрузка разрезов…")
        cid, iid = ind["category_id"], ind["indicator_id"]
        self.run_bg(lambda: (ind, self.cbr.structure(cid, iid), self.cbr.description(iid),
                             self.cbr.updated(cid).get(iid, "")),
                    self._on_indicator, f"Загрузка разрезов показателя {iid}…")

    def _clear_tabs(self):
        fixed = {str(t) for t in (self.desc_tab, self.preview_tab)}
        for tab in self.nb.tabs():
            self.nb.forget(tab)
            if tab not in fixed:
                self.root.nametowidget(tab).destroy()
        self.lists.clear()

    def _show_category(self, res):
        node, upd = res
        last = max((d for d in upd.values() if d), default="—")
        inds = sorted(node["inds"], key=lambda c: c["indicator_name"])
        self.cur = {"kind": "cat", "cid": node["id"], "inds": inds, "struct": None}
        self.title_var.set(node["name"])
        self.sub_var.set(f"Категория {node['id']}  •  показателей: {len(inds)}  •  "
                         f"данные {self._years(inds)}  •  обновлено {last}\n"
                         "Отметьте показатели — разрезы будут выгружены все.")
        self.site_btn.configure(state="normal")
        self._clear_tabs()
        self._add_list_tab("i", "Показатели",
                           [(c["indicator_id"], f"{c['indicator_name']}  [{c['begin_dt']}–{c['end_dt']}]")
                            for c in inds])
        self._finish_tabs(min(c["begin_dt"] for c in inds), max(c["end_dt"] for c in inds), "")
        self._set_desc("Методология показателей категории — по кнопке «Методология» "
                       "или на вкладке «Описание» отдельного показателя.")

    def _on_indicator(self, res):
        ind, st, desc, upd = res
        self.cur = {"kind": "ind", "cid": ind["category_id"], "inds": [ind], "struct": st}
        units = ", ".join(st["units"].values()) or "—"
        per = ", ".join(st["periodicity"]) or "—"
        self.title_var.set(ind["indicator_name"])
        self.sub_var.set(f"Категория {ind['category_id']}: {ind['category_name']}\n"
                         f"ID {ind['indicator_id']}  •  ед. изм.: {units}  •  периодичность: {per}  •  "
                         f"обновлено {upd or '—'}")
        self.site_btn.configure(state="normal" if ind.get("link") else "disabled")
        self._clear_tabs()
        for key, title in (("m1", "Разрез 1"), ("m2", "Разрез 2")):
            if st[key]:
                self._add_list_tab(key, title, list(st[key].items()))
        self._finish_tabs(ind["begin_dt"], ind["end_dt"], per)
        self._set_desc(desc or "Описание методологии для этого показателя не опубликовано.")
        if not st["links"]:
            self.status.set("За последние годы данных нет — выберите более ранний период")

    def _finish_tabs(self, y_from, y_to, per):
        years = [(y, str(y)) for y in range(y_from, y_to + 1)]
        self.lists["y"] = add_dim_tab(
            self.nb, "Год", years, self._update_counts, years=True)
        self.nb.add(self.desc_tab, text="Описание")
        self.nb.add(self.preview_tab, text="Данные")
        self.nb.select(0)
        self.dl_btn.configure(state="normal")
        self.add_btn.configure(state="normal")
        self._update_counts()
        self.status.set(f"Данные с {y_from} по {y_to} год" + (f", {per}" if per else "") +
                        ". Отметьте нужное и нажмите «＋ В обновления» или «Скачать файл…»")

    def _year_range(self):
        ys = chosen_years(self.lists["y"]) if "y" in self.lists else []
        return (ys[0], ys[-1]) if ys else (None, None)

    def _set_desc(self, text):
        self.desc.configure(state="normal")
        self.desc.delete("1.0", "end")
        self.desc.insert("1.0", text)
        self.desc.configure(state="disabled")

    def _add_list_tab(self, key, title, items):
        self.lists[key] = add_dim_tab(self.nb, title, items, self._update_counts)

    def _update_counts(self):
        if not self.cur:
            return
        for key, (chosen, all_ids, tab, title) in self.lists.items():
            if str(tab) in self.nb.tabs():
                self.nb.tab(tab, text=f"{title[:26]} ({len(chosen)}/{len(all_ids)})")
        n_years = len(self.lists["y"][0]) if "y" in self.lists else 0
        st = self.cur["struct"]
        if st:
            m1 = self.lists.get("m1", ({None},))[0]
            m2 = self.lists.get("m2", ({None},))[0]
            n = sum(1 for l in st["links"] if (not st["m1"] or l["measure1_id"] in m1)
                    and (not st["m2"] or l["measure2_id"] in m2))
            self.sel_var.set(f"рядов: {n}, лет: {n_years}")
        elif "i" in self.lists:
            self.sel_var.set(f"показателей: {len(self.lists['i'][0])}, лет: {n_years}")

    def _open_site(self):
        links = [c.get("link") for c in (self.cur or {}).get("inds", []) if c.get("link")]
        webbrowser.open(SITE + links[0] if links else SERVICE_URL)

    # --- выгрузка ----------------------------------------------------------
    def _picked(self, key):
        """ID отмеченных значений; [] — если отмечено всё (API тогда отдаёт всё)."""
        if key not in self.lists:
            return []
        chosen, all_ids, _t, _n = self.lists[key]
        return [] if len(chosen) == len(all_ids) else [v for v in all_ids if v in chosen]

    def _download(self):
        if not self.cur:
            return
        empty = [n for _k, (c, _a, _t, n) in self.lists.items() if not c]
        if empty:
            messagebox.showwarning("Пустой выбор", "Не выбрано ни одного значения: " + ", ".join(empty))
            return
        y1, y2 = self._year_range()
        if y1 is None:
            messagebox.showwarning("Пустой выбор", "Отметьте хотя бы один год.")
            return
        cid = self.cur["cid"]
        if self.cur["kind"] == "ind":
            i_ids = [self.cur["inds"][0]["indicator_id"]]
            base = f"cbr_{cid}_{i_ids[0]}"
        else:
            i_ids = self._picked("i")
            base = f"cbr_{cid}"
        m1, m2 = self._picked("m1"), self._picked("m2")
        fmt = self.fmt.get()
        ext = ".xlsx" if fmt == "xlsx" else ".csv"
        path = filedialog.asksaveasfilename(
            initialdir=self.settings.get("dir", str(self.app.paths.exports)), initialfile=f"{base}_{y1}-{y2}{ext}",
            defaultextension=ext,
            filetypes=[("Excel", "*.xlsx")] if ext == ".xlsx" else [("CSV", "*.csv")])
        if not path:
            return
        if not path.lower().endswith(ext):
            path += ext
        self.settings.update(dir=os.path.dirname(path), fmt=fmt)
        self._save_settings()
        wide = fmt == "csv_wide"
        self.run_bg(lambda: (path, *export(self.cbr, cid, path, i_ids, m1, m2, (y1, y2), wide)),
                    self._on_downloaded, f"Выгрузка данных категории {cid} за {y1}–{y2}…")

    def _on_downloaded(self, res):
        path, rows, cmd = res
        n_series = len({series_name(r) for r in rows})
        msg = f"Сохранено: {path}   ({len(rows)} значений, {n_series} рядов)"
        self._show_preview(rows)
        self.status.set(msg)
        if messagebox.askyesno("Готово", msg + "\n\nОткрыть папку с файлом?"):
            if hasattr(os, "startfile"):
                os.startfile(os.path.dirname(path))

    def _show_preview(self, rows):
        self.pv.delete(*self.pv.get_children())
        keys = [k for k, _ in LONG_COLS if not k.endswith("_id")]
        titles = dict(LONG_COLS)
        self.pv.configure(columns=keys)
        widths = {"date": 85, "periodicity": 80, "indicator": 220, "measure1": 140,
                  "measure2": 200, "unit": 90, "value": 90}
        for k in keys:
            self.pv.heading(k, text=titles[k])
            self.pv.column(k, width=widths.get(k, 100), stretch=k in ("indicator", "measure2"),
                           anchor="e" if k == "value" else "w")
        for r in rows[:PREVIEW_ROWS]:
            self.pv.insert("", "end", values=[r[k].strftime("%d.%m.%Y") if k == "date"
                                              else ("" if r[k] is None else r[k]) for k in keys])
        self.nb.select(self.preview_tab)


    # --- «＋ В обновления» -------------------------------------------------
    def current_params(self):
        """Параметры показателя — те же, что в строке «Повторить» навигатора."""
        if not self.cur:
            return None, None
        empty = [n for _k, (c, _a, _t, n) in self.lists.items() if not c]
        if empty:
            raise ValueError("Не выбрано ни одного значения: " + ", ".join(empty))
        cid = self.cur["cid"]
        if self.cur["kind"] == "ind":
            ind = self.cur["inds"][0]
            i_ids = [ind["indicator_id"]]
            name = ind["indicator_name"]
        else:
            i_ids = self._picked("i")
            inds = [c for c in self.cur["inds"] if not i_ids or c["indicator_id"] in i_ids]
            name = inds[0]["indicator_name"] if len(inds) == 1 else (inds[0]["category_name"] if inds else str(cid))
        m1, m2 = self._picked("m1"), self._picked("m2")
        st = self.cur.get("struct") or {}
        for key, ids in (("m1", m1), ("m2", m2)):
            if ids and st.get(key) and len(ids) <= 3:
                name += " — " + ", ".join(str(st[key].get(i, i)) for i in ids)
        params = {"category": cid, "indicators": i_ids, "m1": m1, "m2": m2}
        chosen, all_ids, _t, _n = self.lists["y"]
        if len(chosen) != len(all_ids):          # отмечены не все годы — грузим с первого отмеченного
            params["y1"] = self._year_range()[0]
        return params, name

    def _add_to_updates(self):
        try:
            params, name = self.current_params()
        except ValueError as e:
            messagebox.showwarning("Пустой выбор", str(e), parent=self)
            return
        if not params:
            return
        from .dialog_add import AddDialog
        AddDialog(self.app, "cbr", params, name)

    def select_indicator(self, category_id, indicator_id, name=""):
        """Открыть показатель из общего поиска."""
        self.ensure_loaded()

        def attempt(tries=0):
            if not self.roots:
                if tries < 300:
                    self.after(200, lambda: attempt(tries + 1))
                return
            self.search_var.set(name or str(indicator_id))
            self.fill_tree()
            for iid, node in self.node_of.items():
                if node.get("kind") == "ind" and node["ind"]["indicator_id"] == int(indicator_id) \
                        and node["ind"]["category_id"] == int(category_id):
                    self.tree.selection_set(iid)
                    self.tree.see(iid)
                    return
        attempt()
