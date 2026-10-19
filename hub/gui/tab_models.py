"""«Модели» — подключённые Excel-модели и ряды, которые они получают."""
import tkinter as tk
from pathlib import Path
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
        for text, cmd in (("Добавить ряды", self.add_series), ("Убрать ряд", self.remove_series),
                          ("Формула", self.formula), ("Открыть в Excel", self.open_excel),
                          ("Открыть модель", lambda: self.open_model()),
                          ("Открыть файл с данными", self.open_packet), ("Отключить", self.disconnect)):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="Проверить связь с Excel", command=self.diagnose).pack(side="right")
        pw = ttk.PanedWindow(self, orient="vertical")
        pw.pack(fill="both", expand=True, pady=8)
        top, self.tree = W.tree_with_scroll(pw, ("name", "path", "n", "built", "news"),
                                            ("Модель", "Файл", "Рядов", "Данные обновлены", "Новые данные"),
                                            {"name": 180, "path": 420, "n": 60, "built": 140, "news": 260}, height=7)
        pw.add(top, weight=1)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_series())
        bottom = ttk.Frame(pw)
        pw.add(bottom, weight=2)
        self.caption = ttk.Label(bottom, text="Ряды модели", style="Title.TLabel")
        self.caption.pack(anchor="w", pady=(4, 2))
        sf, self.series = W.tree_with_scroll(bottom, ("pos", "code", "name", "last", "value"),
                                             ("№ столбца", "Код (имя столбца в таблице Хаб)", "Название",
                                              "Последний период", "Последнее значение"),
                                             {"pos": 80, "code": 230, "name": 420, "last": 110, "value": 130},
                                             selectmode="extended")
        sf.pack(fill="both", expand=True)
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
        self.series.delete(*self.series.get_children())
        m = self.current(quiet=True)
        if not m:
            self.caption.configure(text="Ряды модели")
            return
        self.caption.configure(text=f"Ряды модели «{m['name']}»")
        codes = packet.model_codes(self.app.cfg, self.app.store, m)
        for i, c in enumerate(codes, 1):
            s = self.app.store.series(c)
            last = self.app.store.last_values(c, 1)
            self.series.insert("", "end", iid=c, values=(i + 2, c, s["name"] if s else "",
                                                         last[0]["period_label"] if last else "—",
                                                         fmt(last[0]["value"]) if last else "—"))

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
        from ..excel.com_connect import is_file_open
        if is_file_open(Path(path)):
            messagebox.showwarning("Модель открыта", "Закройте модель в Excel и нажмите «Подключить модель» ещё раз.",
                                   parent=self)
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
            text = res["message"] + "\n\nДальше: «Добавить ряды» — отметьте, какие данные получает модель. " \
                                    f"Пример формулы:\n{res['formula']}"
            if res["files"]:
                text += "\n\nФайлы: " + "\n".join(res["files"])
            (messagebox.showinfo if res["ok"] else messagebox.showwarning)("Подключение модели", text, parent=self)

        self.app.bg(work, done, f"Подключаю модель «{name}» (Excel работает в фоне)…",
                    error=lambda e: messagebox.showerror("Подключение модели", M.connect_error_text(e), parent=self))

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
            PickSeries(self.app, m, on_done=self._rebuild)

    def remove_series(self):
        m = self.current()
        if not m:
            return
        codes = self.series.selection()
        if not codes:
            messagebox.showinfo("Убрать ряд", "Выделите ряд в нижнем списке.", parent=self)
            return
        if not messagebox.askyesno("Убрать ряд", f"Модель «{m['name']}» перестанет получать {len(codes)} ряд(а). "
                                                 "Столбцы исчезнут из таблицы Хаб после обновления модели — "
                                                 "проверьте, что на них нет формул. Продолжить?", parent=self):
            return
        for c in codes:
            self.app.cfg.unsubscribe_code(m["name"], c)
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
        codes = list(self.series.selection()) or self.app.store.subscribed(m["name"])
        FormulaWindow(self.app, codes)

    def open_excel(self):
        """Разовый файл с выбранными рядами (или всеми рядами модели) — для просмотра."""
        m = self.current()
        if not m:
            return
        codes = list(self.series.selection()) or self.app.store.subscribed(m["name"])
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


class PickSeries(tk.Toplevel):
    """«Добавить ряды»: выбор из «Моих показателей» с поиском и галками."""

    def __init__(self, app, model, on_done=None):
        super().__init__(app.root)
        self.app = app
        self.model = model
        self.on_done = on_done
        self.title(f"Ряды для модели «{model['name']}»")
        self.geometry("980x600")
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")
        ttk.Label(top, text="Поиск:").pack(side="left")
        self.q = tk.StringVar()
        ttk.Entry(top, textvariable=self.q, width=40).pack(side="left", padx=6)
        self.q.trace_add("write", lambda *_: self.fill())
        ttk.Label(top, text="Щёлкните по строке, чтобы поставить или снять галку.", style="Hint.TLabel").pack(side="left", padx=10)
        frame, self.tree = W.tree_with_scroll(self, ("mark", "name", "code", "unit"), ("", "Ряд", "Код", "Единицы"),
                                              {"mark": 40, "name": 560, "code": 220, "unit": 100}, show="tree headings")
        self.tree.column("#0", width=30, stretch=False)
        frame.pack(fill="both", expand=True, padx=10)
        self.tree.bind("<Button-1>", self.toggle)
        subs = set(app.store.subscribed(model["name"]))
        self.marked = set(subs)
        bar = ttk.Frame(self, padding=10)
        bar.pack(fill="x")
        ttk.Button(bar, text="Сохранить", style="Accent.TButton", command=self.save).pack(side="right")
        ttk.Button(bar, text="Отмена", command=self.destroy).pack(side="right", padx=8)
        self.fill()
        W.center(self, app.root)

    def fill(self):
        self.tree.delete(*self.tree.get_children())
        q = self.q.get().strip().lower()
        for job in self.app.cfg.jobs():
            series = self.app.store.series_of_job(job["id"])
            rows = [s for s in series if not q or q in (s["name"] + s["code"] + job["name"]).lower()]
            if not rows:
                continue
            all_on = all(s["code"] in self.marked for s in series)
            jid = "job:" + job["id"]
            self.tree.insert("", "end", iid=jid, open=bool(q) or len(series) <= 6,
                             values=("☑" if all_on else "☐", f"{job['name']}  (все ряды показателя)", "", ""))
            for s in rows:
                self.tree.insert(jid, "end", iid=s["code"], values=("☑" if s["code"] in self.marked else "☐",
                                                                    s["name"], s["code"], s["unit"]))

    def toggle(self, e):
        iid = self.tree.identify_row(e.y)
        if not iid or self.tree.identify_element(e.x, e.y) in ("Treeitem.indicator",):
            return
        if iid.startswith("job:"):
            codes = [s["code"] for s in self.app.store.series_of_job(iid[4:])]
            on = not all(c in self.marked for c in codes)
            for c in codes:
                (self.marked.add if on else self.marked.discard)(c)
        else:
            (self.marked.discard if iid in self.marked else self.marked.add)(iid)
        self.fill()
        return "break"

    def save(self):
        cfg = self.app.cfg
        before = set(self.app.store.subscribed(self.model["name"]))
        all_codes = {s["code"]: s["job_id"] for s in self.app.store.all_series()}
        for c in self.marked - before:
            cfg.subscribe(self.model["name"], code=c)
        for c in before - self.marked:
            cfg.unsubscribe_code(self.model["name"], c)
        # если отмечены все ряды показателя — подписка на показатель целиком (новые ряды придут сами)
        m = cfg.model(self.model["name"])
        for job in cfg.jobs():
            codes = [c for c, j in all_codes.items() if j == job["id"]]
            if codes and all(c in self.marked for c in codes) and job["id"] not in m["jobs"]:
                cfg.subscribe(self.model["name"], job_id=job["id"])
        self.destroy()
        if self.on_done:
            self.on_done()
