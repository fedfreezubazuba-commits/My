"""
Читаемые человеком файлы настроек в рабочей папке:

  config/jobs.yaml      — показатели в обновлении (раздел 7 ТЗ)
  config/models.yaml    — подключённые модели и их подписки
  config/settings.yaml  — настройки приложения
  config/trash.yaml     — корзина: удалённые показатели (30 дней)

Запись атомарная: во временный файл, затем замена.
"""
import datetime as dt
import os
import re
import tempfile
import threading
import unicodedata
from pathlib import Path

import yaml

_lock = threading.RLock()

DEFAULT_SETTINGS = {
    "formula_locale": "ru",          # ru | en
    "notifications": True,
    "keep_raw_days": 90,
    "nightly_model_refresh": False,
    "per_job_files": False,
    "models_folder": "",
    "fail_threshold": 3,             # после стольких сбоев подряд — красный статус
    "job_timeout_sec": 600,
    "trash_days": 30,
    "first_run_done": False,
}

TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "j", "k", "l", "m", "n", "o", "p",
                     "r", "s", "t", "u", "f", "h", "c", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"]))


def slug(text, maxlen=30) -> str:
    """Латиница, цифры и подчёркивание: «Лосось Мурманск» → losos_murmansk."""
    s = "".join(TRANSLIT.get(c, c) for c in str(text).lower())
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s[:maxlen].strip("_") or "x"


def _read_yaml(path: Path, default):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            data = yaml.safe_load(fh)
    except FileNotFoundError:
        return default
    return default if data is None else data


def _write_yaml(path: Path, data, header=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    flow = False if isinstance(data, dict) else None   # настройки — столбиком, параметры показателей — в строку
    text = header + yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=flow,
                                   width=110)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp_", suffix=".yaml")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


JOBS_HEADER = ("# Показатели в обновлении. Файл можно править вручную, но проще — в программе.\n"
               "# id и коды рядов не меняйте: на них ссылаются модели.\n")
MODELS_HEADER = "# Подключённые модели и ряды, которые они получают. Порядок рядов не меняйте.\n"


class Config:
    def __init__(self, paths):
        self.paths = paths

    # --- настройки -----------------------------------------------------------
    def settings(self) -> dict:
        s = dict(DEFAULT_SETTINGS)
        s.update(_read_yaml(self.paths.settings, {}) or {})
        return s

    def save_settings(self, s: dict):
        with _lock:
            _write_yaml(self.paths.settings, s, "# Настройки Хаба данных\n")

    def update_settings(self, **kw):
        with _lock:
            s = self.settings()
            s.update(kw)
            self.save_settings(s)
            return s

    # --- показатели ----------------------------------------------------------
    def jobs(self) -> list[dict]:
        jobs = _read_yaml(self.paths.jobs, []) or []
        for j in jobs:
            j.setdefault("params", {})
            j.setdefault("schedule", "auto")
            j.setdefault("enabled", True)
            j.setdefault("checks", {})
            j.setdefault("tags", [])
            if "&" in j.get("name", ""):        # «&laquo;Строительство&raquo;» из старых названий ЕМИСС
                import html
                j["name"] = html.unescape(j["name"])
        return jobs

    def job(self, job_id) -> dict | None:
        return next((j for j in self.jobs() if j["id"] == job_id), None)

    def save_jobs(self, jobs):
        with _lock:
            _write_yaml(self.paths.jobs, jobs, JOBS_HEADER)

    def new_job_id(self, source, name) -> str:
        existing = {j["id"] for j in self.jobs()} | {j["id"] for j in self.trash()}
        base = f"{source}-{slug(name, 32)}"
        jid, n = base, 2
        while jid in existing:
            jid, n = f"{base}-{n}", n + 1
        return jid

    def add_job(self, source, name, params, schedule="auto", checks=None, tags=None, extra=None) -> dict:
        with _lock:
            job = {"id": self.new_job_id(source, name), "name": name, "source": source,
                   "params": params, "schedule": schedule or "auto", "enabled": True,
                   "checks": checks or {}, "tags": tags or [], "created": dt.date.today().isoformat()}
            job.update(extra or {})
            jobs = self.jobs()
            jobs.append(job)
            self.save_jobs(jobs)
            return job

    def update_job(self, job_id, **changes) -> dict:
        with _lock:
            jobs = self.jobs()
            for j in jobs:
                if j["id"] == job_id:
                    j.update(changes)
                    self.save_jobs(jobs)
                    return j
            raise KeyError(job_id)

    # --- корзина -------------------------------------------------------------
    def trash(self) -> list[dict]:
        return _read_yaml(self.paths.trash, []) or []

    def delete_job(self, job_id):
        """Убрать показатель в корзину (данные в базе остаются)."""
        with _lock:
            jobs = self.jobs()
            job = next((j for j in jobs if j["id"] == job_id), None)
            if not job:
                return None
            jobs.remove(job)
            trash = self.trash()
            job["deleted"] = dt.datetime.now().isoformat(timespec="seconds")
            trash.append(job)
            self.save_jobs(jobs)
            _write_yaml(self.paths.trash, trash, "# Корзина: удалённые показатели, хранятся 30 дней\n")
            return job

    def restore_job(self, job_id):
        with _lock:
            trash = self.trash()
            job = next((j for j in trash if j["id"] == job_id), None)
            if not job:
                return None
            trash.remove(job)
            job.pop("deleted", None)
            jobs = self.jobs()
            jobs.append(job)
            self.save_jobs(jobs)
            _write_yaml(self.paths.trash, trash, "# Корзина: удалённые показатели, хранятся 30 дней\n")
            return job

    def purge_trash(self, days=30, now=None) -> list[str]:
        now = now or dt.datetime.now()
        with _lock:
            trash = self.trash()
            keep, gone = [], []
            for j in trash:
                try:
                    old = now - dt.datetime.fromisoformat(j.get("deleted")) > dt.timedelta(days=days)
                except (TypeError, ValueError):
                    old = False
                (gone if old else keep).append(j)
            if gone:
                _write_yaml(self.paths.trash, keep, "# Корзина: удалённые показатели, хранятся 30 дней\n")
            return [j["id"] for j in gone]

    # --- модели --------------------------------------------------------------
    def models(self) -> list[dict]:
        models = _read_yaml(self.paths.models, []) or []
        for m in models:
            m.setdefault("jobs", [])
            m.setdefault("series", [])
            m.setdefault("exclude", [])
            m.setdefault("selections", [])
            m.setdefault("active", True)
        return models

    def model(self, name) -> dict | None:
        return next((m for m in self.models() if m["name"] == name), None)

    def save_models(self, models):
        with _lock:
            _write_yaml(self.paths.models, models, MODELS_HEADER)

    def add_model(self, name, path="", **extra) -> dict:
        with _lock:
            models = self.models()
            if any(m["name"] == name for m in models):
                raise ValueError(f"Модель «{name}» уже подключена")
            m = {"name": name, "path": str(path), "jobs": [], "series": [], "exclude": [],
                 "active": True, "connected": dt.date.today().isoformat()}
            m.update(extra)
            models.append(m)
            self.save_models(models)
            return m

    def update_model(self, name, **changes) -> dict:
        with _lock:
            models = self.models()
            for m in models:
                if m["name"] == name:
                    m.update(changes)
                    self.save_models(models)
                    return m
            raise KeyError(name)

    def subscribe(self, model_name, job_id=None, code=None):
        """Подписать модель на все ряды показателя (job_id) или на один ряд (code)."""
        with _lock:
            models = self.models()
            m = next((x for x in models if x["name"] == model_name), None)
            if not m:
                raise KeyError(model_name)
            if job_id and job_id not in m["jobs"]:
                m["jobs"].append(job_id)
            if code:
                if code in m["exclude"]:
                    m["exclude"].remove(code)
                if code not in m["series"]:
                    m["series"].append(code)
            self.save_models(models)

    def unsubscribe_code(self, model_name, code):
        self.unsubscribe_codes(model_name, [code], exclude=True)

    def unsubscribe_codes(self, model_name, codes, exclude=False):
        """Убрать ряды из модели одной записью файла (рядов могут быть тысячи).
        exclude=True — ряд не отдавать, даже если он входит в выборку или «весь показатель»."""
        drop = set(codes)
        with _lock:
            models = self.models()
            m = next((x for x in models if x["name"] == model_name), None)
            if not m:
                raise KeyError(model_name)
            m["series"] = [c for c in m["series"] if c not in drop]
            if exclude:
                have = set(m["exclude"])
                m["exclude"] += [c for c in dict.fromkeys(codes) if c not in have]
            self.save_models(models)

    def unexclude(self, model_name, codes):
        """Снова отдавать ряды (например, их явно выбрали в выборке)."""
        back = set(codes)
        with _lock:
            models = self.models()
            m = next((x for x in models if x["name"] == model_name), None)
            if m and back & set(m.get("exclude", [])):
                m["exclude"] = [c for c in m["exclude"] if c not in back]
                self.save_models(models)

    # --- выборки: какие ряды показателя и за какой период получает модель ---------
    def save_selection(self, model_name, sel: dict) -> dict:
        """Добавить или заменить выборку (по sel["id"]). Поля: job, name, filters {поле: [значения]},
        from / to (ГГГГ-ММ-ДД или пусто)."""
        with _lock:
            models = self.models()
            m = next((x for x in models if x["name"] == model_name), None)
            if not m:
                raise KeyError(model_name)
            if not sel.get("id"):
                used = {s.get("id") for s in m["selections"]}
                n = 1
                while f"s{n}" in used:
                    n += 1
                sel["id"] = f"s{n}"
            m["selections"] = [s for s in m["selections"] if s.get("id") != sel["id"]] + [sel]
            # выборка заменяет подписку «на весь показатель» (иначе в модель уйдёт всё)
            if sel.get("job") in m["jobs"]:
                m["jobs"].remove(sel["job"])
            self.save_models(models)
            return sel

    def remove_selection(self, model_name, sel_id):
        with _lock:
            models = self.models()
            m = next((x for x in models if x["name"] == model_name), None)
            if m:
                m["selections"] = [s for s in m["selections"] if s.get("id") != sel_id]
                self.save_models(models)

    def unsubscribe_job(self, model_name, job_id):
        with _lock:
            models = self.models()
            m = next((x for x in models if x["name"] == model_name), None)
            if m and job_id in m["jobs"]:
                m["jobs"].remove(job_id)
                self.save_models(models)

    def models_of_job(self, job_id) -> list[str]:
        return [m["name"] for m in self.models() if m.get("active", True) and job_id in m["jobs"]]
