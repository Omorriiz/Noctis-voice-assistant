@echo off
rem Запуск сервера Noctis: дважды щёлкните этот файл. Окно не закрывайте, остановить сервер — Ctrl+C.
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Не найдено виртуальное окружение .venv. Создайте его по инструкции в README.md:
    echo   python -m venv .venv
    echo   .venv\Scripts\pip install -r requirements.txt -r requirements-ai.txt
    pause
    exit /b 1
)

echo Запускаю сервер Noctis. Сайт: http://localhost:8000/  (документация API: /docs)
echo.
".venv\Scripts\python.exe" -m noctis_server

echo.
echo Сервер остановлен.
pause
