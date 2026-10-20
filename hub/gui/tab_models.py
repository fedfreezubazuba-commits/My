"""«Модели» — подключённые Excel-модели и ряды, которые они получают."""
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from ..core.base_fmt import fmt, fmt_date
from ..excel import exports, models as M, packet
from . import widgets as W
from .tab_jobs import FormulaWindow


class ModelsTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        ttk.Button(bar, text="＋ Подключить модель", style="Accent.TButton", command=self.connect).pack(side="left")
        for text, cmd in (("＋ Данные для модели", self.add_series), ("Изменить выборку", self.edit_selection),
                          ("Убрать", self.remove_series),
                          ("Формула", self.formula), ("Открыть в Excel", self.open_excel)):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=(6, 0))
        more = ttk.Menubutton(bar, text="Ещё ▾")
        menu = tk.Menu(more, tearoff=False)
        for text, cmd in (("Открыть модель", lambda: self.open_model()), ("Открыть файл с данными", self.open_packet),
                          ("Вид таблицы «Хаб»…", self.choose_layout),
                          ("Проверить связь с Excel", self.diagnose), ("Отключить модель", self.disconnect)):
            menu.add_command(label=text, command=cmd)
        more["menu"] = menu
        more.pack(side="left", padx=(6, 0))
        pw = ttk.PanedWindow(self, orient="vertical")
        pw.pack(fill="both", expand=True, pady=8)
        top, self.tree = W.tree_with_scroll(pw, ("name", "path", "n", "built", "news"),
                                            ("Модель", "Файл", "Рядов", "Данные обновлены", "Новые данные"),
                                            {"name": 180, "path": 420, "n": 60, "built": 140, "news": 260}, height=7)
        pw.add(top, weight=1)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_series())
        bottom = ttk.Frame(pw)
        pw.add(bottom, weight=2)
        self.caption = ttk.Label(bottom, text="Что получает модель", style="Title.TLabel")
        self.caption.pack(anchor="w", pady=(4, 2))
        sf, self.series = W.tree_with_scroll(bottom, ("pos", "code", "last", "value"),
                                             ("№ столбца", "Код (имя столбца в таблице Хаб)",
                                              "Последний период", "Последнее значение"),
                                             {"pos": 80, "code": 260, "last": 110, "value": 130},
                                             selectmode="extended", show="tree headings")
        self.series.heading("#0", text="Выборка / ряд")
        self.series.column("#0", width=520, stretch=True)
        sf.pack(fill="both", expand=True)
        self.groups = {}        # iid строки-группы -> ("sel", id) | ("job", id) | ("codes", None)
        self.code_of = {}       # iid строки-ряда -> код
        ttk.Label(self, style="Hint.TLabel", wraplength=1100, justify="left", text=(
            "Модель получает данные через файл в папке «Для моделей» и обновляет их сама при открытии "
            "(или «Данные → Обновить всё»). Новые ряды появляются справа, старые столбцы не сдвигаются.")).pack(anchor="w")

    def refresh(self):
        sel = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        news = dict(M.models_with_new_data(self.app.cfg, self.app.store))
        for m in self.app.cfg.models():
            if not m.get("active", True):
                continue
            st = self.app.store.model_state(m["name"])
            n = len(self.app.store.subscribed(m["name"]))
            note = f"есть новые данные по {news[m['name']]} рядам — откройте модель" if m["name"] in news else ""
            if st.get("last_error"):
                note = "файл с данными занят — запись повторится"
            self.tree.insert("", "end", iid=m["name"], values=(m["name"], m.get("path", ""), n,
                                                               fmt_date(st.get("packet_changed_at")), note))
        for i in sel:
            if self.tree.exists(i):
                self.tree.selection_set(i)
        if not self.tree.selection() and self.tree.get_children():
            self.tree.selection_set(self.tree.get_children()[0])
        self.show_series()

    def current(self, quiet=False):
        sel = self.tree.selection()
        if not sel:
            if not quiet:
                messagebox.showinfo("Модель", "Сначала выберите модель в списке (или подключите новую).", parent=self)
            return None
        return self.app.cfg.model(sel[0])

    def show_series(self):
        tree = self.series
        tree.delete(*tree.get_children())
        self.groups.clear()
        self.code_of.clear()
        m = self.current(quiet=True)
        if not m:
            self.caption.configure(text="Что получает модель")
            return
        store, cfg = self.app.store, self.app.cfg
        order = {c: i for i, c in enumerate(packet.model_codes(cfg, store, m), 3)}   # столбцы A, B — Дата, Период
        jobs = {j["id"]: j for j in cfg.jobs()}
        excl = set(m.get("exclude", []))
        groups = []
        for sel in m.get("selections", []):
            codes = packet.selection_codes(store, sel) if sel.get("job") in jobs else []
            groups.append((("sel", sel["id"]), f"Выборка: {sel.get('name') or sel['id']}", codes))
        for job_id in m.get("jobs", []):
            if job_id in jobs:
                codes = [r["code"] for r in store.series_of_job(job_id)]
                note = " — много рядов, нажмите «Изменить выборку», чтобы отдавать выжимку" if len(codes) > 50 else ""
                groups.append((("job", job_id), f"Весь показатель: {jobs[job_id]['name']}{note}", codes))
        if m.get("series"):
            groups.append((("codes", None), "Отдельные ряды", list(m["series"])))
        self.group_codes = {}
        for gi, (key, title, codes) in enumerate(groups):
            if key[0] != "codes":
                codes = [c for c in codes if c not in excl]
            giid = f"g{gi}"
            self.groups[giid] = key
            self.group_codes[giid] = codes
            tree.insert("", "end", iid=giid, text=f"{title}  ({len(codes)} рядов)", open=len(codes) <= 30)
            shown = codes[:self.SHOW_ROWS]
            rows = {c: store.series(c) for c in shown}
            fields = {c: packet.series_fields(r) for c, r in rows.items() if r}
            names = list(dict.fromkeys(k for f in fields.values() for k in f))
            varying = [k for k in names if len({str(packet.field_value(f, k)) for f in fields.values()}) > 1]
            for c in shown:
                s = rows.get(c)
                f = fields.get(c, {})
                label = " | ".join(str(packet.field_value(f, k)) for k in varying) or (s["name"] if s else c)
                last = store.last_values(c, 1)
                iid = f"{giid}|{c}"
                self.code_of[iid] = c
                tree.insert(giid, "end", iid=iid, text=label, values=(
                    order.get(c, ""), c, last[0]["period_label"] if last else "—",
                    fmt(last[0]["value"]) if last else "—"))
            if len(codes) > len(shown):
                tree.insert(giid, "end", iid=f"{giid}|more", text=f"…и ещё {len(codes) - len(shown)} рядов "
                            "(показаны первые)", values=("", "", "", ""))
        self.caption.configure(text=f"Что получает модель «{m['name']}» — рядов: {len(order)}")

    SHOW_ROWS = 500          # в списке группы — не больше стольких рядов (иначе окно долго строится)

    def selected_codes(self):
        out = []
        for iid in self.series.selection():
            if iid in self.code_of:
                out.append(self.code_of[iid])
            elif iid in self.groups:
                out += self.group_codes.get(iid, [])
        return list(dict.fromkeys(out))

    # --- действия ------------------------------------------------------------
    def connect(self):
        folder = self.app.cfg.settings().get("models_folder") or str(Path.home())
        path = filedialog.askopenfilename(parent=self, title="Выберите файл модели Excel", initialdir=folder,
                                          filetypes=[("Книги Excel", "*.xlsx *.xlsm *.xlsb"), ("Все файлы", "*.*")])
        if not path:
            return
        name = simpledialog.askstring("Имя модели", "Как называть модель в хабе?", parent=self,
                                      initialvalue=Path(path).stem)
        if not name:
            return
        self._run_connect(name, path)

    def _run_connect(self, name, path):
        from ..excel.com_connect import is_file_open
        if is_file_open(Path(path)):
            messagebox.showwarning("Модель открыта", "Закройте модель в Excel и повторите.", parent=self)
            return

        def work():
            from ..core.store import Store
            st = Store(self.app.paths.db)
            try:
                return M.connect(self.app.cfg, st, self.app.paths, name, path, log=self.app.log.info)
            finally:
                st.close()

        def done(res):
            self.app.refresh_all()
            self.tree.selection_set(name) if self.tree.exists(name) else None
            text = res["message"] + "\n\nДальше: «＋ Данные для модели» — отметьте, какую выжимку получает модель. " \
                                    f"Пример формулы:\n{res['formula']}"
            if res["files"]:
                text += "\n\nФайлы: " + "\n".join(res["files"])
            (messagebox.showinfo if res["ok"] else messagebox.showwarning)("Подключение модели", text, parent=self)

        self.app.bg(work, done, f"Подключаю модель «{name}» (Excel работает в фоне)…",
                    error=lambda e: messagebox.showerror("Подключение модели", M.connect_error_text(e), parent=self))

    def choose_layout(self):
        m = self.current()
        if m:
            LayoutDialog(self, m)

    def apply_layout(self, m, lay):
        self.app.cfg.update_model(m["name"], layout=lay)
        self._rebuild()
        if m.get("connected_method") in ("com", "com_partial") and Path(m["path"]).exists():
            if messagebox.askyesno("Вид таблицы", f"Перестроить таблицу «Хаб» в модели «{m['name']}» сейчас?\n\n"
                                                  "Хаб откроет модель в фоне (перед этим сделает резервную копию), "
                                                  "поменяет запрос «Хаб» и сохранит модель. Модель должна быть "
                                                  "закрыта. Формулы, которые ссылаются на старые столбцы таблицы "
                                                  "«Хаб», придётся переписать (кнопка «Формула» подскажет новые).",
                                   parent=self):
                self._run_connect(m["name"], m["path"])
        else:
            messagebox.showinfo("Вид таблицы", "Сохранено. Вид применится, когда вы подключите модель "
                                               "(«＋ Подключить модель»).", parent=self)

    def diagnose(self):
        """Пробует все способы подключения на пустой книге и показывает журнал (модель не трогается)."""
        def work():
            from ..excel import com_connect, packet as P
            test = self.app.paths.cache / "проверка_excel.csv"
            test.write_bytes(P._csv([["2025-01-31", "2025-01", "TEST_1", "Проверка", "шт", "1.5"]], P.PACKET_COLS))
            P.series_path(test).write_bytes(P._csv([["TEST_1", "Проверка", "шт", "месячная", "2025-01-31", "1.5",
                                                     "", "", "", "", "1"]], P.SERIES_COLS))
            return com_connect.diagnose(test, log=self.app.log.info)

        def done(lines):
            from ..core import claude_pack
            text = ("# Проверка связи Хаба с Excel\n\nПрочитай CLAUDE.md. Ниже — журнал попытки подключить запросы "
                    "Power Query к пустой книге разными способами. Найди рабочий способ и поправь "
                    "hub/excel/com_connect.py.\n\n```\n" + "\n".join(lines) + "\n```\n")
            path = claude_pack.save(self.app.paths, text, "проверка_excel")
            W.copy_to_clipboard(self.app.root, text)
            W.text_window(self.app.root, "Проверка связи с Excel",
                          "Результат скопирован в буфер обмена и сохранён в файл:\n" + str(path) +
                          "\nЕсли подключение модели не работает — вставьте этот текст в сессию Claude.\n\n" +
                          "\n".join(lines))
        self.app.bg(work, done, "Проверяю связь с Excel (Excel работает в фоне)…")

    def add_series(self):
        m = self.current()
        if m:
            from .dialog_select import SelectionDialog
            SelectionDialog(self.app, m["name"], on_done=self._rebuild)

    def edit_selection(self):
        m = self.current()
        if not m:
            return
        key = next((self.groups[i] for i in self.series.selection() if i in self.groups), None)
        if key is None:
            parent = next((self.series.parent(i) for i in self.series.selection() if i in self.code_of), None)
            key = self.groups.get(parent)
        if not key or key[0] == "codes":
            messagebox.showinfo("Изменить выборку", "Выделите строку выборки (или «Весь показатель») в нижнем списке.",
                                parent=self)
            return
        from .dialog_select import SelectionDialog
        if key[0] == "sel":
            sel = next(s for s in m["selections"] if s.get("id") == key[1])
            SelectionDialog(self.app, m["name"], selection=sel, on_done=self._rebuild)
        else:
            SelectionDialog(self.app, m["name"], job_id=key[1], on_done=self._rebuild)

    def remove_series(self):
        m = self.current()
        if not m:
            return
        chosen = self.series.selection()
        if not chosen:
            messagebox.showinfo("Убрать", "Выделите выборку или ряд в нижнем списке.", parent=self)
            return
        n = len(self.selected_codes())
        if not messagebox.askyesno("Убрать", f"Модель «{m['name']}» перестанет получать {n} ряд(а/ов). Их столбцы "
                                             "исчезнут из таблицы Хаб после обновления модели — проверьте, что на них "
                                             "нет формул. Продолжить?", parent=self):
            return
        loose, excluded = [], []       # всё одной записью файла: рядов могут быть тысячи
        for iid in chosen:
            if iid in self.groups:
                kind, ident = self.groups[iid]
                if kind == "sel":
                    self.app.cfg.remove_selection(m["name"], ident)
                elif kind == "job":
                    self.app.cfg.unsubscribe_job(m["name"], ident)
                else:
                    loose += self.group_codes.get(iid, [])
            elif iid in self.code_of and self.series.parent(iid) not in chosen:
                parent = self.groups.get(self.series.parent(iid), ("codes", None))
                # ряд из «Отдельных рядов» просто убираем; из выборки — помечаем «не отдавать»
                (loose if parent[0] == "codes" else excluded).append(self.code_of[iid])
        if loose:
            self.app.cfg.unsubscribe_codes(m["name"], loose)
        if excluded:
            self.app.cfg.unsubscribe_codes(m["name"], excluded, exclude=True)
        self._rebuild()

    def _rebuild(self):
        def work():
            from ..core.store import Store
            st = Store(self.app.paths.db)
            try:
                return packet.rebuild(self.app.cfg, st, self.app.paths)
            finally:
                st.close()

        def done(res):
            self.refresh()
            busy = [k for k, v in res.items() if str(v).startswith("busy")]
            if busy:
                messagebox.showwarning("Файл занят", "Файл с данными для модели сейчас открыт. Закройте его — "
                                                     "хаб запишет данные при следующем обновлении.", parent=self)
        self.app.bg(work, done, "Обновляю файлы для моделей…")

    def formula(self):
        m = self.current()
        if not m:
            return
        codes = self.selected_codes() or self.app.store.subscribed(m["name"])
        FormulaWindow(self.app, codes[:500], model=m)

    def open_excel(self):
        """Разовый файл с выбранными рядами (или всеми рядами модели) — для просмотра."""
        m = self.current()
        if not m:
            return
        codes = self.selected_codes() or self.app.store.subscribed(m["name"])
        if not codes:
            messagebox.showinfo("Нет рядов", "Модель пока не получает ни одного ряда.", parent=self)
            return

        def work():
            from ..core.store import Store
            st = Store(self.app.paths.db)
            try:
                return exports.export_codes(self.app.cfg, st, self.app.paths, codes, m["name"])
            finally:
                st.close()
        self.app.bg(work, exports.open_file, "Готовлю файл Excel…")

    def open_model(self, name=None):
        m = self.app.cfg.model(name) if name else self.current()
        if not m:
            return
        if not Path(m.get("path", "")).exists():
            messagebox.showwarning("Модель", f"Файл модели не найден: {m.get('path')}", parent=self)
            return
        exports.open_file(m["path"])

    def open_packet(self):
        m = self.current()
        if m:
            p = packet.packet_path(self.app.paths, m["name"])
            exports.open_file(p.parent)

    def disconnect(self):
        m = self.current()
        if m and messagebox.askyesno("Отключить", f"Хаб перестанет обновлять данные для модели «{m['name']}». "
                                                  "Саму модель хаб не трогает: данные в ней останутся. Продолжить?",
                                     parent=self):
            M.disconnect(self.app.cfg, m["name"])
            self.refresh()


class LayoutDialog(tk.Toplevel):
    """Вид таблицы «Хаб» в модели: строки (плоская) или столбцы."""

    EXAMPLES = {
        "long": "Дата        Период   Показатель   Регион              Товар     Единицы  Код   Обновлено   Значение\n"
                "31.01.2024  2024-01  Средние цены Московская область  Пшеница   руб./т   …     19.10.2026  15 200\n"
                "31.01.2024  2024-01  Средние цены Московская область  Кукуруза  руб./т   …     19.10.2026  12 400",
        "wide": "Дата        Период   FEDSTAT_…_1   FEDSTAT_…_2\n"
                "31.01.2024  2024-01  15 200        12 400\n"
                "(названия рядов, регион и товар — в соседней таблице Хаб_Ряды)",
    }

    def __init__(self, tab, model):
        super().__init__(tab.app.root)
        from ..excel import packet as P
        self.tab, self.model = tab, model
        self.title(f"Вид таблицы «Хаб» — {model['name']}")
        self.transient(tab.app.root)
        f = ttk.Frame(self, padding=16)
        f.pack(fill="both", expand=True)
        self.var = tk.StringVar(value=P.layout(tab.app.cfg, model))
        titles = {"long": "Строки — каждое значение отдельной строкой со всеми полями (рекомендуется)",
                  "wide": "Столбцы — каждый ряд отдельным столбцом, даты по строкам"}
        for key in ("long", "wide"):
            ttk.Radiobutton(f, text=titles[key], value=key, variable=self.var).pack(anchor="w", pady=(8, 2))
            ttk.Label(f, text=self.EXAMPLES[key], font=("Consolas", 9), style="Muted.TLabel",
                      justify="left").pack(anchor="w", padx=24)
        ttk.Label(f, style="Hint.TLabel", wraplength=720, justify="left", text=(
            "Строки удобны для фильтров, сводных таблиц и СУММЕСЛИМН по любому полю. Excel вмещает до 1 млн строк — "
            "для больших выборок сузьте период или число рядов.")).pack(anchor="w", pady=(10, 0))
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(14, 0))
        ttk.Button(bar, text="Применить", style="Accent.TButton", command=self.apply).pack(side="right")
        ttk.Button(bar, text="Отмена", command=self.destroy).pack(side="right", padx=8)
        W.center(self, tab.app.root)

    def apply(self):
        lay = self.var.get()
        self.destroy()
        self.tab.apply_layout(self.model, lay)
