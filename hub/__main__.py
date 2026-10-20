"""
Командная строка Хаба (для отладки и планировщика):

    python -m hub                  — открыть приложение
    python -m hub update           — обновить показатели, у которых наступил срок
    python -m hub update --all     — обновить все показатели сейчас
    python -m hub update <id> ...  — обновить указанные показатели
    python -m hub update --scheduled  — так запускает планировщик Windows (без окон)
    python -m hub report           — открыть последний отчёт
    python -m hub check <id>       — пробная загрузка без записи
    python -m hub list             — список показателей и их статусы
    python -m hub setup [папка]    — создать рабочую папку и ярлык (вызывает Установка.bat)
    python -m hub samples          — сохранить живые ответы ЦБ/ЕМИСС в samples/ (для тестов)
    python -m hub claude <id>      — пакет «Отправить Claude» для показателя
"""
import argparse
import sys


def _out(text):
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("utf-8", "replace").decode("ascii", "replace"))


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="python -m hub", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    u = sub.add_parser("update")
    u.add_argument("ids", nargs="*")
    u.add_argument("--all", action="store_true")
    u.add_argument("--scheduled", action="store_true")
    u.add_argument("--open-report", action="store_true")
    sub.add_parser("report")
    c = sub.add_parser("check")
    c.add_argument("id")
    sub.add_parser("list")
    s = sub.add_parser("setup")
    s.add_argument("folder", nargs="?")
    s.add_argument("--shortcut", action="store_true")
    sub.add_parser("samples")
    cl = sub.add_parser("claude")
    cl.add_argument("id")
    sub.add_parser("gui")
    a = ap.parse_args(argv)

    if a.cmd in (None, "gui"):
        try:
            from .gui.app import main as gui_main
            return gui_main()
        except Exception:  # noqa: BLE001 — окно запускается без консоли: ошибку надо показать
            import traceback
            from .core.crash import startup_error
            startup_error(traceback.format_exc())
            return 1

    from .core.paths import Paths
    paths = Paths()

    if a.cmd == "setup":
        from .core import setup
        _out(setup.run(a.folder, shortcut=a.shortcut))
        return 0

    if a.cmd == "samples":
        from .core import samples
        _out(samples.save_live_samples())
        return 0

    from .runner import Busy, Runner
    runner = Runner(paths)
    try:
        if a.cmd == "update":
            try:
                rep = runner.run(job_ids=a.ids or None, force=a.all, scheduled=a.scheduled)
            except Busy as e:
                _out(str(e))
                return 0
            for o in rep.outcomes:
                from .core.status import word
                _out(f"{word(o.status):28} {o.job['name']}: {o.message}")
            if not rep.outcomes:
                _out("Показателей пока нет." if not runner.cfg.jobs() else
                     "Ни у одного показателя ещё не наступил срок обновления.")
            path = getattr(rep, "path", None)
            if path:
                _out(f"Отчёт: {path}")
                if a.open_report:
                    from .excel.exports import open_file
                    open_file(path)
            return 0
        if a.cmd == "report":
            from .core.report import list_reports
            files = list_reports(paths, 1)
            if not files:
                _out("Отчётов пока нет")
                return 0
            from .excel.exports import open_file
            open_file(files[0])
            _out(str(files[0]))
            return 0
        if a.cmd == "check":
            job = runner.cfg.job(a.id)
            if not job:
                _out(f"Показатель {a.id} не найден")
                return 1
            res, problems = runner.check(job)
            _out(res.describe())
            for s in res.series[:50]:
                _out(f"  {s.code:40} {s.name} [{s.unit}] {s.periodicity}")
            for p in problems:
                _out("Проверка: " + p)
            for w in res.warnings:
                _out("Замечание: " + w)
            return 0
        if a.cmd == "list":
            from .core.status import effective, word
            states = runner.store.states()
            for j in runner.cfg.jobs():
                st = states.get(j["id"], {})
                _out(f"{j['id']:40} {word(effective(j, st)):28} {j['name']}")
            return 0
        if a.cmd == "claude":
            from .core import claude_pack
            text = claude_pack.job_package(runner.cfg, runner.store, a.id)
            p = claude_pack.save(paths, text, a.id)
            _out(text)
            _out(f"\nСохранено: {p}")
            return 0
    finally:
        runner.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
