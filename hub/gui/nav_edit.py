"""
«Изменить набор данных» у показателя ЕМИСС или Банка России: навигатор открывает тот же показатель
с уже отмеченными значениями, кнопка «＋ В обновления» становится «✔ Сохранить изменения».
Показатель остаётся тем же (id, коды рядов, расписание, выборки моделей) — меняются только параметры.
"""
from tkinter import messagebox, ttk


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
        if not messagebox.askyesno(
                "Сохранить изменения", f"Изменить набор данных показателя «{job['name']}»?\n\n"
                "Показатель, его расписание и выборки моделей останутся прежними. Новые ряды появятся после "
                "загрузки, ряды, которые вы сняли, больше не будут обновляться (их история сохранится).", parent=self):
            return
        self.app.cfg.update_job(job["id"], params=new)
        self.cancel_edit()
        self.status.set(f"Набор данных показателя «{job['name']}» изменён — загружаю")
        self.app.update_jobs([job["id"]])


# параметры выбора: если в новом выборе их нет — значит, «всё» (из старых параметров их не переносим)
SELECTION_KEYS = {"select", "years_from", "indicators", "m1", "m2", "y1"}
