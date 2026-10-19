"""Журнал в logs/hub.log (по файлу на месяц). Пользователь журналы не читает — их читает Claude."""
import logging
import time

_configured = set()


def get_logger(paths=None, name="hub"):
    log = logging.getLogger(name)
    key = str(paths.logs) if paths else "-"
    if key not in _configured:
        _configured.add(key)
        log.setLevel(logging.INFO)
        if paths:
            paths.logs.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(paths.logs / f"hub_{time.strftime('%Y-%m')}.log", encoding="utf-8")
            fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
            log.addHandler(fh)
    return log
