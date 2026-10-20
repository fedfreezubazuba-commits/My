@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Папка программы: %CD%
if not exist ".venv\Scripts\python.exe" (
  echo.
  echo В этой папке программа не установлена: нет папки .venv.
  echo Скорее всего, архив распакован не поверх старой папки, а в новую.
  echo Либо распакуйте его поверх старой папки, либо запустите здесь «Установка.bat».
  pause
  exit /b 1
)
".venv\Scripts\python.exe" --version
echo Запускаю хаб. Пока окно хаба открыто, это окно не закрывайте.
echo.
".venv\Scripts\python.exe" -X faulthandler -m hub
echo.
echo Хаб закрылся (код %ERRORLEVEL%). Если выше есть текст ошибки — скопируйте его и пришлите Claude.
pause
