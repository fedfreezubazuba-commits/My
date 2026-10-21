"""
Уведомления Windows — только когда есть что сказать (новые данные в моделях, ошибки).
Отключаются в настройках. Без дополнительных пакетов: всплывающее уведомление через
PowerShell (Windows.UI.Notifications), а если оно недоступно — значок в области уведомлений.
На других системах уведомление только пишется в журнал.
"""
import base64
import subprocess

from .paths import IS_WINDOWS

TOAST_PS = r"""
$ErrorActionPreference = 'Stop'
$title = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{title}'))
$text = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{text}'))
try {{
  [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
  $tpl = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
  $nodes = $tpl.GetElementsByTagName('text')
  $nodes.Item(0).AppendChild($tpl.CreateTextNode($title)) > $null
  $nodes.Item(1).AppendChild($tpl.CreateTextNode($text)) > $null
  $toast = [Windows.UI.Notifications.ToastNotification]::new($tpl)
  $app = '{{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}}\WindowsPowerShell\v1.0\powershell.exe'
  [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show($toast)
}} catch {{
  Add-Type -AssemblyName System.Windows.Forms
  $n = New-Object System.Windows.Forms.NotifyIcon
  $n.Icon = [System.Drawing.SystemIcons]::Information
  $n.Visible = $true
  $n.ShowBalloonTip(10000, $title, $text, 'Info')
  Start-Sleep -Seconds 11
  $n.Dispose()
}}
"""


def _b64(s):
    return base64.b64encode(s.encode("utf-8")).decode()


def toast(title, text, log=None) -> bool:
    if log:
        log.info("Уведомление: %s — %s", title, text)
    if not IS_WINDOWS:
        return False
    script = TOAST_PS.format(title=_b64(title), text=_b64(text[:250]))
    try:
        subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                          "-Command", script], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return True
    except OSError as e:
        if log:
            log.warning("Уведомление не показано: %s", e)
        return False


def message_for(report) -> tuple[str, str] | None:
    """Текст уведомления или None, если сказать нечего."""
    bad = report.by_status("error", "login", "quarantine")
    parts = []
    if report.models_new:
        names = ", ".join(n for n, _ in report.models_new[:3])
        parts.append(f"Новые данные в моделях: {names}")
    if bad:
        parts.append(f"Требует внимания: {len(bad)}")
    from .hostblock import title
    for o in report.outcomes:
        if getattr(o, "blocked", None):
            host, till = o.blocked
            parts.append(f"{title(host)} временно не пускает хаб — пауза до {till:%H:%M}. Подробности на «Обзоре»")
    if not parts:
        return None
    return "Хаб данных", ". ".join(parts)


def after_run(runner, report):
    if not runner.cfg.settings().get("notifications", True):
        return
    msg = message_for(report)
    if msg:
        toast(*msg, log=runner.log)
