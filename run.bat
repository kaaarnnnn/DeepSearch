@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3.12) || (set PY=python)
if not exist .venv (
    echo Первый запуск: создаю окружение и ставлю зависимости...
    %PY% -m venv .venv || goto :fail
    call .venv\Scripts\activate.bat
    python -m pip install --upgrade pip >nul
    python -m pip install -r requirements.txt || goto :fail
) else (
    call .venv\Scripts\activate.bat
)
start "" pythonw main.py
exit /b 0
:fail
echo Не удалось подготовить окружение. Нужен Python 3.12+ (https://www.python.org/downloads/).
pause
exit /b 1
