"""Общие элементы интерфейса: стили, светофор, прокручиваемая панель, буфер обмена."""
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
    return frame, tree


class ScrollFrame(ttk.Frame):
    """Прокручиваемая панель для списков карточек («Обзор»)."""

    def __init__(self, parent, **kw):
        super().__init__(parent, **kw)
        bg = ttk.Style(self).lookup("TFrame", "background") or BG
        self.canvas = tk.Canvas(self, highlightthickness=0, background=bg)
        sb = ttk.Scrollbar(self, command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
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

    def clear(self):
        for w in self.inner.winfo_children():
            w.destroy()


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
