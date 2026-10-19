@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   Установка «Хаба данных»
echo   Повторный запуск безопасен: ничего не сломает.
echo ============================================
echo.

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY goto nopython
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 goto oldpython

echo [1/5] Python найден.
if not exist ".venv\Scripts\python.exe" (
  echo [2/5] Создаю отдельное окружение для программы в папке .venv ...
  %PY% -m venv .venv
  if errorlevel 1 goto failvenv
) else (
  echo [2/5] Окружение .venv уже есть.
)

echo [3/5] Устанавливаю нужные библиотеки (несколько минут при первом запуске) ...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failpip

echo [4/5] Встроенный браузер для сайтов со входом (по желанию) ...
".venv\Scripts\python.exe" -m pip install playwright >nul 2>nul
if errorlevel 1 (
  echo       Не установился - не страшно: всё остальное работает.
) else (
  ".venv\Scripts\python.exe" -m playwright install chromium
  if errorlevel 1 echo       Браузер не скачался - не страшно: всё остальное работает.
)

echo [5/5] Создаю рабочую папку и ярлык на рабочем столе ...
".venv\Scripts\python.exe" -m hub setup --shortcut
if errorlevel 1 goto failsetup

echo.
echo Готово! Открываю программу. Дальше запускайте её ярлыком «Хаб данных» на рабочем столе.
start "" ".venv\Scripts\pythonw.exe" -m hub
timeout /t 10
exit /b 0

:nopython
echo Python не найден.
echo 1. Откроется страница загрузки. Скачайте Python 3.12 для Windows.
echo 2. В установщике ОБЯЗАТЕЛЬНО отметьте галку «Add python.exe to PATH».
echo 3. После установки снова запустите этот файл.
start "" "https://www.python.org/downloads/windows/"
pause
exit /b 1

:oldpython
echo Установлен слишком старый Python. Нужен 3.11 или новее.
echo Скачайте новый: https://www.python.org/downloads/windows/ (галка «Add python.exe to PATH»).
start "" "https://www.python.org/downloads/windows/"
pause
exit /b 1

:failvenv
echo Не удалось создать окружение .venv. Проверьте, что папка не только для чтения, и запустите снова.
pause
exit /b 1

:failpip
echo Не удалось установить библиотеки. Чаще всего это нет интернета или мешает антивирус.
echo Проверьте интернет и запустите этот файл ещё раз.
pause
exit /b 1

:failsetup
echo Не удалось создать рабочую папку. Сообщение об ошибке — выше.
pause
exit /b 1
