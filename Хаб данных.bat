@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Сначала запустите «Установка.bat».
  call "Установка.bat"
  exit /b
)
start "" ".venv\Scripts\pythonw.exe" -m hub
