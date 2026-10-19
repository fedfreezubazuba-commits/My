"""
Подготовка рабочей папки и ярлыка на рабочем столе (вызывается из «Установка.bat»).
Повторный запуск безопасен: существующие файлы не трогаются.
"""
import subprocess
from .config import Config
from .paths import IS_WINDOWS, PROJECT, Paths, default_workdir, is_configured, set_workdir, workdir


def run(folder=None, shortcut=False) -> str:
    if folder:
        set_workdir(folder)
    elif not is_configured():
        set_workdir(default_workdir())
    paths = Paths(workdir()).ensure()
    cfg = Config(paths)
    if not paths.settings.exists():
        cfg.save_settings(cfg.settings())
    for p, empty in ((paths.jobs, []), (paths.models, [])):
        if not p.exists():
            if p == paths.jobs:
                cfg.save_jobs(empty)
            else:
                cfg.save_models(empty)
    lines = [f"Рабочая папка: {paths.root}"]
    if shortcut:
        lines.append(make_shortcut())
    return "\n".join(lines)


def make_shortcut() -> str:
    if not IS_WINDOWS:
        return "Ярлык создаётся только в Windows"
    target = PROJECT / "Хаб данных.bat"
    ps = (
        "$s=(New-Object -ComObject WScript.Shell);"
        "$d=[Environment]::GetFolderPath('Desktop');"
        "$l=$s.CreateShortcut((Join-Path $d 'Хаб данных.lnk'));"
        f"$l.TargetPath='{target}';"
        f"$l.WorkingDirectory='{PROJECT}';"
        "$l.WindowStyle=7;"
        "$l.IconLocation='%SystemRoot%\\System32\\imageres.dll,170';"
        "$l.Save()"
    )
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return "Ярлык на рабочем столе создать не удалось: " + (r.stderr or "").strip()[:300]
    return "Ярлык «Хаб данных» создан на рабочем столе"
