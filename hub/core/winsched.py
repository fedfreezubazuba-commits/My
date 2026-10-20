"""
Планировщик заданий Windows (раздел 10 ТЗ): задача «Хаб данных — обновление» запускает
`pythonw -m hub update --scheduled` каждые 15 минут и при входе в систему, с режимом
«запускать как можно скорее после пропущенного запуска» (StartWhenAvailable).

Задача создаётся из XML через schtasks — так доступны все нужные настройки.
XML собирается обычным кодом и проверяется тестами на любой системе.
Частый запуск дешёвый: если по расписанию ничего не пора проверять, проход сразу заканчивается.
"""
import datetime as dt
import getpass
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

from .paths import IS_WINDOWS, PROJECT

TASK_NAME = "Хаб данных — обновление"
INTERVAL = "PT15M"     # точность расписания «в среду в 10:30» — до 15 минут
EVERY_TEXT = "каждые 15 минут"


def pythonw() -> Path:
    exe = Path(sys.executable)
    cand = exe.with_name("pythonw.exe")
    return cand if cand.exists() else exe


def task_xml(command: str, args: str, workdir: str, user: str | None = None, start=None) -> str:
    user = user or getpass.getuser()
    start = (start or dt.datetime.now().replace(minute=0, second=0, microsecond=0)).strftime("%Y-%m-%dT%H:%M:%S")
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Фоновое обновление показателей Хаба данных. Включается и выключается в программе.</Description>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <Repetition><Interval>{INTERVAL}</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>
      <StartBoundary>{start}</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{escape(user)}</UserId>
      <Delay>PT2M</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(user)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(command)}</Command>
      <Arguments>{escape(args)}</Arguments>
      <WorkingDirectory>{escape(workdir)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _run(args):
    return subprocess.run(args, capture_output=True, text=True,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def is_enabled() -> bool | None:
    """True/False — есть ли задача; None — не Windows."""
    if not IS_WINDOWS:
        return None
    r = _run(["schtasks", "/Query", "/TN", TASK_NAME, "/XML"])
    return r.returncode == 0


def enable() -> tuple[bool, str]:
    if not IS_WINDOWS:
        return False, "Автообновление через планировщик доступно только в Windows"
    xml = task_xml(str(pythonw()), "-m hub update --scheduled", str(PROJECT))
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-16") as fh:
        fh.write(xml)
        path = fh.name
    try:
        r = _run(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", path, "/F"])
    finally:
        Path(path).unlink(missing_ok=True)
    if r.returncode != 0:
        return False, f"Не удалось создать задачу планировщика: {(r.stderr or r.stdout).strip()}"
    return True, f"Автообновление включено: {EVERY_TEXT} и при входе в Windows"


def parse_next_run(text) -> str | None:
    """Время следующего запуска из вывода «schtasks /Query /FO LIST» (подписи зависят от языка Windows,
    поэтому берём первую дату со временем)."""
    import re
    m = re.search(r"(\d{1,2})([./])(\d{1,2})[./](\d{4}),?\s+(\d{1,2}):(\d{2})(?::\d{2})?\s*([AaPp][Mm])?", text or "")
    if not m:
        return None
    a, sep, b, y, h, mi, ampm = m.groups()
    day, mon = (int(b), int(a)) if sep == "/" else (int(a), int(b))     # 10/20/2026 — американский порядок
    h = int(h) % 12 + (12 if ampm and ampm.lower() == "pm" else 0) if ampm else int(h)
    return f"{day:02d}.{mon:02d}.{y} {h:02d}:{mi}"


def status() -> tuple[bool | None, str | None]:
    """(есть ли задача, время следующего запуска «ДД.ММ.ГГГГ ЧЧ:ММ» или None); (None, None) — не Windows."""
    if not IS_WINDOWS:
        return None, None
    r = _run(["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST"])
    if r.returncode != 0:
        return False, None
    return True, parse_next_run(r.stdout)


def open_scheduler():
    """Открыть «Планировщик заданий» Windows."""
    if IS_WINDOWS:
        import os
        os.startfile("taskschd.msc")       # noqa: S606


def upgrade() -> bool:
    """Задача, созданная прежней версией (раз в час), пересоздаётся с новым интервалом."""
    if not IS_WINDOWS:
        return False
    r = _run(["schtasks", "/Query", "/TN", TASK_NAME, "/XML"])
    if r.returncode != 0 or f"<Interval>{INTERVAL}</Interval>" in (r.stdout or ""):
        return False
    return enable()[0]


def disable() -> tuple[bool, str]:
    if not IS_WINDOWS:
        return False, "Автообновление через планировщик доступно только в Windows"
    r = _run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
    if r.returncode != 0 and is_enabled():
        return False, f"Не удалось удалить задачу: {(r.stderr or r.stdout).strip()}"
    return True, "Автообновление выключено"
