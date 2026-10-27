"""
Фоновое обновление (раздел 10 ТЗ). Вызывается планировщиком Windows каждые 15 минут и при входе
в систему, а также кнопками «Обновить сейчас» / «Обновить всё сейчас».

  1. блокировка — два прохода не идут одновременно;
  2. выбор показателей, у которых наступил срок;
  3. загрузка: к одному хосту — строго по очереди с паузами, разные хосты — параллельно,
     у каждого показателя тайм-аут; сбой одного показателя не мешает остальным;
  4. проверки → запись в базу или карантин;
  5. пересборка посылок моделей (занятый файл — повтор в следующий раз, данные уже в базе);
  6. отчёт и уведомление, если есть что сказать.
"""
import copy
import datetime as dt
import json
import os
import queue
import threading
import time
import traceback
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from . import VERSION, sources
from .core import errors as E
from .core import hostblock as HB
from .core import schedule as S
from .core.checks import run_checks
from .core.config import Config
from .core.log import get_logger
from .core.paths import Paths
from .core.store import Store, now_iso
from .excel import packet

HOST_PAUSE = {"www.fedstat.ru": 3.0, "www.cbr.ru": 1.0}
DEFAULT_PAUSE = 2.0
MAX_HOSTS = 4
STALE_LOCK_SEC = 3 * 3600


class Busy(Exception):
    pass


def default_browser(paths):
    """Встроенный браузер для страниц со скриптами и входом (если установлен Playwright)."""
    try:
        from .sources.browser import Browser
        return Browser(paths) if Browser.available() else None
    except Exception:  # noqa: BLE001
        return None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)            # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ctypes.GetLastError() == 5             # «нет доступа» — процесс есть, но чужой
        try:
            code = ctypes.c_ulong()
            return bool(k.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259    # STILL_ACTIVE
        finally:
            k.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def boot_time() -> float | None:
    """Когда включили компьютер (секунды с 1970) — замок старше включения точно брошен."""
    try:
        if os.name == "nt":
            import ctypes
            ticks = ctypes.windll.kernel32.GetTickCount64
            ticks.restype = ctypes.c_ulonglong
            return time.time() - ticks() / 1000
        with open("/proc/uptime") as fh:
            return time.time() - float(fh.read().split()[0])
    except Exception:  # noqa: BLE001
        return None


class Lock:
    """Файл-замок runner.lock: создаётся атомарно. Брошенным считается замок, если процесса, который его
    поставил, уже нет, если он старше включения компьютера (выключили посреди обновления) или старше 3 часов."""

    def __init__(self, path):
        self.path = path
        self.held = False

    def _abandoned(self) -> bool:
        try:
            age = time.time() - os.path.getmtime(self.path)
            text = open(self.path, encoding="utf-8", errors="replace").read()
        except OSError:
            return True
        if age > STALE_LOCK_SEC:
            return True
        boot = boot_time()
        if boot and os.path.getmtime(self.path) < boot - 60:
            return True
        try:
            pid = int(text.split()[0])
        except (IndexError, ValueError):
            return age > 60                 # замок ещё пишется или испорчен
        return not _pid_alive(pid)

    def __enter__(self):
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"{os.getpid()} {now_iso()}".encode())
                os.close(fd)
                self.held = True
                return self
            except FileExistsError:
                if self._abandoned():
                    try:
                        os.remove(self.path)
                    except OSError:
                        pass
                    continue
                raise Busy("Обновление уже идёт")
        raise Busy("Обновление уже идёт")

    def __exit__(self, *exc):
        if self.held:
            try:
                os.remove(self.path)
            except OSError:
                pass


class JobOutcome:
    def __init__(self, job):
        self.job = job
        self.status = None          # updated | unchanged | waiting | quarantine | error | retry | login | blocked
        self.category = None
        self.message = ""
        self.new_periods = []       # [(код, подпись, значение)]
        self.revisions = []         # [(код, подпись, было, стало)]
        self.problems = []
        self.warnings = []
        self.summary = ""
        self.traceback = ""
        self.run_id = None
        self.raw_path = None
        self.blocked = None         # (сайт, пауза до) — сайт не пускает хаб
        self.sheets = []            # листы файла, обновлённые для моделей (file_sheets)

    @property
    def changed(self):
        return bool(self.new_periods or self.revisions or self.sheets)


def blocking(exc) -> bool:
    """Сайт не пускает хаб (403, 429, проверка «я не робот») — пауза по всему сайту (hostblock).
    503 «сайт перегружен» — обычный временный сбой."""
    code = getattr(exc, "code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    return E.classify(exc) == E.BOT or code == 429


def timing_note_path(paths, job_id):
    """Куски страницы источника о сроках обновления (если хаб не смог их разобрать) — для «Отправить Claude»."""
    return paths.cache / "сроки" / f"{job_id}.txt"


class RunReport:
    def __init__(self):
        self.started = dt.datetime.now()
        self.finished = None
        self.outcomes = []
        self.packets = {}
        self.models_new = []
        self.job_files = {}
        self.purged = []
        self.notes = []

    def by_status(self, *st):
        return [o for o in self.outcomes if o.status in st]


class Runner:
    def __init__(self, paths=None, ctx_clients=None, log=None, now=None, browser=None):
        self.paths = (paths or Paths()).ensure()
        self.cfg = Config(self.paths)
        self.store = Store(self.paths.db)
        self.log = log or get_logger(self.paths)
        self.clients = ctx_clients or {}
        self.now = now
        self.browser = browser if browser is not None else default_browser(self.paths)

    def close(self):
        self.store.close()

    def context(self, dry_run=False):
        ctx = sources.Context(paths=self.paths, log=self.log, settings=self.cfg.settings(),
                              clients=self.clients, browser=self.browser, now=self.now or dt.datetime.now(),
                              dry_run=dry_run)
        ctx.interactive = getattr(self, "interactive", False)
        return ctx

    # --- выбор показателей ---------------------------------------------------
    def due_jobs(self, now=None):
        now = now or self.now or dt.datetime.now()
        out = []
        for job in self.cfg.jobs():
            try:
                src = sources.get(job["source"])
            except KeyError:
                continue
            if HB.until(self.store, src.host(job.get("params") or {}), now):
                continue                     # сайт не пускает хаб — пауза по всему сайту
            st = self.store.state(job["id"])
            exp, per = S.expected_next(self.store, job)
            if S.is_due(job, st, now, snapshot=src.snapshot, periodicity=per):
                out.append(job)
        return out

    # --- одна загрузка -------------------------------------------------------
    def fetch_with_timeout(self, src, job, ctx, timeout):
        box = {}

        def work():
            try:
                box["result"] = src.fetch(job.get("params") or {}, ctx)
            except BaseException as e:  # noqa: BLE001
                box["error"] = e
                box["tb"] = traceback.format_exc()

        t = threading.Thread(target=work, daemon=True, name=f"fetch-{job['id']}")
        t.start()
        t.join(timeout)
        if t.is_alive():
            raise E.HubError(E.NETWORK, f"Источник не ответил за {timeout // 60} мин")
        if "error" in box:
            err = box["error"]
            err.hub_traceback = box.get("tb", "")
            raise err
        return box["result"]

    def check(self, job):
        """Пробная загрузка без записи: (FetchResult, проблемы проверок)."""
        src = sources.get(job["source"])
        res = src.fetch(job.get("params") or {}, self.context(dry_run=True))
        codes = {s.key: self.store.peek_code(job["id"], s.key) or s.code for s in res.series}
        return res, run_checks(job, res, self.store, codes)

    def _save_raw(self, job, res):
        if not res.raw:
            return None
        day = dt.date.today().isoformat()
        d = self.paths.raw / job["id"]
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{day}_{dt.datetime.now():%H%M%S}.{res.raw_ext or 'bin'}"
        p.write_bytes(res.raw)
        return str(p)

    def job_context(self, job, ctx, progress=None):
        """Контекст одного показателя: последний загруженный период (для дозагрузки только свежих лет)
        и нужна ли полная перезагрузка (раз в N дней — чтобы поймать пересмотры старых данных)."""
        jctx = copy.copy(ctx)
        state = self.store.state(job["id"])
        try:
            jctx.last_period = dt.date.fromisoformat(state["last_period"]) if state.get("last_period") else None
        except ValueError:
            jctx.last_period = None
        last_full = self.store.meta_get(f"full:{job['id']}")
        days = self.cfg.full_reload_days(job)      # из «Настроек» или свой у показателя; 0 — никогда
        try:
            jctx.full_reload = not last_full or days > 0 and (
                dt.datetime.now() - dt.datetime.fromisoformat(last_full)).days >= days
        except ValueError:
            jctx.full_reload = True
        jctx.progress = progress
        return jctx

    def fetch_job(self, job, ctx, use_cheap_check=True, progress=None):
        """Загрузка (в рабочем потоке): ('fetched', FetchResult) | ('no_change', дата) | ('error', exc)."""
        src = sources.get(job["source"])
        params = job.get("params") or {}
        state = self.store.state(job["id"])
        ctx = self.job_context(job, ctx, progress)
        timeout = max(int(ctx.settings.get("job_timeout_sec", 600)), int(src.timeout(params) or 0))
        try:
            # источник сообщает дату обновления (ЦБ) — если она не изменилась, не скачиваем при любом расписании;
            # «Обновить сейчас» (use_cheap_check=False) скачивает всегда
            if use_cheap_check and not src.snapshot and state.get("source_updated") \
                    and state.get("status") not in ("error", "retry", "new", "blocked", None):
                try:
                    upd = src.check_updated(params, ctx)
                except Exception as e:  # noqa: BLE001 — дешёвая проверка не обязательна
                    self.log.info("Проверка свежести %s не удалась: %s", job["id"], e)
                    upd = None
                if upd and str(upd) == str(state.get("source_updated")):
                    return "no_change", upd
            return "fetched", self.fetch_with_timeout(src, job, ctx, timeout)
        except BaseException as e:  # noqa: BLE001
            return "error", e

    def apply(self, job, kind, payload, initial_hint=False) -> JobOutcome:
        """Запись результата (в главном потоке)."""
        out = JobOutcome(job)
        now = dt.datetime.now()
        run_id = self.store.start_run(job["id"])
        out.run_id = run_id
        state = self.store.state(job["id"])
        fail_threshold = int(self.cfg.settings().get("fail_threshold", 3))
        if kind == "error":
            exc = payload
            cat = E.classify(exc)
            fails = (state.get("fail_count") or 0) + 1
            out.category = cat
            out.message = E.human_message(exc)
            out.traceback = getattr(exc, "hub_traceback", "") or "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__))
            if getattr(exc, "raw_text", None):     # страница, на которой не нашлось нужного, — для «Отправить Claude»
                from .sources.base import FetchResult
                out.raw_path = self._save_raw(job, FetchResult(raw=exc.raw_text.encode("utf-8"), raw_ext="html"))
            if blocking(exc):
                host = sources.get(job["source"]).host(job.get("params") or {})
                till = HB.block(self.store, host, out.message)
                out.blocked = (host, till)
                out.message = (f"{out.message}. Хаб не будет обращаться к сайту {HB.title(host)} до "
                               f"{till:%H:%M %d.%m} и попробует сам")
            if cat == E.LOGIN:
                out.status = "login"
            elif out.blocked:
                out.status = "blocked"
            elif cat in E.TRANSIENT and fails < fail_threshold:
                out.status = "retry"
            else:
                out.status = "error"
            self.store.finish_run(run_id, status=out.status, category=cat, message=out.message,
                                  traceback=out.traceback[-8000:], raw_path=out.raw_path)
            self.store.set_state(job["id"], status=out.status, category=cat, message=out.message,
                                 last_attempt=now.isoformat(timespec="seconds"), fail_count=fails)
            self.log.warning("Показатель %s: %s (%s)", job["id"], out.message, cat)
            return out

        if kind == "fetched" and getattr(payload, "unchanged", False) and not payload.obs:
            kind, payload = "no_change", None
        if kind == "no_change":
            exp, per = S.expected_next(self.store, job)
            out.status = "delayed" if S.is_delayed(exp, per, now) else "waiting"
            out.category = E.NO_NEW_DATA
            out.message = "Новых данных ещё нет"
            self.store.finish_run(run_id, status=out.status, category=E.NO_NEW_DATA, message=out.message)
            self.store.set_state(job["id"], status=out.status, category=None, message=out.message,
                                 last_attempt=now.isoformat(timespec="seconds"), fail_count=0,
                                 expected_next=exp.isoformat(timespec="seconds") if exp else None)
            return out

        res = payload
        if getattr(res, "sheets", None) is not None:     # листы файла в модель — без разбора в базу
            return self._apply_sheets(job, res, run_id, now, out, state)
        out.warnings = list(res.warnings)
        HB.clear(self.store, sources.get(job["source"]).host(job.get("params") or {}))   # сайт снова пускает
        if res.next_release or res.release_note:     # срок следующего обновления, который сообщил источник
            self.store.set_state(job["id"], release_next=res.next_release, release_note=res.release_note or None)
        if getattr(res, "timing_text", ""):            # срок не разобрали — сохраняем куски страницы для Claude
            note = timing_note_path(self.paths, job["id"])
            note.parent.mkdir(parents=True, exist_ok=True)
            note.write_text(res.timing_text, encoding="utf-8")
        out.raw_path = self._save_raw(job, res)
        initial = not self.store.series_of_job(job["id"]) or initial_hint
        codes = {s.key: self.store.peek_code(job["id"], s.key) or s.code for s in res.series}
        problems = run_checks(job, res, self.store, codes, today=(self.now or now).date())
        if problems and initial and not any(p.startswith(("Значение не число", "Дата из будущего")) for p in problems):
            problems = []   # первая загрузка: сравнивать не с чем
        if not res.obs and initial:
            out.status, out.category = "error", E.STRUCTURE
            out.message = "Источник не вернул ни одного значения — проверьте параметры показателя"
            self.store.finish_run(run_id, status="error", category=E.STRUCTURE, message=out.message,
                                  raw_path=out.raw_path)
            self.store.set_state(job["id"], status="error", category=E.STRUCTURE, message=out.message,
                                 last_attempt=now.isoformat(timespec="seconds"),
                                 fail_count=(state.get("fail_count") or 0) + 1)
            return out
        fetched_at = now.isoformat(timespec="seconds")
        qid = self.store.new_quarantine(job["id"], run_id, problems) if problems else None
        if qid and getattr(res, "lost_codes", None):    # какие ряды пропали — чтобы при «Принять» больше их не ждать
            self.store.meta_set(f"lost:{qid}", json.dumps(res.lost_codes))
        if getattr(res, "retire_codes", None):            # после изменения набора данных — сняты пользователем
            self.store.retire(res.retire_codes)
            out.warnings.append(f"После изменения набора данных не приходят {len(res.retire_codes)} рядов — "
                                "хаб их больше не ждёт (история сохранена)")
        if not getattr(res, "partial", False) and not qid:
            self.store.meta_set(f"edited:{job['id']}", "")    # полная загрузка после изменения прошла
        by_key, upto = {}, None
        for o in res.obs:
            if o.value is not None:
                by_key.setdefault(o.key, []).append(o)
        for s in res.series:
            if s.key not in by_key:
                continue
            code = self.store.assign_code(job, s)
            self.store.update_series_meta(code, s)
            changes = self.store.diff(code, by_key[s.key])
            if not changes:
                continue
            self.store.apply(code, changes, run_id, fetched_at, initial=initial, qid=qid)
            for o, old, is_new in changes:
                if is_new and (upto is None or o.period_end > upto[0]):
                    upto = (o.period_end, o.period_label)       # по какой период пришли данные («Обзор»)
                if is_new:
                    out.new_periods.append((code, o.period_label, o.value))
                else:
                    out.revisions.append((code, o.period_label, old, o.value))
        out.summary = res.describe()
        if qid:
            out.status, out.category = "quarantine", E.CHECK_FAILED
            out.problems = problems
            out.message = "; ".join(problems)
        else:
            self.store.refresh_summary(job["id"])
            exp, per = S.expected_next(self.store, job)
            if out.changed:
                out.status = "updated"
            elif sources.get(job["source"]).snapshot:
                out.status = "unchanged"
            else:
                out.status = "delayed" if S.is_delayed(exp, per, now) else "waiting"
            out.message = out.summary
            upd = {"last_success": fetched_at, "expected_next": exp.isoformat(timespec="seconds") if exp else None}
            if out.changed:
                upd["last_change"] = fetched_at
            self.store.set_state(job["id"], **upd)
            if not getattr(res, "partial", False):
                self.store.meta_set(f"full:{job['id']}", fetched_at)
        if not getattr(res, "partial", False):
            self.store.set_state(job["id"], fetch_rows=sum(1 for o in res.obs if o.value is not None))
        self.store.set_state(job["id"], status=out.status, category=out.category, message=out.message,
                             last_attempt=fetched_at, fail_count=0,
                             source_updated=str(res.source_updated) if res.source_updated else None,
                             warnings="\n".join(out.warnings) or None)
        self.store.finish_run(run_id, status=out.status, category=out.category, message=out.message,
                              new_periods=len(out.new_periods), revisions=len(out.revisions),
                              n_series=len(res.series), raw_path=out.raw_path,
                              details={"upto": upto[1] if upto else None,
                                       "new": out.new_periods[:200], "revisions": out.revisions[:200],
                                       "problems": problems, "warnings": out.warnings})
        self.log.info("Показатель %s: %s (новых периодов %d, пересмотров %d)", job["id"], out.status,
                      len(out.new_periods), len(out.revisions))
        return out

    def _apply_sheets(self, job, res, run_id, now, out, state):
        """Листы файла: записать в «Листы/», если содержимое изменилось; история — в runs (для «Хаб_Обновления»)."""
        from .core import sheets as SH
        HB.clear(self.store, sources.get(job["source"]).host(job.get("params") or {}))
        fetched_at = now.isoformat(timespec="seconds")
        out.raw_path = self._save_raw(job, res)
        try:
            summary = SH.save(self.paths, job, res.sheets, fetched_at)
        except E.FileBusy as exc:            # файл листа открыт (например, в Excel) — повтор позже
            out.status, out.category, out.message = "retry", E.FILE_BUSY, E.human_message(exc)
            self.store.finish_run(run_id, status=out.status, category=out.category, message=out.message)
            self.store.set_state(job["id"], status=out.status, category=out.category, message=out.message,
                                 last_attempt=fetched_at, fail_count=(state.get("fail_count") or 0) + 1)
            return out
        if summary is None:
            out.status, out.message = "unchanged", "Файл не изменился"
            self.store.finish_run(run_id, status=out.status, category=E.NO_NEW_DATA, message=out.message,
                                  raw_path=out.raw_path)
            self.store.set_state(job["id"], status=out.status, category=None, message=out.message,
                                 last_attempt=fetched_at, last_success=fetched_at, fail_count=0)
            return out
        out.sheets = summary
        out.status = "updated"
        added = (job.get("params") or {}).get("mode") == "append"
        parts = [f"«{x['name']}» — {x['rows']} строк" + (f", файл от {x['file_date'][8:10]}.{x['file_date'][5:7]}."
                                                          f"{x['file_date'][:4]}" if x["file_date"] else "")
                 for x in summary]
        out.message = out.summary = ("Добавлено обновление листов: " if added else "Обновлены листы: ") + "; ".join(parts)
        fdate = max((x["file_date"] for x in summary if x["file_date"]), default="") or fetched_at[:10]
        self.store.meta_set(f"sheets_changed:{job['id']}", fetched_at)
        self.store.finish_run(run_id, status=out.status, message=out.message, n_series=len(summary),
                              new_periods=sum(x["rows"] for x in summary),       # для «Новые данные» на «Обзоре»
                              raw_path=out.raw_path, details={"sheets": summary})
        self.store.set_state(job["id"], status=out.status, category=None, message=out.message, fail_count=0,
                             last_attempt=fetched_at, last_success=fetched_at, last_change=fetched_at,
                             n_series=len(summary), n_rows=sum(x["rows"] for x in summary),
                             first_period=state.get("first_period") or fdate, last_period=fdate,
                             last_label=f"файл от {fdate[8:10]}.{fdate[5:7]}.{fdate[:4]}", warnings=None)
        self.log.info("Показатель %s: листы обновлены (%s)", job["id"], ", ".join(x["name"] for x in summary))
        return out

    # --- проход --------------------------------------------------------------
    def run(self, job_ids=None, force=False, scheduled=False, progress=None, write_report=True) -> RunReport:
        report = RunReport()
        self.interactive = not scheduled         # по кнопке человек рядом — ему можно показать окно проверки
        with Lock(self.paths.lock):
            self.store.meta_set("runner_heartbeat", now_iso())
            if scheduled:
                self.store.meta_set("scheduler_heartbeat", now_iso())     # планировщик Windows работает
            settings = self.cfg.settings()
            report.purged = self.cfg.purge_trash(int(settings.get("trash_days", 30)))
            all_jobs = self.cfg.jobs()
            if job_ids:
                jobs = [j for j in all_jobs if j["id"] in job_ids]
            elif force:
                jobs = [j for j in all_jobs if j.get("enabled", True)]
            else:
                jobs = self.due_jobs()
            self.log.info("Проход: %d показателей (force=%s, scheduled=%s)", len(jobs), force, scheduled)
            if not jobs and scheduled and not self._rebuild_overdue():
                return report        # по расписанию ничего не пора проверять — частый запуск ничего не стоит
            self._run_jobs(jobs, report, progress, cheap=not (force or job_ids))
            report.packets = packet.rebuild(self.cfg, self.store, self.paths, log=self.log)
            self.store.meta_set("packets_built", now_iso())
            from .excel.models import models_with_new_data
            report.models_new = models_with_new_data(self.cfg, self.store)
            if settings.get("per_job_files"):
                from .excel import exports
                changed_jobs = [o.job for o in report.outcomes if o.changed]
                report.job_files = exports.update_job_files(self.cfg, self.store, self.paths, changed_jobs)
            if settings.get("nightly_model_refresh") and scheduled and 1 <= dt.datetime.now().hour <= 5:
                report.notes += self._nightly_models()
            report.finished = dt.datetime.now()
            self.store.meta_set("runner_last_finished", now_iso())
            self._purge_raw(int(settings.get("keep_raw_days", 90) or 90))
            if write_report and report.outcomes:
                from .core import report as R
                report.path = R.write(self, report)
            if report.outcomes:
                from .core import notify
                notify.after_run(self, report)
        return report

    def _rebuild_overdue(self) -> bool:
        """Посылки пересобираются и в пустом проходе, но не чаще раза в час."""
        last = self.store.meta_get("packets_built")
        try:
            return not last or dt.datetime.now() - dt.datetime.fromisoformat(last) >= dt.timedelta(hours=1)
        except ValueError:
            return True

    def _purge_raw(self, days):
        """Сырые ответы старше N дней удаляются (последний ответ показателя сохраняется всегда)."""
        limit = time.time() - days * 86400
        if not self.paths.raw.exists():
            return
        for d in self.paths.raw.iterdir():
            files = sorted(d.glob("*")) if d.is_dir() else []
            for f in files[:-1]:
                try:
                    if f.stat().st_mtime < limit:
                        f.unlink()
                except OSError:
                    pass

    def _run_jobs(self, jobs, report, progress, cheap):
        if not jobs:
            return
        ctx = self.context()
        by_host = OrderedDict()
        for j in jobs:
            try:
                host = sources.get(j["source"]).host(j.get("params") or {})
            except KeyError:
                o = JobOutcome(j)
                o.status, o.category, o.message = "error", E.INTERNAL, f"Неизвестный источник «{j['source']}»"
                report.outcomes.append(o)
                continue
            by_host.setdefault(host, []).append(j)
        results = queue.Queue()

        def host_worker(host, host_jobs):
            pause = HOST_PAUSE.get(host, DEFAULT_PAUSE)
            for i, job in enumerate(host_jobs):
                if i:
                    time.sleep(pause)
                if progress:
                    progress(f"Загружаю: {job['name']}")
                kind, payload = self.fetch_job(job, ctx, use_cheap_check=cheap, progress=progress)
                results.put((job, kind, payload))
                if kind == "error" and blocking(payload):
                    break        # сайт не пускает — остальные показатели этого сайта не трогаем

        with ThreadPoolExecutor(max_workers=min(MAX_HOSTS, len(by_host)) or 1) as ex:
            futures = [ex.submit(host_worker, h, js) for h, js in by_host.items()]
            done = 0
            total = sum(len(js) for js in by_host.values())
            while done < total:
                try:
                    job, kind, payload = results.get(timeout=1)
                except queue.Empty:
                    if all(f.done() for f in futures) and results.empty():
                        break
                    continue
                done += 1
                try:
                    report.outcomes.append(self.apply(job, kind, payload))
                except Exception as e:  # noqa: BLE001 — сбой записи одного показателя не мешает остальным
                    o = JobOutcome(job)
                    o.status, o.category, o.message = "error", E.INTERNAL, E.human_message(e)
                    o.traceback = traceback.format_exc()
                    self.log.exception("Ошибка записи показателя %s", job["id"])
                    report.outcomes.append(o)
            for f in futures:
                f.result()

    def _nightly_models(self):
        notes = []
        from .excel import com_connect
        from .excel.models import models_with_new_data
        for name, _n in models_with_new_data(self.cfg, self.store):
            m = self.cfg.model(name)
            try:
                com_connect.refresh_closed_model(m["path"], self.paths.model_backups, log=self.log.info)
                notes.append(f"Модель «{name}» обновлена ночью")
            except Exception as e:  # noqa: BLE001
                notes.append(f"Модель «{name}» не обновлена ночью: {E.human_message(e)}")
        return notes


def version_string():
    return f"Хаб данных {VERSION}"
