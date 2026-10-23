"""
Планировщик заданий Windows (раздел 10 ТЗ): задача «Хаб данных — обновление» запускает
`pythonw -m hub update --scheduled` каждые 15 минут и при входе в систему, с режимом
«запускать как можно скорее после пропущенного запуска» (StartWhenAvailable).

Задача создаётся из XML через schtasks — так доступны все нужные настройки.
XML собирается обычным кодом и проверяется тестами на любой системе.
Частый запуск дешёвый: если по расписанию ничего не пора проверять, проход сразу заканчивается.

Кроме запуска раз в 15 минут в задаче есть ежедневные запуски ровно во время из графиков показателей
(«по будням в 19:05» → каждый день в 19:05 по времени компьютера): хаб сам решает, что пора проверять, а
планировщик будит его вовремя. sync() сверяет задачу с тем, что нужно сейчас (путь к программе, рабочая папка,
времена из графиков), и пересоздаёт её, если что-то разошлось: например, папку с программой перенесли —
тогда Windows не находит файл (0x80070002) и задача молча перестаёт работать.
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
ARGS = "-m hub update --scheduled"
TIMES_MARK = "hub-times:"
MAX_TIMES = 40          # у задачи не больше 48 триггеров
RESULTS = {
    0: "последний запуск прошёл успешно",
    0x41301: "задача сейчас выполняется",
    0x41303: "задача ещё ни разу не запускалась",
    0x41306: "задачу остановили (возможно, слишком долго работала)",
    0x80070002: "Windows не находит файл программы — папку с хабом переносили или переименовывали",
    0x8007010B: "Windows не находит папку программы — её переносили или переименовывали",
    0x800710E0: "Windows не дала запустить задачу (компьютер спал или был выключен)",
    1: "хаб запустился, но завершился с ошибкой — подробности в журнале (logs) рабочей папки",
}
OK_RESULTS = {0, 0x41301, 0x41303}
INTERVAL = "PT15M"     # точность расписания «в среду в 10:30» — до 15 минут
EVERY_TEXT = "каждые 15 минут"


def pythonw() -> Path:
    exe = Path(sys.executable)
    cand = exe.with_name("pythonw.exe")
    return cand if cand.exists() else exe


def rule_times(cfg) -> list[str]:
    """Время проверок из графиков всех показателей — «ЧЧ:ММ» по часам компьютера (правила — по Москве)."""
    from . import schedule as S
    if cfg is None:
        return []
    out = set()
    today = dt.date.today()
    for job in cfg.jobs():
        if not job.get("enabled", True):
            continue
        try:
            rules = S.parse_rules(job.get("schedule"))
        except ValueError:
            continue
        for kind, _arg, at in rules:
            if kind in ("auto", "every"):
                continue
            h, m = at or (S.SLOT_HOUR, 0)
            local = S.from_msk(dt.datetime(today.year, today.month, today.day, h, m))
            out.add(f"{local:%H:%M}")
    return sorted(out)[:MAX_TIMES]


def task_xml(command: str, args: str, workdir: str, user: str | None = None, start=None, times=()) -> str:
    user = user or getpass.getuser()
    start = start or dt.datetime.now().replace(minute=0, second=0, microsecond=0)
    begin = start.strftime("%Y-%m-%dT%H:%M:%S")
    daily = "".join(f"""
    <CalendarTrigger>
      <StartBoundary>{start:%Y-%m-%d}T{t}:30</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>""" for t in times)
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Фоновое обновление показателей Хаба данных. Включается и выключается в программе. {TIMES_MARK}{",".join(times)}</Description>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <Repetition><Interval>{INTERVAL}</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>
      <StartBoundary>{begin}</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{escape(user)}</UserId>
      <Delay>PT2M</Delay>
    </LogonTrigger>{daily}
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


def problems(task_text: str | list, command: str, workdir: str, times) -> list[str]:
    """Чем задача в планировщике отличается от нужной (пусто — всё совпадает).
    task_text — XML задачи (или несколько вариантов расшифровки вывода schtasks)."""
    texts = [task_text] if isinstance(task_text, str) else list(task_text)

    def has(x):
        return any(x.lower() in t.lower() for t in texts)
    out = []
    if not has(f"<Command>{escape(command)}</Command>"):
        out.append("путь к программе")
    if not has(f"<WorkingDirectory>{escape(workdir)}</WorkingDirectory>"):
        out.append("папка программы")
    if not has(f"<Interval>{INTERVAL}</Interval>"):
        out.append("интервал")
    if not has(f"{TIMES_MARK}{','.join(times)}<"):
        out.append("время из графиков")
    return out


def _texts(data: bytes) -> list[str]:
    """Вывод schtasks в возможных кодировках (зависит от языка Windows)."""
    out = []
    for enc in (("utf-16",) if data[:2] in (b"\xff\xfe", b"\xfe\xff") else ()) + ("utf-8", "cp866", "cp1251"):
        try:
            out.append(data.decode(enc))
        except (UnicodeDecodeError, LookupError):
            continue
    return out or [data.decode("utf-8", "replace")]


def _run(args):
    """Результат schtasks: код возврата и вывод, расшифрованный как получится (для сообщений)."""
    r = subprocess.run(args, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    r.texts = _texts(r.stdout or b"")
    r.stdout = r.texts[0]
    r.stderr = _texts(r.stderr or b"")[0]
    return r


def is_enabled() -> bool | None:
    """True/False — есть ли задача; None — не Windows."""
    if not IS_WINDOWS:
        return None
    r = _run(["schtasks", "/Query", "/TN", TASK_NAME, "/XML"])
    return r.returncode == 0


def enable(cfg=None, times=None) -> tuple[bool, str]:
    if not IS_WINDOWS:
        return False, "Автообновление через планировщик доступно только в Windows"
    cmd = pythonw()
    if not cmd.exists():
        return False, f"Не найден файл программы {cmd}. Запустите «Установка.bat» в папке программы"
    times = rule_times(cfg) if times is None else times
    xml = task_xml(str(cmd), ARGS, str(PROJECT), times=times)
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-16") as fh:
        fh.write(xml)
        path = fh.name
    try:
        r = _run(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", path, "/F"])
    finally:
        Path(path).unlink(missing_ok=True)
    if r.returncode != 0:
        return False, f"Не удалось создать задачу планировщика: {(r.stderr or r.stdout).strip()}"
    return True, f"Автообновление включено: {EVERY_TEXT}, при входе в Windows" + (
        f" и точно ко времени из графиков ({', '.join(times)})" if times else "")


def sync(cfg) -> tuple[bool, list[str]]:
    """Задача есть, но не такая, как нужно (папку программы перенесли, поменялись графики) — пересоздать.
    → (пересоздана ли, что было не так). Если автообновление выключено (задачи нет) — ничего не делаем."""
    if not IS_WINDOWS:
        return False, []
    r = _run(["schtasks", "/Query", "/TN", TASK_NAME, "/XML"])
    if r.returncode != 0:
        return False, []
    bad = problems(r.texts, str(pythonw()), str(PROJECT), rule_times(cfg))
    if not bad:
        return False, []
    ok, _msg = enable(cfg)
    return ok, bad


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


def status() -> tuple[bool | None, str | None, int | None]:
    """(есть ли задача, следующий запуск «ДД.ММ.ГГГГ ЧЧ:ММ» или None, код результата последнего запуска или None);
    (None, None, None) — не Windows."""
    if not IS_WINDOWS:
        return None, None, None
    r = _run(["schtasks", "/Query", "/TN", TASK_NAME, "/V", "/FO", "CSV"])
    if r.returncode != 0:
        return False, None, None
    nxt, code = parse_status_csv(r.stdout)
    return True, nxt, code


def parse_status_csv(text) -> tuple[str | None, int | None]:
    """Из «schtasks /Query /V /FO CSV»: следующий запуск и код последнего результата. Подписи столбцов
    зависят от языка Windows, порядок — нет: 3-й столбец — следующий запуск, 7-й — последний результат."""
    import csv
    rows = [r for r in csv.reader((text or "").splitlines()) if len(r) > 6]
    data = [r for r in rows if r[1].strip("\\") == TASK_NAME or r[1].endswith(TASK_NAME)] or rows[1:2]
    if not data:
        return None, None
    row = data[0]
    try:
        code = int(row[6].strip(), 0) & 0xFFFFFFFF
    except ValueError:
        code = None
    return parse_next_run(row[2]), code


def result_text(code) -> str:
    if code is None:
        return ""
    return RESULTS.get(code, f"код {code:#010x}")


def open_scheduler():
    """Открыть «Планировщик заданий» Windows."""
    if IS_WINDOWS:
        import os
        os.startfile("taskschd.msc")       # noqa: S606


def upgrade(cfg=None) -> bool:
    """Задача от прежней версии или с устаревшим путём к программе пересоздаётся."""
    return sync(cfg)[0]


def disable() -> tuple[bool, str]:
    if not IS_WINDOWS:
        return False, "Автообновление через планировщик доступно только в Windows"
    r = _run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
    if r.returncode != 0 and is_enabled():
        return False, f"Не удалось удалить задачу: {(r.stderr or r.stdout).strip()}"
    return True, "Автообновление выключено"
