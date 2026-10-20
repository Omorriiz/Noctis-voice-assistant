# Подготовка окружения обучения слова пробуждения на Windows (без WSL). Запуск: .\setup_windows.ps1 [-Work D:\wake]
param([string]$Work = (Join-Path $PSScriptRoot "work"))
$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force $Work | Out-Null
python -m venv "$Work\venv"
$py = "$Work\venv\Scripts\python.exe"
& $py -m pip install --upgrade pip
& $py -m pip install "git+https://github.com/whatsnowplaying/audio-metadata@d4ebb238e6a401bb1a5aaaac60c9e2b3cb30929f"
if (-not (Test-Path "$Work\microWakeWord")) { git clone https://github.com/kahrendt/microWakeWord "$Work\microWakeWord" }
git -C "$Work\microWakeWord" checkout 4665173cd35f1cff9a61e06fc427f124766c488e   # проверенная версия
& $py -m pip install -e "$Work\microWakeWord"
& $py -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
& $py -m pip install -r "$PSScriptRoot\requirements.txt"
Write-Host "Готово. Дальше: README.md, шаг 1 (make_samples.py). Интерпретатор: $py"
