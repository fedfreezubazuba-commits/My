"""
Навигатор ЕМИСС (fedstat.ru): дерево показателей, выбор разрезов, выгрузка.

Запуск:  python fedstat_gui.py

1. Слева — дерево «ведомство → разделы → показатели» и поиск по названию.
2. Выберите показатель — справа появятся его измерения (годы, периоды,
   регионы…). В каждой вкладке отметьте нужные значения.
3. «Скачать…» — CSV (длинная таблица), Excel (как на сайте) или SDMX XML.
   После выгрузки CSV/SDMX первые строки видны на вкладке «Данные».
"""
import csv
import os
import queue
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fedstat_client import BASE, Fedstat, sdmx_to_rows  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "catalog_cache.json")
SEARCH_LIMIT = 1000
PREVIEW_ROWS = 1000


class App:
    def __init__(self, root):
        self.root = root
        self.fs = Fedstat()
        self.q = queue.Queue()
        self.cat = None
        self.node_of = {}       # iid дерева -> узел каталога
        self.info = None        # измерения текущего показателя
        self.dim_lists = {}     # filter_id -> (listbox, [value_id...], tab)
        self.busy = False

        root.title("Навигатор ЕМИСС — fedstat.ru")
        root.geometry("1280x780")
        root.minsize(900, 560)
        self._build()
        self.root.after(100, self._poll)
        self.run_bg(lambda: self.fs.catalog(CACHE), self._on_catalog,
                    "Загрузка каталога (первый раз ~1 мин)…")

    # --- интерфейс ---------------------------------------------------------
    def _build(self):
        pw = ttk.PanedWindow(self.root, orient="horizontal")
        pw.pack(fill="both", expand=True, padx=6, pady=(6, 0))

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
        self.hide_excl = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Скрыть исключённые", variable=self.hide_excl,
                        command=self.fill_tree).pack(side="left")
        ttk.Button(opts, text="Обновить каталог", command=self._refresh_catalog).pack(side="right")

        tf = ttk.Frame(left)
        tf.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tf, columns=("years", "id"), selectmode="browse")
        self.tree.heading("#0", text="Ведомство / раздел / показатель")
        self.tree.heading("years", text="Данные")
        self.tree.heading("id", text="ID")
        self.tree.column("#0", width=430)
        self.tree.column("years", width=90, anchor="center", stretch=False)
        self.tree.column("id", width=60, anchor="e", stretch=False)
        sb = ttk.Scrollbar(tf, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.tag_configure("excl", foreground="#999999")
        self.tree.tag_configure("nolink", foreground="#bbbbbb")
        self.tree.bind("<<TreeviewOpen>>", self._on_open)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        # Правая панель: карточка показателя + вкладки
        right = ttk.Frame(pw)
        pw.add(right, weight=3)
        self.title_var = tk.StringVar(value="Выберите показатель в дереве слева")
        title = ttk.Label(right, textvariable=self.title_var, font=("Segoe UI", 11, "bold"),
                          wraplength=500, justify="left")
        title.pack(fill="x")
        title.bind("<Configure>", lambda e: title.configure(wraplength=max(e.width - 10, 200)))
        self.sub_var = tk.StringVar()
        sub = ttk.Frame(right)
        sub.pack(fill="x", pady=(2, 4))
        ttk.Label(sub, textvariable=self.sub_var, foreground="#555").pack(side="left")
        self.site_btn = ttk.Button(sub, text="Открыть на сайте", state="disabled",
                                   command=self._open_site)
        self.site_btn.pack(side="right")

        self.nb = ttk.Notebook(right)
        self.nb.pack(fill="both", expand=True)
        self.preview_tab = ttk.Frame(self.nb)
        self._build_preview(self.preview_tab)

        dl = ttk.Frame(right)
        dl.pack(fill="x", pady=6)
        ttk.Label(dl, text="Формат:").pack(side="left")
        self.fmt = tk.StringVar(value="csv")
        for v, t in (("csv", "CSV"), ("excel", "Excel (.xls)"), ("sdmx", "SDMX XML")):
            ttk.Radiobutton(dl, text=t, value=v, variable=self.fmt).pack(side="left", padx=4)
        self.sel_var = tk.StringVar()
        ttk.Label(dl, textvariable=self.sel_var, foreground="#555").pack(side="left", padx=10)
        self.dl_btn = ttk.Button(dl, text="Скачать…", state="disabled", command=self._download)
        self.dl_btn.pack(side="right")

        # Статус
        st = ttk.Frame(self.root)
        st.pack(fill="x", padx=6, pady=(0, 4))
        self.status = tk.StringVar()
        ttk.Label(st, textvariable=self.status).pack(side="left")
        self.pb = ttk.Progressbar(st, mode="indeterminate", length=160)

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
                    messagebox.showerror("Ошибка", self._err_text(err))
                else:
                    done(res)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    @staticmethod
    def _err_text(err):
        s = str(err)
        if "503" in s:
            return ("Сервер fedstat.ru временно отклоняет запросы (503) — так он "
                    "ограничивает частые выгрузки. Подождите минуту и повторите.\n\n" + s)
        return s

    # --- каталог и дерево --------------------------------------------------
    def _on_catalog(self, cat):
        self.cat = cat
        n = len(cat["years"])
        self.status.set(f"Каталог от {cat.get('updated', '?')}: {len(cat['roots'])} ведомств, "
                        f"{n} показателей с данными о годах")
        self.fill_tree()

    def _refresh_catalog(self):
        self.run_bg(lambda: self.fs.catalog(CACHE, refresh=True), self._on_catalog,
                    "Обновление каталога с сайта…")

    def _reset_search(self):
        self.search_var.set("")
        self.fill_tree()

    def _visible(self, node):
        return not (self.hide_excl.get() and node.get("excluded"))

    def _years(self, node):
        y = self.cat["years"].get(node.get("id"), {})
        a, b = y.get("dataStart", ""), y.get("dataEnd", "")
        return f"{a}–{b}" if a or b else ""

    def _insert(self, parent, node):
        is_ind = "children" not in node
        label = node["name"] if is_ind else f"{node.get('code', '')} {node['name']}".strip()
        tags = []
        if node.get("excluded"):
            tags.append("excl")
        if is_ind and not node.get("id"):
            tags.append("nolink")
        iid = self.tree.insert(parent, "end", text=label, tags=tags,
                               values=(self._years(node) if is_ind else "",
                                       node.get("id", "")))
        self.node_of[iid] = node
        if not is_ind and node["children"]:
            self.tree.insert(iid, "end", text="…")  # заглушка для ленивой загрузки
        return iid

    def fill_tree(self):
        if not self.cat:
            return
        self.tree.delete(*self.tree.get_children())
        self.node_of.clear()
        text = self.search_var.get().strip().lower()
        if not text:
            for r in self.cat["roots"]:
                if self._visible(r):
                    self._insert("", r)
            return
        # Поиск: плоский список совпадений с путём раздела
        words = text.split()
        seen, found = set(), 0

        def walk(nodes, path):
            nonlocal found
            for n in nodes:
                if found >= SEARCH_LIMIT or not self._visible(n):
                    continue
                if "children" in n:
                    walk(n["children"], path + [n["name"]])
                elif n.get("id") and n["id"] not in seen and all(w in n["name"].lower() for w in words):
                    seen.add(n["id"])
                    found += 1
                    iid = self._insert("", n)
                    self.tree.item(iid, text=f"{n['name']}   ⟨{path[0]}⟩" if path else n["name"])
        walk(self.cat["roots"], [])
        self.status.set(f"Найдено: {found}" + (" (показаны первые)" if found >= SEARCH_LIMIT else ""))

    def _on_open(self, _e):
        iid = self.tree.focus()
        kids = self.tree.get_children(iid)
        if len(kids) == 1 and self.tree.item(kids[0], "text") == "…":
            self.tree.delete(kids[0])
            for c in self.node_of[iid]["children"]:
                if self._visible(c):
                    self._insert(iid, c)

    def _on_select(self, _e):
        sel = self.tree.selection()
        if not sel:
            return
        node = self.node_of.get(sel[0])
        if not node or "children" in node or not node.get("id"):
            return
        ind_id = node["id"]
        self.title_var.set(node["name"])
        self.sub_var.set(f"ID {ind_id} — загрузка измерений…")
        self.run_bg(lambda: self.fs.indicator(ind_id), self._on_indicator,
                    f"Загрузка измерений показателя {ind_id}…")

    # --- измерения ---------------------------------------------------------
    def _on_indicator(self, info):
        self.info = info
        self.title_var.set(info["title"])
        self.sub_var.set(f"ID {info['id']}   •   единица: {info['unit'] or '—'}   •   "
                         f"измерений: {len(info['filters'])}")
        self.site_btn.configure(state="normal")
        for tab in self.nb.tabs():
            if tab != str(self.preview_tab):
                self.nb.forget(tab)
        self.dim_lists.clear()
        if not info["filters"]:
            self.status.set("У показателя нет измерений — вероятно, на сайте нет данных")
            self.dl_btn.configure(state="disabled")
            return
        for k, f in info["filters"].items():
            self._add_dim_tab(k, f)
        self.nb.add(self.preview_tab, text="Данные")
        self.nb.select(0)
        self.dl_btn.configure(state="normal")
        self._update_counts()
        self.status.set("Отметьте нужные значения и нажмите «Скачать…»")

    def _add_dim_tab(self, fid, f):
        tab = ttk.Frame(self.nb, padding=4)
        self.nb.insert(len(self.nb.tabs()) - 1 if str(self.preview_tab) in self.nb.tabs() else "end",
                       tab, text=f["title"][:30])
        top = ttk.Frame(tab)
        top.pack(fill="x")
        flt = tk.StringVar()
        ent = ttk.Entry(top, textvariable=flt, width=30)
        ent.pack(side="left")
        ttk.Label(top, text="← фильтр списка", foreground="#777").pack(side="left", padx=4)
        lb_frame = ttk.Frame(tab)
        lb_frame.pack(fill="both", expand=True, pady=4)
        lb = tk.Listbox(lb_frame, selectmode="multiple", activestyle="none", exportselection=False)
        sb = ttk.Scrollbar(lb_frame, command=lb.yview)
        lb.configure(yscrollcommand=sb.set)
        lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        all_ids = list(f["values"].keys())
        chosen = set(all_ids)   # по умолчанию выбрано всё (как на сайте)
        shown = []

        def redraw(*_a):
            shown.clear()
            lb.delete(0, "end")
            s = flt.get().strip().lower()
            for vid in all_ids:
                t = f["values"][vid].strip()
                if s in t.lower():
                    shown.append(vid)
                    lb.insert("end", t)
                    if vid in chosen:
                        lb.selection_set("end")
            self._update_counts()

        def on_sel(_e):
            cur = set(lb.curselection())
            for i, vid in enumerate(shown):
                (chosen.add if i in cur else chosen.discard)(vid)
            self._update_counts()

        def set_all(flag):
            for vid in shown:
                (chosen.add if flag else chosen.discard)(vid)
            redraw()

        btns = ttk.Frame(tab)
        btns.pack(fill="x")
        ttk.Button(btns, text="Выбрать видимые", command=lambda: set_all(True)).pack(side="left")
        ttk.Button(btns, text="Снять видимые", command=lambda: set_all(False)).pack(side="left", padx=4)
        if fid == "3":  # годы: быстрый выбор последних лет
            for n in (5, 10):
                ttk.Button(btns, text=f"Последние {n}",
                           command=lambda n=n: (chosen.clear(), chosen.update(all_ids[-n:]), redraw())
                           ).pack(side="left", padx=2)
        ttk.Label(btns, text=f"всего значений: {len(all_ids)}", foreground="#777").pack(side="right")

        flt.trace_add("write", redraw)
        lb.bind("<<ListboxSelect>>", on_sel)
        self.dim_lists[fid] = (chosen, all_ids, tab, f["title"])
        redraw()

    def _update_counts(self):
        total = 1
        for fid, (chosen, all_ids, tab, title) in self.dim_lists.items():
            self.nb.tab(tab, text=f"{title[:26]} ({len(chosen)}/{len(all_ids)})")
            total *= max(len(chosen), 0)
        self.sel_var.set(f"комбинаций ≈ {total:,}".replace(",", " ") if self.dim_lists else "")

    def _open_site(self):
        if self.info:
            webbrowser.open(f"{BASE}/indicator/{self.info['id']}")

    # --- выгрузка ----------------------------------------------------------
    def _download(self):
        if not self.info:
            return
        empty = [t for _f, (c, _a, _t, t) in self.dim_lists.items() if not c]
        if empty:
            messagebox.showwarning("Пустой выбор", "Не выбрано ни одного значения в: " + ", ".join(empty))
            return
        fmt = self.fmt.get()
        ext = {"csv": ".csv", "excel": ".xls", "sdmx": ".xml"}[fmt]
        path = filedialog.asksaveasfilename(
            initialdir=HERE, initialfile=f"fedstat_{self.info['id']}{ext}", defaultextension=ext,
            filetypes=[({"csv": "CSV", "excel": "Excel 97-2003", "sdmx": "SDMX XML"}[fmt], "*" + ext)])
        if not path:
            return
        ind_id = self.info["id"]
        select = {fid: list(c) for fid, (c, _a, _t, _n) in self.dim_lists.items()}

        def work():
            data = self.fs.download(ind_id, "excel" if fmt == "excel" else "sdmx", select)
            rows = None
            if fmt == "excel":
                with open(path, "wb") as fh:
                    fh.write(data)
            else:
                rows = sdmx_to_rows(data)
                if fmt == "sdmx":
                    with open(path, "wb") as fh:
                        fh.write(data)
                else:
                    cols = list(dict.fromkeys(k for r in rows for k in r))
                    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
                        w = csv.DictWriter(fh, cols, delimiter=";")
                        w.writeheader()
                        w.writerows(rows)
            return path, rows

        self.run_bg(work, self._on_downloaded, f"Выгрузка показателя {ind_id}…")

    def _on_downloaded(self, res):
        path, rows = res
        msg = f"Сохранено: {path}"
        if rows is not None:
            msg += f"   ({len(rows)} строк)"
            self._show_preview(rows)
        self.status.set(msg)
        if messagebox.askyesno("Готово", msg + "\n\nОткрыть папку с файлом?"):
            os.startfile(os.path.dirname(path)) if hasattr(os, "startfile") else None

    def _show_preview(self, rows):
        self.pv.delete(*self.pv.get_children())
        cols = list(dict.fromkeys(k for r in rows[:PREVIEW_ROWS] for k in r))
        self.pv.configure(columns=cols)
        for c in cols:
            self.pv.heading(c, text=c)
            self.pv.column(c, width=110 if c not in ("TIME", "VALUE") else 70, stretch=True)
        for r in rows[:PREVIEW_ROWS]:
            self.pv.insert("", "end", values=[r.get(c, "") for c in cols])
        if not rows:
            self.status.set("Сервер вернул пустой набор — попробуйте другой срез")
        self.nb.select(self.preview_tab)


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
