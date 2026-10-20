"""Создаёт учётную запись администратора (или назначает администратором существующего пользователя).

    python tools/create_admin.py --email admin@example.com [--name Админ]
    python tools/create_admin.py --email admin@example.com --demote       # снять права администратора

Пароль запрашивается в терминале (не вводится в аргументах и не хранится в файлах). Пароля по умолчанию нет.
Для автоматизации можно передать пароль в переменной окружения NOCTIS_ADMIN_PASSWORD.
Запускать из папки server (тем же Python, что и сервер): применяет миграции БД и пишет в data/noctis.db.
"""
import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from noctis_server.__main__ import migrate  # noqa: E402
from noctis_server.db import SessionLocal  # noqa: E402
from noctis_server.models import User, UserSettings, utcnow  # noqa: E402
from noctis_server.security import hash_password  # noqa: E402
from noctis_server.services import get_role, seed_roles  # noqa: E402

MIN_LEN = 10


def ask_password() -> str:
    env = os.environ.get("NOCTIS_ADMIN_PASSWORD")
    if env:
        return env
    while True:
        p1 = getpass.getpass("Пароль администратора: ")
        if len(p1) < MIN_LEN:
            print(f"Слишком короткий пароль (минимум {MIN_LEN} символов).")
            continue
        if p1 != getpass.getpass("Повторите пароль: "):
            print("Пароли не совпадают.")
            continue
        return p1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--email", required=True)
    ap.add_argument("--name", default="Администратор")
    ap.add_argument("--demote", action="store_true", help="сделать обычным пользователем")
    args = ap.parse_args()
    email = args.email.strip().lower()

    migrate()
    with SessionLocal() as db:
        seed_roles(db)
        user = db.scalar(select(User).where(User.email == email))
        if args.demote:
            if user is None:
                print("Пользователь не найден.")
                return 1
            user.role_id = get_role(db, "user").id
            db.commit()
            print(f"{email}: права администратора сняты.")
            return 0
        password = ask_password()
        if len(password) < MIN_LEN:
            print(f"Пароль короче {MIN_LEN} символов.")
            return 1
        if user is None:
            user = User(email=email, password_hash=hash_password(password), display_name=args.name,
                        role_id=get_role(db, "admin").id, consent_at=utcnow())
            user.settings = UserSettings()
            db.add(user)
            action = "создан"
        else:
            user.password_hash = hash_password(password)
            user.role_id = get_role(db, "admin").id
            user.is_active = True
            action = "назначен администратором (пароль обновлён)"
        db.commit()
        print(f"{email}: {action}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
