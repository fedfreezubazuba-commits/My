"""Общие элементы интерфейса: стили, светофор, прокручиваемая панель, буфер обмена,
кнопки-значки с подсказками, панель кнопок с переносом, текст по ширине окна."""
import re
import sys
import tkinter as tk
from tkinter import ttk

from ..core import status as ST

IS_WIN = sys.platform.startswith("win")
FONT = "Segoe UI" if IS_WIN else "DejaVu Sans"
BG = "#f5f7fa"
CARD = "#ffffff"
ACCENT = "#1565c0"
MUTED = "#6b7785"
SELECTED_BG = "#cfe3fb"    # нажатая кнопка-фильтр
UPDATED_BG = "#c8e6c9"     # календарь «Обзора»: день с новыми данными
UPDATED_FG = "#1b5e20"
ICON_FONT = ("Segoe UI Symbol", 13) if IS_WIN else ("DejaVu Sans", 12)   # значки — символы из шрифта


def apply_style(root):
    style = ttk.Style(root)
    for theme in (("vista", "clam") if IS_WIN else ("clam",)):
        try:
            style.theme_use(theme)
            break
        except tk.TclError:
            continue
    from tkinter import font as tkfont
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        try:
            tkfont.nametofont(name).configure(family=FONT, size=10)
        except tk.TclError:
            pass
    style.configure(".", font=(FONT, 10))
    style.configure("TNotebook.Tab", padding=(14, 6), font=(FONT, 10))
    style.configure("Big.TLabel", font=(FONT, 20, "bold"))
    style.configure("Title.TLabel", font=(FONT, 13, "bold"))
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("Hint.TLabel", foreground=MUTED, font=(FONT, 9))
    style.configure("Accent.TButton", font=(FONT, 10, "bold"), padding=(12, 6))
    style.configure("Add.TButton", font=(FONT, 10, "bold"), padding=(12, 6), foreground=ACCENT)
    style.configure("Icon.TButton", font=ICON_FONT, padding=(2, 0), width=2)
    style.configure("Treeview", rowheight=24)
    style.configure("Treeview.Heading", font=(FONT, 9, "bold"))
    for name, color in ST.COLORS.items():
        style.configure(f"{name}.TLabel", foreground=color)
    return style


def status_text(status) -> str:
    c = ST.color(status)
    return f"{ST.DOTS.get(c, '●')} {ST.word(status)}"


def tag_colors(tree):
    for name, color in ST.COLORS.items():
        tree.tag_configure(name, foreground=color)


def copy_to_clipboard(widget, text):
    widget.clipboard_clear()
    widget.clipboard_append(text)
    widget.update_idletasks()


def tree_with_scroll(parent, columns, headings, widths=None, height=12, show="headings", selectmode="browse"):
    frame = ttk.Frame(parent)
    tree = ttk.Treeview(frame, columns=columns, show=show, height=height, selectmode=selectmode)
    for c, h in zip(columns, headings):
        tree.heading(c, text=h)
        tree.column(c, width=(widths or {}).get(c, 120), stretch=True, anchor="w")
    ys = ttk.Scrollbar(frame, command=tree.yview)
    xs = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
    tree.grid(row=0, column=0, sticky="nsew")
    ys.grid(row=0, column=1, sticky="ns")
    xs.grid(row=1, column=0, sticky="ew")
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    tag_colors(tree)
    fit_columns(tree)
    return frame, tree


def fit_columns(tree, minimum=40):
    """Столбцы таблицы делят её ширину в тех же пропорциях — при узком окне видно все столбцы,
    при широком таблица заполняет окно (вместо прокрутки вбок)."""
    state = {"base": None, "w": 0}

    def on(e):
        if e.width < 50 or abs(e.width - state["w"]) < 3:
            return
        state["w"] = e.width
        cols = list(tree["columns"])
        if "tree" in str(tree.cget("show")):
            cols = ["#0"] + cols
        if state["base"] is None:
            state["base"] = {c: max(minimum, int(tree.column(c, "width"))) for c in cols}
        base = state["base"]
        total = sum(base.values()) or 1
        avail = e.width - 4
        for c in cols:
            tree.column(c, width=max(minimum, int(base.get(c, minimum) * avail / total)), stretch=False)
    tree.bind("<Configure>", on, add="+")


_DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})(?:\s+(\d{1,2}):(\d{2}))?")
_NUM = re.compile(r"[-−]?\d[\d\s\u00a0]*(?:,\d+)?(?![\d.\-])")
EMPTY = ("", "—", "-")


def _as_date(s):
    m = _DATE.match(s)
    return (m.group(3), m.group(2), m.group(1), int(m.group(4) or 0), m.group(5) or "00") if m else None


def _as_num(s):
    m = _NUM.match(s)
    if not m:
        return None
    try:
        return float(re.sub(r"\s", "", m.group(0)).replace(",", ".").replace("−", "-"))
    except ValueError:
        return None


def sort_key_fn(values):
    """Как сравнивать значения столбца: все — даты «дд.мм.гггг» → по дате; все начинаются с числа → по числу;
    иначе — по тексту без учёта регистра (значок статуса «●» не мешает)."""
    vals = [v for v in values if v not in EMPTY]
    if vals and all(_as_date(v) for v in vals):
        return _as_date
    if vals and all(_as_num(v) is not None for v in vals):
        return _as_num
    return lambda v: v.lstrip("●○◐◑▲▼■□◆◇⏸✓✗ ").casefold()


def sortable(tree):
    """Щелчок по заголовку столбца — сортировка (по возрастанию, повторный щелчок — по убыванию).
    Пустые «—» всегда внизу. После перезаполнения таблицы вызвать resort(tree)."""
    tree._sort = {"col": None, "rev": False, "titles": {c: tree.heading(c, "text") for c in tree["columns"]}}

    def click(c):
        st = tree._sort
        st["rev"] = not st["rev"] if st["col"] == c else False
        st["col"] = c
        for col, title in st["titles"].items():
            mark = (" ▼" if st["rev"] else " ▲") if col == c else ""
            tree.heading(col, text=title + mark)
        resort(tree)

    for c in tree["columns"]:
        tree.heading(c, command=lambda c=c: click(c))
    return tree


def resort(tree, parent=""):
    st = getattr(tree, "_sort", None)
    if not st or not st["col"]:
        return
    items = [(tree.set(i, st["col"]), i) for i in tree.get_children(parent)]
    key = sort_key_fn([v for v, _i in items])
    full = [(v, i) for v, i in items if v not in EMPTY and key(v) is not None]
    empty = [(v, i) for v, i in items if v in EMPTY or key(v) is None]
    full.sort(key=lambda x: key(x[0]), reverse=st["rev"])
    for n, (_v, i) in enumerate(full + empty):
        tree.move(i, parent, n)


class ScrollFrame(ttk.Frame):
    """Прокручиваемая панель для списков карточек («Обзор»)."""

    def __init__(self, parent, **kw):
        super().__init__(parent, **kw)
        bg = ttk.Style(self).lookup("TFrame", "background") or BG
        self.canvas = tk.Canvas(self, highlightthickness=0, background=bg)
        sb = ttk.Scrollbar(self, command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.inner.bind("<Configure>", lambda e: self._region())
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.canvas.bind_all("<MouseWheel>", self._wheel, add="+")

    def _wheel(self, e):
        try:
            if str(self.focus_get()).startswith(str(self)) or self.winfo_containing(e.x_root, e.y_root):
                self.canvas.yview_scroll(int(-e.delta / 120), "units")
        except (tk.TclError, KeyError):
            pass

    def _region(self):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        if self.inner.winfo_reqheight() <= self.canvas.winfo_height():
            self.canvas.yview_moveto(0)      # всё помещается — прокрутки нет, содержимое сверху

    def clear(self):
        for w in self.inner.winfo_children():
            w.destroy()
        self.canvas.yview_moveto(0)          # новое содержимое показываем с начала


class Collapsible(ttk.Frame):
    """Свёрнутый блок «Дополнительно ▸»."""

    def __init__(self, parent, title="Дополнительно"):
        super().__init__(parent)
        self.title = title
        self.open = tk.BooleanVar(value=False)
        self.btn = ttk.Button(self, text=f"▸ {title}", command=self.toggle, style="Toolbutton")
        self.btn.pack(anchor="w")
        self.body = ttk.Frame(self, padding=(16, 4, 0, 4))

    def toggle(self):
        self.open.set(not self.open.get())
        if self.open.get():
            self.body.pack(fill="x")
            self.btn.configure(text=f"▾ {self.title}")
        else:
            self.body.pack_forget()
            self.btn.configure(text=f"▸ {self.title}")


def center(win, parent):
    win.update_idletasks()
    try:
        x = parent.winfo_rootx() + (parent.winfo_width() - win.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - win.winfo_height()) // 3
        win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
    except tk.TclError:
        pass


def text_window(parent, title, text, copy=True):
    w = tk.Toplevel(parent)
    w.title(title)
    w.geometry("760x520")
    t = tk.Text(w, wrap="word", font=(FONT, 10), padx=10, pady=10)
    t.insert("1.0", text)
    t.configure(state="disabled")
    bar = ttk.Frame(w, padding=8)
    bar.pack(side="bottom", fill="x")
    if copy:
        ttk.Button(bar, text="Копировать всё", command=lambda: copy_to_clipboard(w, text)).pack(side="left")
    ttk.Button(bar, text="Закрыть", command=w.destroy).pack(side="right")
    t.pack(fill="both", expand=True)
    center(w, parent)
    return w


class Tooltip:
    """Подсказка при наведении (через полсекунды), как в обычных программах Windows."""

    def __init__(self, widget, text, delay=500):
        self.widget, self.text, self.delay = widget, text, delay
        self.tip = self.job = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._hide()
        self.job = self.widget.after(self.delay, self._show)

    def _show(self):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 8
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=self.text, justify="left", background="#ffffe8", relief="solid", borderwidth=1,
                 wraplength=360, padx=6, pady=3, font=(FONT, 9)).pack()
        self.tip.update_idletasks()          # у правого и нижнего края экрана подсказка не уходит за край
        sw, sh = self.widget.winfo_screenwidth(), self.widget.winfo_screenheight()
        tw, th = self.tip.winfo_reqwidth(), self.tip.winfo_reqheight()
        if x + tw > sw - 4:
            x = max(0, sw - tw - 4)
        if y + th > sh - 4:
            y = max(0, self.widget.winfo_rooty() - th - 4)
        self.tip.wm_geometry(f"+{x}+{y}")

    def _hide(self, _e=None):
        if self.job:
            self.widget.after_cancel(self.job)
            self.job = None
        if self.tip:
            self.tip.destroy()
            self.tip = None


def toggle_button(parent, text, variable, command):
    """Кнопка-переключатель для сплошного ряда фильтров: нажата — подсвечена и с галочкой."""
    b = tk.Checkbutton(parent, text=text, variable=variable, command=command, indicatoron=False,
                       font=(FONT, 9), padx=10, pady=2, bd=1, relief="raised", offrelief="raised",
                       overrelief="raised", background=CARD, activebackground="#e8f0fb",
                       selectcolor=SELECTED_BG, cursor="hand2")
    return b


def icon_button(parent, icon, tip, command, text=None, style=None):
    """Кнопка-значок с подсказкой. С text — значок и подпись (для главных действий)."""
    if text:
        b = ttk.Button(parent, text=f"{icon}  {text}", command=command, style=style or "TButton")
    else:
        b = ttk.Button(parent, text=icon, command=command, style=style or "Icon.TButton")
    b.tooltip = Tooltip(b, tip)
    return b


class FlowBar(ttk.Frame):
    """Панель кнопок: если окно узкое, кнопки переносятся на следующую строку, а не прячутся за край."""

    def __init__(self, parent, gap=6, **kw):
        super().__init__(parent, **kw)
        self.gap = gap
        self.items = []
        self.bind("<Configure>", lambda e: self._layout(e.width))

    def add(self, widget, gap_before=0):
        self.items.append((widget, gap_before))
        self.after_idle(lambda: self._layout(self.winfo_width()))
        return widget

    def _layout(self, width):
        if width <= 1:
            width = 10000
        x = y = row_h = 0
        for w, gap_before in self.items:
            ww, wh = w.winfo_reqwidth(), w.winfo_reqheight()
            if x and x + gap_before + ww > width:
                x, y, row_h = 0, y + row_h + 4, 0
            elif x:
                x += gap_before
            w.place(in_=self, x=x, y=y)
            x += ww + self.gap
            row_h = max(row_h, wh)
        h = y + row_h
        if int(self.cget("height") or 0) != h:
            self.configure(height=h)


def autowrap(label, margin=24, minimum=160, initial=600):
    """Текст переносится по ширине, которую метке даёт окно (вместо постоянной ширины).
    Метка должна растягиваться (pack fill="x" или grid sticky="ew"); initial — ширина до первой раскладки,
    чтобы окно без заданного размера не растянулось на всю длину строки."""
    label.configure(wraplength=initial)

    def on(e):
        w = max(minimum, e.width - margin)
        if abs(int(str(label.cget("wraplength") or 0)) - w) > 2:
            label.configure(wraplength=w)
    label.bind("<Configure>", on, add="+")
    return label
