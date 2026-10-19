"""«Отчёты» — последние отчёты об обновлениях."""
import html
import re
import tkinter as tk
from tkinter import ttk

from ..core.report import list_reports, short_title
from ..excel.exports import open_file
from . import widgets as W


def html_to_text(s: str) -> str:
    s = re.sub(r"(?is)<(style|script).*?</\1>", "", s)
    s = re.sub(r"(?i)<br\s*/?>|</(p|div|h\d|li|tr)>", "\n", s)
    s = re.sub(r"(?i)</t[dh]>", "   ", s)
    s = re.sub(r"(?i)<li>", "• ", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return re.sub(r"\n\s*\n+", "\n\n", s).strip()


class ReportsTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
        pw = ttk.PanedWindow(self, orient="horizontal")
        pw.pack(fill="both", expand=True)
        left = ttk.Frame(pw)
        pw.add(left, weight=1)
        ttk.Label(left, text="Отчёты", style="Title.TLabel").pack(anchor="w")
        self.lb = tk.Listbox(left, activestyle="none", exportselection=False, font=(W.FONT, 10))
        self.lb.pack(fill="both", expand=True, pady=4)
        self.lb.bind("<<ListboxSelect>>", lambda e: self.show())
        ttk.Button(left, text="Открыть в браузере", command=self.open).pack(fill="x")
        right = ttk.Frame(pw)
        pw.add(right, weight=4)
        self.text = tk.Text(right, wrap="word", font=(W.FONT, 10), padx=12, pady=10, relief="flat")
        self.text.pack(fill="both", expand=True)
        self.files = []

    def refresh(self):
        self.files = list_reports(self.app.paths)
        self.lb.delete(0, "end")
        for f in self.files:
            self.lb.insert("end", short_title(f))
        if self.files:
            self.lb.selection_set(0)
            self.show()
        else:
            self._set("Отчётов пока нет. Они появятся после первого обновления.")

    def _set(self, s):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", s)
        self.text.configure(state="disabled")

    def show(self):
        sel = self.lb.curselection()
        if sel:
            self._set(html_to_text(self.files[sel[0]].read_text("utf-8")))

    def open(self):
        sel = self.lb.curselection()
        if sel:
            open_file(self.files[sel[0]])
