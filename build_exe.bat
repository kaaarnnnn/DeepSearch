@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3.12) || (set PY=python)
if not exist .venv (
    echo Создаю виртуальное окружение...
    %PY% -m venv .venv || goto :fail
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
python -m pip install -r requirements-build.txt || goto :fail
python tools\make_icon.py || goto :fail
python build_exe.py %* || goto :fail
echo.
echo Сборка завершена. Запускайте dist\DeepSearch\DeepSearch.exe
pause
exit /b 0
:fail
echo.
echo Сборка не удалась. Смотрите сообщения выше.
pause
exit /b 1
