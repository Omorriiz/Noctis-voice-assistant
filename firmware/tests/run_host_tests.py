"""Сборка и запуск хост-тестов noctis_core на ПК. Нужен компилятор: gcc/clang или `pip install ziglang`
(используется `python -m ziglang cc`)."""
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def find_cc() -> list[str]:
    for name in ("gcc", "clang", "cc"):
        if shutil.which(name):
            return [name]
    try:
        import ziglang  # noqa: F401
        return [sys.executable, "-m", "ziglang", "cc"]
    except ImportError:
        sys.exit("Нет компилятора Си: установите gcc/clang или `pip install ziglang`")


def main() -> int:
    exe = HERE / "build" / ("test_core.exe" if sys.platform == "win32" else "test_core")
    exe.parent.mkdir(exist_ok=True)
    cmd = find_cc() + ["-std=c11", "-Wall", "-Wextra", "-Werror", "-O1", "-fsanitize=undefined",
                       "-I", str(ROOT / "components/noctis_core/include"), "-I", str(HERE / "vendor"), "-I", str(HERE),
                       str(HERE / "test_core.c"), str(HERE / "test_stage5.c"), str(HERE / "test_wifi.c"),
                       str(ROOT / "components/noctis_core/noctis_core.c"),
                       str(ROOT / "components/noctis_core/noctis_sched.c"), str(ROOT / "components/noctis_core/noctis_audio.c"),
                       "-o", str(exe)]
    # cJSON — сторонний код: предупреждения компилятора для него не нужны
    cjson = exe.parent / "cJSON.o"
    subprocess.check_call(find_cc() + ["-std=c99", "-O1", "-w", "-c", str(HERE / "vendor/cJSON.c"), "-o", str(cjson)])
    cmd.insert(len(find_cc()), "-D_CRT_SECURE_NO_WARNINGS")
    cmd.append(str(cjson))
    subprocess.check_call(cmd)
    return subprocess.call([str(exe)])


if __name__ == "__main__":
    sys.exit(main())
