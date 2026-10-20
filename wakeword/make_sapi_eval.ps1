# Независимая проверка модели голосом ДРУГОГО движка: Windows SAPI («Microsoft Irina», ru-RU), а не Silero, которым
# синтезировались обучающие примеры. Положительные — слово с разной скоростью/пунктуацией; отрицательные — длинная
# обычная русская речь (чтение текста) для оценки ложных срабатываний в час.
#   .\make_sapi_eval.ps1 -Out D:\wake\eval_sapi -TextFile "..\docs\stage3-personalization-report.md,..\docs\stage2-firmware-report.md"
param([Parameter(Mandatory)][string]$Out, [string]$Word = "Нильс", [string]$TextFile = "")  # несколько файлов — через запятую
Add-Type -AssemblyName System.Speech
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SelectVoice("Microsoft Irina Desktop")
New-Item -ItemType Directory -Force "$Out\positive", "$Out\speech" | Out-Null
$i = 0
foreach ($rate in -4..6) {
    foreach ($suffix in @("", "!", "?", ".", ",", "...")) {
        $s.Rate = $rate
        $s.Volume = 100
        $s.SetOutputToWaveFile("$Out\positive\irina_{0:D3}.wav" -f $i, $fmt)
        $s.Speak($Word + $suffix)
        $s.SetOutputToNull()
        $i++
    }
}
"Положительных: $i"
# Слова-«обманки», похожие по звучанию или смыслу: колонка просыпаться на них не должна
New-Item -ItemType Directory -Force "$Out\decoys" | Out-Null
$k = 0
foreach ($w in @("Нил", "Нина", "Нельсон", "Билл", "Эльс", "Миля", "Ниже", "Нить", "Привет", "Алиса", "Окей", "Слушай",
                 "Музыка", "Время", "Ноктис", "Колонка", "Спасибо", "Хорошо", "Фильм", "Ноль", "Никс", "Милс")) {
    foreach ($rate in @(-2, 1, 4)) {
        $s.Rate = $rate
        $s.SetOutputToWaveFile("$Out\decoys\decoy_{0:D3}.wav" -f $k, $fmt)
        $s.Speak($w)
        $s.SetOutputToNull()
        $k++
    }
}
"Обманок: $k"
$j = 0
foreach ($f in ($TextFile -split "," | Where-Object { $_ })) {
    $text = (Get-Content $f -Raw -Encoding UTF8) -replace '[`#*|>\[\]\(\)_\-=]', ' ' -replace '\s+', ' ' -replace '(?i)нильс\w*|nils', 'ассистент'   # само слово из текста убираем: это были бы не ложные срабатывания
    $s.Rate = 1
    $s.SetOutputToWaveFile("$Out\speech\speech_{0:D2}.wav" -f $j, $fmt)
    $s.Speak($text)
    $s.SetOutputToNull()
    $j++
}
"Файлов длинной речи: $j"
