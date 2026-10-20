"""Журнал UART платы для длительной проверки: запись с метками времени и разбор итогов.

Запись (плату НЕ перезагружает; если USB отвалился — переподключается сам; остановка Ctrl+C или --hours):
    python tools/board_log.py record --port COM5 --out board-night.log --hours 12
    python tools/board_log.py record --port COM5 --out board.log --reset     # с перезагрузкой платы в начале

Разбор готового журнала (перезагрузки, падения, обрывы Wi-Fi, память, запас стека, срабатывания слова):
    python tools/board_log.py analyze board-night.log

Нужен pyserial (есть в tools_venv PlatformIO: ..\\..\\tools_venv\\Scripts\\python.exe).
"""
import argparse
import re
import sys
import time
from datetime import datetime, timedelta

CRASH = re.compile(r"Guru Meditation|assert failed|abort\(\)|stack overflow|Backtrace:|CORRUPT|panic'ed", re.I)
RESET = re.compile(r"rst:0x([0-9a-f]+) \((\w+)\)")
MEM = re.compile(r"Память: свободно (\d+) КБ \(мин\. (\d+) КБ\); запас стека, Б: (.*)")
STACK = re.compile(r"(\w+) (\d+)")
WIFI_DROP = re.compile(r"Wi-Fi отключён(?: \(причина (\d+)\))?")
WS_OK = "WebSocket подключён"
WAKE = "! Говорите команду"
HEARD = re.compile(r"\[слух\] вероятность слова ([0-9.]+), пик (\d+)")
TS = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\] ")


def record(args):
    import serial  # noqa: PLC0415

    end = time.time() + args.hours * 3600 if args.hours else None
    first = True
    with open(args.out, "a", encoding="utf-8", buffering=1) as out:
        print(f"Запись в {args.out} (Ctrl+C — стоп)")
        while end is None or time.time() < end:
            try:
                ser = serial.Serial(args.port, args.baud, timeout=1)
            except (serial.SerialException, OSError) as e:
                print(f"порт {args.port} недоступен ({e}); повтор через 2 с")
                time.sleep(2)
                continue
            if first and args.reset:
                ser.dtr, ser.rts = False, True
                time.sleep(0.1)
                ser.rts = False
            first = False
            buf = b""
            try:
                while end is None or time.time() < end:
                    chunk = ser.read(4096)
                    if not chunk:
                        continue
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        text = line.decode("utf-8", "replace").rstrip("\r")
                        out.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {text}\n")
            except (serial.SerialException, OSError):
                out.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] ### USB-порт пропал, переподключаюсь\n")
                time.sleep(1)
            finally:
                try:
                    ser.close()
                except Exception:
                    pass


def analyze(args):
    resets, crashes, drops, reasons = [], [], 0, {}
    ws_ok = wakes = errors = lines = 0
    mem_free, mem_min, stack_min = [], [], {}
    heard_max, first_ts, last_ts, usb_losses = 0.0, None, None, 0
    first_boot_seen = False
    for raw in open(args.file, encoding="utf-8", errors="replace"):
        lines += 1
        m = TS.match(raw)
        ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S") if m else None
        if ts:
            first_ts = first_ts or ts
            last_ts = ts
        line = raw[m.end():] if m else raw
        if "### USB-порт пропал" in line:
            usb_losses += 1
        r = RESET.search(line)
        if r:
            if first_boot_seen or r.group(2) != "POWERON":
                resets.append((ts, r.group(2)))
            first_boot_seen = True
        if CRASH.search(line):
            crashes.append((ts, line.strip()[:100]))
        w = WIFI_DROP.search(line)
        if w:
            drops += 1
            reasons[w.group(1) or "?"] = reasons.get(w.group(1) or "?", 0) + 1
        if WS_OK in line:
            ws_ok += 1
        if WAKE in line:
            wakes += 1
        if re.match(r"^E \(", line):
            errors += 1
        mm = MEM.search(line)
        if mm:
            mem_free.append(int(mm.group(1)))
            mem_min.append(int(mm.group(2)))
            for name, val in STACK.findall(mm.group(3)):
                if int(val) > 0:
                    stack_min[name] = min(stack_min.get(name, 10 ** 9), int(val))
        h = HEARD.search(line)
        if h:
            heard_max = max(heard_max, float(h.group(1)))

    if not lines:
        sys.exit("файл пуст")
    span = (last_ts - first_ts) if first_ts and last_ts else timedelta(0)
    hours = max(span.total_seconds() / 3600, 1e-9)
    print(f"Файл: {args.file}, строк {lines}, длительность {span}")
    print(f"Перезагрузок (кроме первого включения): {len(resets)}", [f"{t:%m-%d %H:%M} {c}" if t else c for t, c in resets][:6])
    print(f"Падений/паник: {len(crashes)}", [(f"{t:%H:%M:%S}" if t else "", c) for t, c in crashes[:3]])
    print(f"Обрывов Wi-Fi: {drops} ({drops / hours:.1f} в час), причины: {reasons or '—'}")
    print(f"Подключений к серверу: {ws_ok}; потерь USB: {usb_losses}; строк-ошибок E: {errors}")
    print(f"Срабатываний слова пробуждения: {wakes}")
    if mem_free:
        print(f"Внутренняя память: свободно {min(mem_free)}–{max(mem_free)} КБ, минимум за всё время {min(mem_min)} КБ; "
              f"тренд: {mem_free[0]} → {mem_free[-1]} КБ")
    if stack_min:
        print("Минимальный запас стека, Б:", dict(sorted(stack_min.items())))
    if heard_max:
        print(f"Макс. вероятность слова в журнале: {heard_max:.2f}")

    problems = []
    if crashes:
        problems.append("были падения")
    if resets:
        problems.append("были перезагрузки")
    if mem_min and min(mem_min) < 8:
        problems.append("внутренней памяти почти не оставалось (< 8 КБ)")
    if mem_free and len(mem_free) > 10 and mem_free[-1] < mem_free[0] - 10:
        problems.append("свободная память заметно убывает (утечка?)")
    if stack_min and min(stack_min.values()) < 600:
        problems.append("у какой-то задачи запас стека < 600 Б")
    print("ВЕРДИКТ:", "ЗАМЕЧАНИЙ НЕТ" if not problems else "ЕСТЬ ЗАМЕЧАНИЯ — " + "; ".join(problems))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--port", default="COM5")
    r.add_argument("--baud", type=int, default=115200)
    r.add_argument("--out", default="board.log")
    r.add_argument("--hours", type=float, default=0, help="0 — пока не остановят")
    r.add_argument("--reset", action="store_true", help="перезагрузить плату в начале записи")
    r.set_defaults(fn=record)
    a = sub.add_parser("analyze")
    a.add_argument("file")
    a.set_defaults(fn=analyze)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        args.fn(args)
    except KeyboardInterrupt:
        print("\nостановлено")


if __name__ == "__main__":
    main()
