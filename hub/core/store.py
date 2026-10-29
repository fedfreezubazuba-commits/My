"""
Главное хранилище — SQLite (раздел 9.1 ТЗ).

Таблицы:
  series         — ряды: код, показатель, название, единицы, периодичность, URL
  observations   — наблюдения; state: current | old (пересмотренное) | quarantine | rejected
  runs           — запуски показателей
  job_state      — состояние показателя (последний успех, статус, ожидаемая дата…)
  subscriptions  — ряды моделей и их постоянный порядок столбцов
  model_state    — когда менялась посылка модели
  quarantine     — отложенные подозрительные данные
  meta           — служебные значения (например, когда последний раз работало обновление)

Коды рядов присваиваются один раз (таблица series) и больше не меняются.
"""
import datetime as dt
import hashlib
import json
import re
import sqlite3
import threading
from contextlib import contextmanager

from .config import slug

SCHEMA = """
CREATE TABLE IF NOT EXISTS series (
    code TEXT PRIMARY KEY, job_id TEXT NOT NULL, ident TEXT NOT NULL,
    name TEXT, unit TEXT, periodicity TEXT, source TEXT, url TEXT, date_rule TEXT,
    created TEXT, last_seen TEXT, UNIQUE(job_id, ident));
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL, period_end TEXT NOT NULL,
    period_label TEXT, value REAL, source_date TEXT, fetched_at TEXT NOT NULL,
    run_id INTEGER, state TEXT NOT NULL DEFAULT 'current', was_new INTEGER DEFAULT 0, qid INTEGER);
CREATE INDEX IF NOT EXISTS obs_code ON observations(code, state, period_end);
CREATE INDEX IF NOT EXISTS obs_q ON observations(qid);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, started TEXT, finished TEXT, status TEXT,
    category TEXT, message TEXT, new_periods INTEGER DEFAULT 0, revisions INTEGER DEFAULT 0,
    n_series INTEGER DEFAULT 0, raw_path TEXT, details TEXT, traceback TEXT);
CREATE INDEX IF NOT EXISTS runs_job ON runs(job_id, id);
CREATE TABLE IF NOT EXISTS job_state (
    job_id TEXT PRIMARY KEY, status TEXT, category TEXT, message TEXT,
    last_success TEXT, last_attempt TEXT, last_change TEXT, fail_count INTEGER DEFAULT 0,
    expected_next TEXT, n_series INTEGER, n_rows INTEGER, first_period TEXT, last_period TEXT,
    last_label TEXT, last_value REAL, last_unit TEXT, source_updated TEXT, warnings TEXT, fetch_rows INTEGER,
    release_next TEXT, release_note TEXT);
CREATE TABLE IF NOT EXISTS subscriptions (
    model TEXT NOT NULL, code TEXT NOT NULL, position INTEGER NOT NULL, active INTEGER DEFAULT 1,
    added TEXT, PRIMARY KEY(model, code));
CREATE TABLE IF NOT EXISTS model_state (
    name TEXT PRIMARY KEY, packet_changed_at TEXT, packet_hash TEXT, last_build TEXT, last_error TEXT);
CREATE TABLE IF NOT EXISTS quarantine (
    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, run_id INTEGER, created TEXT,
    reasons TEXT, status TEXT DEFAULT 'pending', decided TEXT);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

CODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
MAX_CODE = 40


def now_iso():
    return dt.datetime.now().isoformat(timespec="seconds")


def make_code(suggested: str, ident: str) -> str:
    """Код ряда: латиница, цифры, подчёркивание, до 40 символов, начинается с буквы."""
    s = re.sub(r"[^A-Za-z0-9_]+", "_", suggested or "").strip("_")
    s = re.sub(r"_+", "_", s)
    if not s or not s[0].isalpha():
        s = "S_" + s
    if len(s) > MAX_CODE:
        h = hashlib.sha1(ident.encode("utf-8")).hexdigest()[:6]
        s = s[:MAX_CODE - 7].rstrip("_") + "_" + h
    return s


def series_dims(row) -> list:
    """Разрезы ряда отдельными полями [(поле, значение)]; для старых рядов — одно поле «Название»."""
    raw = row["dims"] if "dims" in row.keys() else None
    if raw:
        try:
            return [tuple(x) for x in json.loads(raw)]
        except ValueError:
            pass
    return [("Название", row["name"] or "")]


def same_value(a, b) -> bool:
    if a is None or b is None:
        return a is b
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


class Store:
    def __init__(self, db_path):
        self.path = str(db_path)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, timeout=60, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript(SCHEMA)
            self._migrate()
            self.conn.commit()
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower():
                raise
            from .errors import FILE_BUSY, HubError
            raise HubError(FILE_BUSY, "База данных хаба занята другой копией программы — например, идёт фоновое "
                                      "обновление или зависла прошлая попытка запуска. Перезагрузите компьютер "
                                      "(или подождите несколько минут) и запустите хаб снова.", detail=str(e))

    def _migrate(self):
        """Новые столбцы в старых базах (база пользователя никогда не пересоздаётся)."""
        have = {r[1] for r in self.conn.execute("PRAGMA table_info(job_state)")}
        for col, typ in (("fetch_rows", "INTEGER"), ("release_next", "TEXT"), ("release_note", "TEXT")):
            if col not in have:
                self.conn.execute(f"ALTER TABLE job_state ADD COLUMN {col} {typ}")
        have = {r[1] for r in self.conn.execute("PRAGMA table_info(series)")}
        if "dims" not in have:
            self.conn.execute("ALTER TABLE series ADD COLUMN dims TEXT")
        if "retired" not in have:          # источник перестал давать ряд, пользователь согласился — не ждём его
            self.conn.execute("ALTER TABLE series ADD COLUMN retired TEXT")

    def close(self):
        self.conn.close()

    @contextmanager
    def tx(self):
        with self._lock:
            try:
                yield self.conn
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def q(self, sql, args=()):
        with self._lock:
            return self.conn.execute(sql, args).fetchall()

    def q1(self, sql, args=()):
        with self._lock:
            return self.conn.execute(sql, args).fetchone()

    # --- служебное -----------------------------------------------------------
    def meta_get(self, key, default=None):
        r = self.q1("SELECT value FROM meta WHERE key=?", (key,))
        return r["value"] if r else default

    def meta_set(self, key, value):
        with self.tx() as c:
            c.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (key, str(value)))

    # --- ряды ----------------------------------------------------------------
    def assign_code(self, job, info) -> str:
        """Код ряда по (показатель, идентичность ряда). Новый — только при первом появлении."""
        r = self.q1("SELECT code FROM series WHERE job_id=? AND ident=?", (job["id"], info.key))
        if r:
            return r["code"]
        base = make_code(info.code or f"{job['source']}_{slug(info.name)}".upper(), info.key)
        code, n = base, 2
        while self.q1("SELECT 1 FROM series WHERE lower(code)=lower(?)", (code,)):
            suffix = f"_{n}"
            code, n = base[:MAX_CODE - len(suffix)] + suffix, n + 1
        with self.tx() as c:
            c.execute("INSERT INTO series(code, job_id, ident, name, unit, periodicity, source, url, date_rule,"
                      " created) VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (code, job["id"], info.key, info.name, info.unit, info.periodicity, job["source"],
                       info.url, info.date_rule, now_iso()))
        return code

    def peek_code(self, job_id, ident):
        r = self.q1("SELECT code FROM series WHERE job_id=? AND ident=?", (job_id, ident))
        return r["code"] if r else None

    def series_of_job(self, job_id):
        return self.q("SELECT * FROM series WHERE job_id=? ORDER BY created, rowid", (job_id,))

    def series(self, code):
        return self.q1("SELECT * FROM series WHERE code=?", (code,))

    def all_series(self):
        return self.q("SELECT * FROM series ORDER BY job_id, created, rowid")

    def update_series_meta(self, code, info):
        dims = json.dumps(info.dims, ensure_ascii=False) if getattr(info, "dims", None) else None
        with self.tx() as c:
            c.execute("UPDATE series SET name=?, unit=?, periodicity=?, url=?, date_rule=?, last_seen=?, dims=?,"
                      " retired=NULL WHERE code=?", (info.name, info.unit, info.periodicity, info.url, info.date_rule,
                                                     now_iso(), dims, code))      # ряд снова пришёл — снова ждём

    # --- наблюдения ----------------------------------------------------------
    def current(self, code):
        """{period_end: (value, label)} — актуальные значения ряда."""
        return {r["period_end"]: (r["value"], r["period_label"]) for r in self.q(
            "SELECT period_end, value, period_label FROM observations WHERE code=? AND state='current'",
            (code,))}

    def current_rows(self, code):
        return self.q("SELECT * FROM observations WHERE code=? AND state='current' ORDER BY period_end",
                      (code,))

    def history(self, code, period_end=None):
        if period_end:
            return self.q("SELECT * FROM observations WHERE code=? AND period_end=? ORDER BY id",
                          (code, period_end))
        return self.q("SELECT * FROM observations WHERE code=? ORDER BY period_end, id", (code,))

    def diff(self, code, obs_list):
        """Что изменится: [(obs, old_value_or_None)] — только новые периоды и пересмотры."""
        cur = self.current(code)
        out = []
        for o in obs_list:
            key = o.period_end.isoformat()
            if key not in cur:
                out.append((o, None, True))
            elif not same_value(cur[key][0], o.value):
                out.append((o, cur[key][0], False))
        return out

    def apply(self, code, changes, run_id, fetched_at, initial=False, qid=None):
        """Записать изменения. qid — номер карантина: тогда данные не становятся актуальными."""
        with self.tx() as c:
            for o, _old, is_new in changes:
                key = o.period_end.isoformat()
                state = "quarantine" if qid else "current"
                if not qid:
                    c.execute("UPDATE observations SET state='old' WHERE code=? AND period_end=? AND state='current'",
                              (code, key))
                c.execute("INSERT INTO observations(code, period_end, period_label, value, source_date, fetched_at,"
                          " run_id, state, was_new, qid) VALUES(?,?,?,?,?,?,?,?,?,?)",
                          (code, key, o.period_label, o.value, o.source_date, fetched_at, run_id, state,
                           1 if (is_new and not initial) else 0, qid))

    def accept_quarantine(self, qid):
        """Принять отложенные значения (их могут быть сотни тысяч — одним пакетом)."""
        rows = self.q("SELECT code, period_end FROM observations WHERE qid=? AND state='quarantine'", (qid,))
        with self.tx() as c:
            c.executemany("UPDATE observations SET state='old' WHERE code=? AND state='current' AND period_end=?",
                          [(r["code"], r["period_end"]) for r in rows])
            c.execute("UPDATE observations SET state='current', fetched_at=? WHERE qid=? AND state='quarantine'",
                      (now_iso(), qid))
            c.execute("UPDATE quarantine SET status='accepted', decided=? WHERE id=?", (now_iso(), qid))
        return len(rows)

    def quarantine_counts(self, qid) -> tuple[int, int]:
        """(новых периодов, изменённых значений) в отложенной загрузке."""
        r = self.q1("SELECT SUM(CASE WHEN EXISTS (SELECT 1 FROM observations c WHERE c.code=o.code "
                    "AND c.state='current' AND c.period_end=o.period_end) THEN 0 ELSE 1 END) AS n_new, COUNT(*) AS n "
                    "FROM observations o WHERE o.qid=? AND o.state='quarantine'", (qid,))
        n_new = r["n_new"] or 0
        return n_new, (r["n"] or 0) - n_new

    def delete_series(self, codes) -> int:
        """Удалить ряды из хаба совсем: сам ряд, все его значения и место в подписках моделей (столбцы,
        которые пользователь отметил «Не брать»). → сколько рядов удалено."""
        codes = list(codes)
        if not codes:
            return 0
        with self.tx() as c:
            for i in range(0, len(codes), 500):
                part = codes[i:i + 500]
                marks = ",".join("?" * len(part))
                c.execute(f"DELETE FROM observations WHERE code IN ({marks})", part)
                c.execute(f"DELETE FROM subscriptions WHERE code IN ({marks})", part)
                c.execute(f"DELETE FROM series WHERE code IN ({marks})", part)
        return len(codes)

    def retire(self, codes) -> int:
        """Эти ряды больше не ждём (их сняли в наборе данных или источник перестал их давать)."""
        with self.tx() as c:
            c.executemany("UPDATE series SET retired=? WHERE code=? AND retired IS NULL",
                          [(now_iso(), code) for code in codes])
        return len(codes)

    def retire_lost(self, qid) -> int:
        """Принято «исчезли ряды»: ряды показателя, которых не было в ответе этой загрузки, больше не ждём —
        проверка не будет спрашивать о них снова. Их прежние значения остаются. → сколько рядов."""
        q = self.q1("SELECT q.job_id, q.reasons, r.started FROM quarantine q LEFT JOIN runs r ON r.id=q.run_id "
                    "WHERE q.id=?", (qid,))
        if not q or "Исчезли ряды" not in (q["reasons"] or ""):
            return 0
        saved = self.meta_get(f"lost:{qid}")
        with self.tx() as c:
            if saved:                       # список пропавших рядов записан при загрузке
                codes = json.loads(saved)
                c.executemany("UPDATE series SET retired=? WHERE code=? AND job_id=? AND retired IS NULL",
                              [(now_iso(), code, q["job_id"]) for code in codes])
                return len(codes)
            # отложено прежней версией: пропавшие — ряды, которых не было в ответе (не обновлялись с загрузки)
            since = q["started"] or self.q1("SELECT created FROM quarantine WHERE id=?", (qid,))["created"]
            cur = c.execute("UPDATE series SET retired=? WHERE job_id=? AND retired IS NULL "
                            "AND (last_seen IS NULL OR last_seen < ?)", (now_iso(), q["job_id"], since))
            return cur.rowcount

    def reject_quarantine(self, qid):
        with self.tx() as c:
            c.execute("UPDATE observations SET state='rejected' WHERE qid=? AND state='quarantine'", (qid,))
            c.execute("UPDATE quarantine SET status='rejected', decided=? WHERE id=?", (now_iso(), qid))

    def new_quarantine(self, job_id, run_id, reasons):
        with self.tx() as c:
            cur = c.execute("INSERT INTO quarantine(job_id, run_id, created, reasons) VALUES(?,?,?,?)",
                            (job_id, run_id, now_iso(), json.dumps(reasons, ensure_ascii=False)))
            return cur.lastrowid

    def pending_quarantine(self, job_id=None):
        if job_id:
            return self.q("SELECT * FROM quarantine WHERE status='pending' AND job_id=? ORDER BY id", (job_id,))
        return self.q("SELECT * FROM quarantine WHERE status='pending' ORDER BY id")

    def quarantine_rows(self, qid):
        return self.q("SELECT o.*, s.name, s.unit FROM observations o JOIN series s ON s.code=o.code"
                      " WHERE o.qid=? ORDER BY o.code, o.period_end", (qid,))

    def last_values(self, code, n=2):
        return self.q("SELECT * FROM observations WHERE code=? AND state='current' ORDER BY period_end DESC LIMIT ?",
                      (code, n))

    def last_period_ends(self, job_id) -> dict:
        """{код: последний период} всех рядов показателя одним запросом."""
        # по ряду — подзапрос: MAX по индексу (code, state, period_end) берётся сразу, без перебора всех значений
        return {r["code"]: r["e"] for r in self.q(
            "SELECT s.code, (SELECT MAX(o.period_end) FROM observations o WHERE o.code=s.code AND o.state='current')"
            " AS e FROM series s WHERE s.job_id=?", (job_id,)) if r["e"]}

    def first_seen(self, codes, period_end) -> str | None:
        """Когда хаб впервые получил данные за этот период по любому из рядов (время загрузки)."""
        codes = list(codes)[:200]
        if not codes:
            return None
        r = self.q1(f"SELECT MIN(fetched_at) AS f FROM observations WHERE period_end=? AND code IN "
                    f"({','.join('?' * len(codes))})", [period_end, *codes])
        return r["f"] if r else None

    def new_period_lags(self, code, limit=12):
        """Сколько дней проходит от конца периода до появления данных (по реальным обновлениям)."""
        rows = self.q("SELECT period_end, MIN(fetched_at) AS f FROM observations WHERE code=? AND was_new=1"
                      " GROUP BY period_end ORDER BY period_end DESC LIMIT ?", (code, limit))
        out = []
        for r in rows:
            try:
                out.append((dt.datetime.fromisoformat(r["f"]).date() - dt.date.fromisoformat(r["period_end"])).days)
            except ValueError:
                pass
        return out

    def window_stats(self, codes, a="", b=""):
        """{код: (число значений, последнее значение, подпись последнего периода)} в периоде [a; b]."""
        out = {}
        codes = list(codes)
        for i in range(0, len(codes), 400):
            part = codes[i:i + 400]
            marks = ",".join("?" * len(part))
            cond, args = "", list(part)
            if a:
                cond += " AND period_end>=?"
                args.append(a)
            if b:
                cond += " AND period_end<=?"
                args.append(b)
            for r in self.q(f"SELECT code, COUNT(*) n, MAX(period_end) last FROM observations WHERE state='current'"
                            f" AND code IN ({marks}){cond} GROUP BY code", args):
                out[r["code"]] = [r["n"], None, r["last"]]
        for code, v in out.items():
            r = self.q1("SELECT value, period_label FROM observations WHERE code=? AND state='current' AND period_end=?",
                        (code, v[2]))
            if r:
                v[1], v[2] = r["value"], r["period_label"]
        return {k: tuple(v) for k, v in out.items()}

    def changed_since(self, codes, since_iso):
        if not codes:
            return []
        marks = ",".join("?" * len(codes))
        return [r["code"] for r in self.q(
            f"SELECT DISTINCT code FROM observations WHERE state='current' AND fetched_at>? AND code IN ({marks})",
            (since_iso, *codes))]

    # --- запуски и состояние -------------------------------------------------
    def start_run(self, job_id):
        with self.tx() as c:
            return c.execute("INSERT INTO runs(job_id, started, status) VALUES(?,?,?)",
                             (job_id, now_iso(), "running")).lastrowid

    def finish_run(self, run_id, **kw):
        kw.setdefault("finished", now_iso())
        if "details" in kw and not isinstance(kw["details"], str):
            kw["details"] = json.dumps(kw["details"], ensure_ascii=False, default=str)
        cols = ", ".join(f"{k}=?" for k in kw)
        with self.tx() as c:
            c.execute(f"UPDATE runs SET {cols} WHERE id=?", (*kw.values(), run_id))

    def runs(self, job_id=None, limit=50):
        if job_id:
            return self.q("SELECT * FROM runs WHERE job_id=? ORDER BY id DESC LIMIT ?", (job_id, limit))
        return self.q("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))

    def state(self, job_id) -> dict:
        r = self.q1("SELECT * FROM job_state WHERE job_id=?", (job_id,))
        return dict(r) if r else {"job_id": job_id, "fail_count": 0}

    def states(self) -> dict:
        return {r["job_id"]: dict(r) for r in self.q("SELECT * FROM job_state")}

    def set_state(self, job_id, **kw):
        kw = {k: v for k, v in kw.items() if k != "job_id"}
        with self.tx() as c:
            c.execute("INSERT OR IGNORE INTO job_state(job_id) VALUES(?)", (job_id,))
            if kw:
                cols = ", ".join(f"{k}=?" for k in kw)
                c.execute(f"UPDATE job_state SET {cols} WHERE job_id=?", (*kw.values(), job_id))

    def refresh_summary(self, job_id):
        """Пересчитать объём показателя: число рядов, диапазон дат, последнее значение."""
        codes = [r["code"] for r in self.series_of_job(job_id)]
        if not codes:
            return
        marks = ",".join("?" * len(codes))
        r = self.q1(f"SELECT MIN(period_end) a, MAX(period_end) b, COUNT(*) n FROM observations"
                    f" WHERE state='current' AND code IN ({marks})", codes)
        last = self.q1(f"SELECT o.value, o.period_label, o.period_end, s.unit FROM observations o JOIN series s"
                       f" ON s.code=o.code WHERE o.state='current' AND o.code IN ({marks})"
                       f" ORDER BY o.period_end DESC, s.created, s.rowid LIMIT 1", codes)
        n_series = self.q1(f"SELECT COUNT(DISTINCT code) n FROM observations WHERE state='current'"
                           f" AND code IN ({marks})", codes)["n"]
        self.set_state(job_id, n_series=n_series, n_rows=r["n"], first_period=r["a"], last_period=r["b"],
                       last_value=last["value"] if last else None,
                       last_label=last["period_label"] if last else None,
                       last_unit=last["unit"] if last else None)

    # --- подписки ------------------------------------------------------------
    def sync_subscriptions(self, model_name, wanted_codes):
        """Порядок столбцов присваивается один раз: новые ряды получают позицию справа."""
        rows = {r["code"]: r for r in self.q("SELECT * FROM subscriptions WHERE model=?", (model_name,))}
        top = max((r["position"] for r in rows.values()), default=0)
        wanted = list(dict.fromkeys(wanted_codes))
        with self.tx() as c:
            for code in wanted:
                if code not in rows:
                    top += 1
                    c.execute("INSERT INTO subscriptions(model, code, position, active, added) VALUES(?,?,?,1,?)",
                              (model_name, code, top, now_iso()))
                elif not rows[code]["active"]:
                    c.execute("UPDATE subscriptions SET active=1 WHERE model=? AND code=?", (model_name, code))
            for code, r in rows.items():
                if r["active"] and code not in wanted:
                    c.execute("UPDATE subscriptions SET active=0 WHERE model=? AND code=?", (model_name, code))
        return self.subscribed(model_name)

    def subscribed(self, model_name):
        return [r["code"] for r in self.q("SELECT code FROM subscriptions WHERE model=? AND active=1"
                                          " ORDER BY position", (model_name,))]

    def model_state(self, name) -> dict:
        r = self.q1("SELECT * FROM model_state WHERE name=?", (name,))
        return dict(r) if r else {"name": name}

    def set_model_state(self, name, **kw):
        with self.tx() as c:
            c.execute("INSERT OR IGNORE INTO model_state(name) VALUES(?)", (name,))
            cols = ", ".join(f"{k}=?" for k in kw)
            c.execute(f"UPDATE model_state SET {cols} WHERE name=?", (*kw.values(), name))
