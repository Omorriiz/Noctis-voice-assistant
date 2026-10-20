"""Парольная политика (этап 8): длина ≥ 8 проверяется схемой; здесь — слабые и «очевидные» пароли.

Проверяется: пароль из списка самых частых (в том числе с цифрами в конце: «password1»), последовательности («12345678», «abcdefgh»,
«qwertyui»), один и тот же символ, слишком мало разных символов, пароль содержит почту (часть до «@», от 4 символов) или имя
пользователя (от 4 символов). Длинная фраза из обычных слов допускается — длина важнее «сложности».
"""
import re

MIN_DISTINCT = 5

COMMON = frozenset("""
password passw0rd p@ssw0rd pass1234 password1 password12 password123 passwort qwerty qwertyui qwertyuiop qwerty12 qwerty123 qwerty1234
qwertyu1 qwerty1 1qaz2wsx 1q2w3e4r 1q2w3e4r5t 1q2w3e4r5t6y qazwsxedc qazwsx zxcvbnm zxcvbnm1 asdfghjk asdfghjkl asdfgh123 zaq12wsx
iloveyou iloveyou1 letmein welcome welcome1 admin admin123 administrator root toor changeme default master dragon monkey shadow
sunshine princess football baseball superman batman trustno1 abc12345 abcd1234 abcdefgh abcdefg1 abc123456 aaaaaaaa 11111111 00000000
12345678 123456789 1234567890 12345678910 87654321 987654321 0987654321 123123123 123123 1234qwer 1234abcd 12341234 12345qwert 11223344
112233445566 123321123 666666 696969 iloveu 1111111111 0123456789 9876543210 noctis noctis123 noctis1234 nils nils1234 nilsnils alexa alexa123
пароль пароль1 пароль12 пароль123 пароль1234 парольпароль йцукенгшщзх йцукен123 qwertyйцукен 12345678q 1234567q q1w2e3r4 q1w2e3r4t5 qweasdzxc
qweasd123 qweqweqwe qwe123qwe 1qazxsw2 zxcvbnm123 mypassword mypassword1 secret secret123 test1234 test12345 testtest guest guest123 user1234
""".split())

_SEQUENCES = ("0123456789", "9876543210", "abcdefghijklmnopqrstuvwxyz", "zyxwvutsrqponmlkjihgfedcba", "qwertyuiopasdfghjklzxcvbnm",
              "йцукенгшщзхъфывапролджэячсмитьбю", "1qaz2wsx3edc4rfv")


def _is_run(p: str) -> bool:
    """Символы идут подряд по одному шагу вверх или вниз («123456789012», «abcdefgh»; цифры по кругу: …890123…)."""
    if len(p) < 6 or not p.isalnum():
        return False
    steps = {((ord(b) - ord(a)) if not (a.isdigit() and b.isdigit()) else ((int(b) - int(a)) % 10 if (int(b) - int(a)) % 10 in (1, 9) else 99))
             for a, b in zip(p, p[1:])}
    return steps in ({1}, {-1}, {9}, {1, 9}) if p.isdigit() else steps in ({1}, {-1})


def _is_sequence(p: str) -> bool:
    return len(p) >= 6 and (any(p in seq for seq in _SEQUENCES) or _is_run(p))


def _squash(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.lower())


def policy_error(password: str, email: str = "", name: str = "") -> str | None:
    """Текст причины (по-английски, для ответа API) или None, если пароль допустим."""
    p = password.lower()
    base = re.sub(r"[\d\W_]+$", "", p)                       # «password1!» → «password»
    if p in COMMON or base in COMMON or p.lstrip("0123456789") in COMMON:
        return "Password is too common"
    if len(set(p)) < MIN_DISTINCT:
        return "Password uses too few different characters"
    if _is_sequence(p) or _is_sequence(base) and len(base) >= 6:
        return "Password is a simple sequence"
    flat = _squash(password)
    local = _squash(email.split("@")[0]) if email else ""
    if len(local) >= 4 and local in flat:
        return "Password must not contain your email"
    n = _squash(name)
    if len(n) >= 4 and n in flat:
        return "Password must not contain your name"
    return None
