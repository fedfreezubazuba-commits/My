@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Сначала запустите «Установка.bat».
  pause
  exit /b 1
)
echo Обновляю все показатели. Когда всё закончится, откроется отчёт.
".venv\Scripts\python.exe" -m hub update --all --open-report
echo.
echo Готово. Окно можно закрыть.
timeout /t 15
