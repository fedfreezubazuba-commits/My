"""
«Изменить набор данных» у показателя ЕМИСС или Банка России: навигатор открывает тот же показатель
с уже отмеченными значениями, кнопка «＋ В обновления» становится «✔ Сохранить изменения».
Показатель остаётся тем же (id, коды рядов, расписание, выборки моделей) — меняются только параметры.
"""
import datetime as dt
import tkinter as tk
from tkinter import messagebox, ttk

from ..core import impact
from . import widgets as W


class EditMode:
    """Примесь к навигатору: self.add_btn, self.status, self.app; навигатор вызывает _edit_bar(act),
    apply_pending(lists) после построения вкладок и save_or_add(params, name, add) из «＋ В обновления»."""

    editing = None          # показатель, набор данных которого меняем
    edit_pending = None     # {вкладка: [значения]} — отметить после загрузки показателя

    def _edit_bar(self, act):
        self.cancel_btn = ttk.Button(act, text="Отменить изменение", command=self.cancel_edit)

    def start_edit(self, job, pending):
        self.editing, self.edit_pending = job, pending
        self.add_btn.configure(text="✔ Сохранить изменения")
        self.cancel_btn.pack(side="right", padx=(6, 0))
        self.status.set(f"Меняете набор данных показателя «{job['name']}». Отметьте нужное и нажмите "
                        "«Сохранить изменения».")

    def cancel_edit(self):
        self.editing = self.edit_pending = None
        self.add_btn.configure(text="＋ В обновления")
        self.cancel_btn.pack_forget()
        self.status.set("Изменение набора данных отменено")

    def apply_pending(self, lists):
        """Отметить в списках значения из параметров показателя (поля, которых нет, — «всё»)."""
        if not self.edit_pending:
            return
        for key, want in self.edit_pending.items():
            if key not in lists or want is None:
                continue
            chosen, all_ids, tab, _t = lists[key]
            want = {str(x) for x in want}
            hit = [v for v in all_ids if str(v) in want]
            if hit:
                chosen.clear()
                chosen.update(hit)
                getattr(tab, "redraw", lambda: None)()
        self.edit_pending = None

    def save_or_add(self, params, name, add):
        if not self.editing:
            add()
            return
        job = self.app.cfg.job(self.editing["id"])
        if not job:
            self.cancel_edit()
            add()
            return
        new = {k: v for k, v in (job.get("params") or {}).items() if k not in params and k not in SELECTION_KEYS}
        new.update(params)
        if new == (job.get("params") or {}):
            messagebox.showinfo("Набор данных", "Ничего не изменилось.", parent=self)
            return
        dropped = impact.dropped_series(self.app.store, job, new, getattr(self, "info", None))
        codes = [r["code"] for r in dropped]
        models = impact.affected_models(self.app.cfg, self.app.store, codes) if codes else []
        answer = confirm_edit(self, job, dropped, models)
        if answer is None:
            return
        self.app.cfg.update_job(job["id"], params=new)
        if codes:
            self.app.store.retire(codes)               # сняты пользователем — не ждём, тревоги «исчезли ряды» не будет
            if answer:
                for name, _n in models:
                    self.app.cfg.unsubscribe_codes(name, codes, exclude=True)
        impact.mark_edited(self.app.store, job["id"], dt.datetime.now().isoformat(timespec="seconds"))
        self.cancel_edit()
        self.status.set(f"Набор данных показателя «{job['name']}» изменён — загружаю")
        self.app.update_jobs([job["id"]])


# параметры выбора: если в новом выборе их нет — значит, «всё» (из старых параметров их не переносим)
SELECTION_KEYS = {"select", "select_titles", "years_from", "indicators", "m1", "m2", "y1"}


def confirm_edit(parent, job, dropped, models):
    """Окно подтверждения: что перестанет обновляться и какие модели это затронет.
    → None — отмена; True — сохранить и убрать снятые ряды из моделей; False — сохранить, в моделях оставить."""
    w = tk.Toplevel(parent)
    w.title("Изменить набор данных")
    w.transient(parent.winfo_toplevel())
    body = ttk.Frame(w, padding=14)
    body.pack(fill="both", expand=True)
    lines = [f"Изменить набор данных показателя «{job['name']}»?",
             "Показатель, его расписание и выборки моделей останутся прежними. Следующая загрузка будет полной — "
             "у добавленных значений будет вся история."]
    if dropped:
        names = [r["name"] or r["code"] for r in dropped[:5]]
        lines.append(f"Перестанут обновляться рядов: {len(dropped)}, например:\n  • " + "\n  • ".join(names)
                     + ("\n  …" if len(dropped) > 5 else ""))
        lines.append("Их история сохранится в базе; хаб больше не будет их ждать и не будет предупреждать, что они "
                     "«исчезли».")
        if models:
            lines.append("Эти ряды сейчас получают модели: " +
                         ", ".join(f"«{m}» ({n})" for m, n in models) + ".")
    else:
        lines.append("Ряды, которые вы сняли, перестанут обновляться (их история сохранится). Хаб не смог заранее "
                     "посчитать, какие именно, — он поймёт это при следующей загрузке и не будет из-за них тревожить.")
    for t in lines:
        W.autowrap(ttk.Label(body, text=t, justify="left"), initial=560).pack(fill="x", pady=(0, 8))
    remove = tk.BooleanVar(value=False)
    if models:
        ttk.Radiobutton(body, variable=remove, value=False, text="Оставить их в моделях с прежними значениями "
                        "(формулы не сломаются, новых значений не будет)").pack(anchor="w")
        ttk.Radiobutton(body, variable=remove, value=True, text="Убрать их из моделей (после «Данные → Обновить "
                        "всё» в модели их не станет)").pack(anchor="w")
    result = {"v": None}
    bar = ttk.Frame(w, padding=(14, 0, 14, 14))
    bar.pack(fill="x")

    def ok():
        result["v"] = bool(remove.get())
        w.destroy()
    ttk.Button(bar, text="Сохранить изменения", style="Accent.TButton", command=ok).pack(side="right")
    ttk.Button(bar, text="Отмена", command=w.destroy).pack(side="right", padx=8)
    W.center(w, parent.winfo_toplevel())
    try:
        w.grab_set()
    except tk.TclError:
        pass
    if getattr(parent, "_auto_confirm", None) is not None:      # тесты: ответ без окна
        result["v"] = parent._auto_confirm
        w.destroy()
    else:
        w.wait_window()
    return result["v"]
