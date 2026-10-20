"""Поиск секретов в проекте (этап 8, пункт 11): в отслеживаемых git-файлах и всей истории, в ZIP-архивах поставки и в журналах.

    python tools\\scan_secrets.py [--zips <папка с архивами>] [--logs <папка журналов>]

Что ищется:
  1. ТОЧНЫЕ значения ваших настоящих секретов (из server\\.env, data\\secret.key, app\\android\\key.properties): ключ Jamendo, ключ подписи токенов,
     пароли хранилища ключей Android — не должны встретиться нигде, кроме самих этих файлов;
  2. файлы, которых в поставке быть не должно: .env, data\\secret.key, *.jks, key.properties, базы данных, образы моделей/записи;
  3. по шаблонам: JWT-токены, закрытые ключи, «Bearer …», длинные случайные строки рядом со словами password/secret/token/api_key.
Код возврата 1 — найдено подтверждённое попадание (пункты 1–2 или закрытый ключ/JWT); остальное — на ручную проверку.
"""
import argparse
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
FORBIDDEN_NAMES = (".env", "secret.key", "key.properties")
FORBIDDEN_SUFFIXES = (".jks", ".keystore", ".db", ".db-wal", ".db-shm", ".pem", ".p12")
TEXT_SUFFIXES = {".py", ".dart", ".js", ".json", ".md", ".txt", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".env", ".example", ".html", ".css", ".c", ".h",
                 ".cpp", ".csv", ".ps1", ".bat", ".gradle", ".properties", ".xml", ".kts", ".sh", ".log", ""}
PATTERNS = {
    "JWT": (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"), True),
    "закрытый ключ": (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"), True),
    "Bearer-токен": (re.compile(r"Bearer\s+[A-Za-z0-9_\-.=]{30,}"), False),
    "секрет рядом с длинной случайной строкой": (re.compile(r"(?i)(?:secret|api[_-]?key|token|passw\w*)\W{1,4}[A-Za-z0-9+/_\-]{32,}"), False),
}
SKIP_DIRS = {".git", ".venv", "node_modules", ".dart_tool", "build", ".pio", "__pycache__", "managed_components"}


def real_secrets() -> dict[str, str]:
    """Значения настоящих секретов, найденные на этой машине (имя → значение). Короткие и общеизвестные не берутся."""
    found = {}
    env = SERVER / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                value = value.split("#")[0].strip().strip('"\'')
                if len(value) >= 6 and any(w in key.upper() for w in ("KEY", "SECRET", "CLIENT_ID", "PASSWORD", "TOKEN")):
                    found[f".env:{key.strip()}"] = value
    sk = SERVER / "data" / "secret.key"
    if sk.exists() and len(sk.read_text().strip()) >= 32:
        found["data/secret.key"] = sk.read_text().strip()
    kp = ROOT / "app" / "android" / "key.properties"
    if kp.exists():
        for line in kp.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "=" in line and "password" in line.lower():
                found[f"key.properties:{line.split('=')[0]}"] = line.split("=", 1)[1].strip()
    return {k: v for k, v in found.items() if len(v) >= 6}


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, encoding="utf-8", errors="ignore").stdout


class Report:
    def __init__(self):
        self.hard: list[str] = []
        self.review: list[str] = []

    def add(self, where: str, what: str, hard: bool):
        line = f"{where}: {what}"
        bucket = self.hard if hard else self.review
        if line not in bucket:                       # одно и то же место, найденное много раз, показываем один раз
            bucket.append(line)


def scan_text(where: str, text: str, secrets: dict[str, str], rep: Report):
    for name, value in secrets.items():
        if value in text:
            rep.add(where, f"содержит значение секрета «{name}»", True)
    for label, (rx, hard) in PATTERNS.items():
        for m in rx.finditer(text):
            sample = m.group(0)
            rep.add(where, f"{label}: {sample[:18]}…", hard)


def scan_git(secrets: dict[str, str], rep: Report) -> tuple[int, int]:
    files = [f for f in git("ls-files").splitlines() if f]
    for f in files:
        name = Path(f).name
        if name in FORBIDDEN_NAMES or name.endswith(FORBIDDEN_SUFFIXES):
            rep.add(f"git:{f}", "файл не должен быть в репозитории", True)
        p = ROOT / f
        if p.suffix.lower() in TEXT_SUFFIXES and p.exists() and p.stat().st_size < 2_000_000:
            scan_text(f"git:{f}", p.read_text(encoding="utf-8", errors="ignore"), secrets, rep)
    history = git("log", "--all", "-p", "--no-color", "--format=commit %h")
    scan_text("история git", history, secrets, rep)
    for added in set(re.findall(r"^\+\+\+ b/(.+)$", history, re.M)):
        name = Path(added).name
        if name in FORBIDDEN_NAMES or name.endswith(FORBIDDEN_SUFFIXES):
            rep.add(f"история git:{added}", "файл когда-то попадал в репозиторий", True)
    return len(files), history.count("\ncommit ") + 1


def scan_zips(folder: Path, secrets: dict[str, str], rep: Report) -> int:
    n = 0
    for z in sorted(folder.glob("*.zip")):
        n += 1
        try:
            with zipfile.ZipFile(z) as zf:
                for info in zf.infolist():
                    name = Path(info.filename).name
                    if any(part in SKIP_DIRS - {"build"} for part in Path(info.filename).parts) and ".git" not in info.filename.split("/"):
                        pass
                    if name in FORBIDDEN_NAMES or name.endswith(FORBIDDEN_SUFFIXES):
                        rep.add(f"{z.name}:{info.filename}", "файл не должен быть в архиве", True)
                    elif Path(info.filename).suffix.lower() in TEXT_SUFFIXES and 0 < info.file_size < 2_000_000 and ".git/" not in info.filename:
                        scan_text(f"{z.name}:{info.filename}", zf.read(info).decode("utf-8", errors="ignore"), secrets, rep)
        except zipfile.BadZipFile:
            rep.add(z.name, "архив не открывается", False)
    return n


def scan_logs(folder: Path, secrets: dict[str, str], rep: Report) -> int:
    n = 0
    for f in sorted(folder.rglob("*.log")):
        n += 1
        scan_text(f"журнал:{f.name}", f.read_text(encoding="utf-8", errors="ignore"), secrets, rep)
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zips", type=Path, default=ROOT.parent)
    ap.add_argument("--logs", type=Path, default=SERVER / "logs")
    ap.add_argument("--redact-logs", action="store_true", help="заменить найденные в журналах значения секретов на ***")
    args = ap.parse_args()
    secrets = real_secrets()
    if args.redact_logs and args.logs.exists():
        for f in args.logs.rglob("*.log"):
            text = f.read_text(encoding="utf-8", errors="ignore")
            cleaned = text
            for value in secrets.values():
                cleaned = cleaned.replace(value, "***")
            if cleaned != text:
                f.write_text(cleaned, encoding="utf-8")
                print(f"журнал очищен: {f.name}")
    rep = Report()
    files, commits = scan_git(secrets, rep)
    zips = scan_zips(args.zips, secrets, rep) if args.zips.exists() else 0
    logs = scan_logs(args.logs, secrets, rep) if args.logs.exists() else 0
    print(f"Проверено: файлов в git {files}, коммитов {commits}, архивов {zips}, журналов {logs}; настоящих секретов для поиска: {len(secrets)} "
          f"({', '.join(secrets) or 'нет'})")
    for line in rep.hard:
        print("ПОПАДАНИЕ  ", line)
    for line in rep.review[:40]:
        print("проверить  ", line)
    if len(rep.review) > 40:
        print(f"…и ещё {len(rep.review) - 40} строк для ручной проверки")
    print("ИТОГ:", "найдены попадания" if rep.hard else "подтверждённых утечек нет", f"(на ручную проверку: {len(rep.review)})")
    return 1 if rep.hard else 0


if __name__ == "__main__":
    sys.exit(main())
