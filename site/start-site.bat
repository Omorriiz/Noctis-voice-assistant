@echo off
rem Сайт теперь отдаёт сам сервер Noctis: запустите сервер и откройте http://localhost:8000
rem Этот файл нужен только для отдельного запуска сайта без сервера (адрес сервера тогда задайте в assets\config.js).
cd /d "%~dp0"
start "" "http://localhost:8080/"
python -m http.server 8080 --bind 127.0.0.1
pause
