"""People, and what each of them may do.

Two axes came with the users table - role (admin / operator) and sight
(all / own) - and they answer "what may this person see". This module adds
the third question, "what may this person DO", as six booleans an admin
ticks per operator. One function answers it, `may`, and every mutating
surface built after this asks it rather than reading columns: the Users
page is where the answer is set, this is the only place it is read.

Passwords never leave the database. A create or a reset mints a one-time
password, returns it once to the admin who asked, and marks the row
`must_change_password` - the person's first act after signing in is to
choose their own, and until they do every other page redirects there.
"""

from __future__ import annotations

import re
import secrets

from ..config import Settings
from .db import connect

#: The six things an operator may be allowed to do, in the order the Users
#: page lists them: (column, label, what it unlocks). An admin has all of
#: them implicitly and drives the service besides.
PERMISSIONS: tuple[tuple[str, str, str], ...] = (
    ("may_add_gmail", "add gmails", "the Gmail Pool add form"),
    ("may_add_gpt", "add GPT accounts", "the manual section of Gpt Pool"),
    ("may_add_proxy", "add proxies",
     "and answer the unlisted / needs-new-IP lists"),
    ("may_login_accounts", "log accounts in",
     "select accounts and boot warm phones for them"),
    ("may_change_proxy", "change a phone's proxy", ""),
    ("may_take_phones", "take phones", "mark a phone taken, done or failed"),
)
PERMISSION_COLUMNS = tuple(column for column, _, _ in PERMISSIONS)

ROLES = ("admin", "operator")
SIGHTS = ("all", "own")

#: What a username may look like: the same shape the log and History tabs
#: already carry for machine names, so a name never needs escaping twice.
USERNAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,31}$")

#: The floor store-init sets for the first admin, applied to everyone.
PASSWORD_MIN = 8

#: Length of a minted one-time password, in random bytes before encoding -
#: twelve url-safe characters, enough to be unguessable and short enough
#: to be read off a screen once.
_ONE_TIME_BYTES = 9


def may(user: dict | None, permission: str) -> bool:
    """Whether this person may do the thing. The one place the answer lives.

    An admin may do everything; an operator may do what is ticked; nobody
    who is deactivated or absent may do anything. Unknown permission names
    are False rather than an error - a typo must fail closed.
    """
    # The vocabulary check comes first, before the admin shortcut: a name
    # nobody defined must be False for everyone, or a typo in a caller
    # would quietly grant admins something that does not exist.
    if permission not in PERMISSION_COLUMNS:
        return False
    if not user or not user.get("active", True):
        return False
    if user.get("role") == "admin":
        return True
    return bool(user.get(permission))


def mint_password() -> str:
    return secrets.token_urlsafe(_ONE_TIME_BYTES)


# ------------------------------------------------------------------- reads
_LISTING = (
    "SELECT id, username, role, sees, active, must_change_password,"
    " last_login_at, created_at, " + ", ".join(PERMISSION_COLUMNS) +
    " FROM users")


#: Admins first, then operators, the deactivated last - the order the
#: Users page lists people in.
LISTING_ORDER = " ORDER BY active DESC, (role = 'admin') DESC, username"


def listing(settings: Settings) -> list[dict]:
    with connect(settings) as conn:
        cur = conn.execute(_LISTING + LISTING_ORDER)
        names = [d.name for d in cur.description]
        rows = [dict(zip(names, r, strict=True)) for r in cur.fetchall()]
        conn.rollback()
        return rows


def get(settings: Settings, user_id: int) -> dict | None:
    with connect(settings) as conn:
        cur = conn.execute(_LISTING + " WHERE id = %s", (user_id,))
        row = cur.fetchone()
        names = [d.name for d in cur.description]
        conn.rollback()
        return dict(zip(names, row, strict=True)) if row else None


# ------------------------------------------------------------------ writes
def create(settings: Settings, *, username: str, role: str, sees: str,
           permissions: dict) -> tuple[int, str]:
    """Make a person and return (id, one-time password).

    The password is returned exactly once, to the admin who asked, and is
    not kept anywhere in the clear. The row starts must_change_password.
    """
    from . import auth

    if not USERNAME.match(username):
        raise ValueError("a username is 2-32 characters: lowercase letters, "
                         "digits, dot, dash or underscore")
    if role not in ROLES or sees not in SIGHTS:
        raise ValueError("role must be admin or operator; sees all or own")
    password = mint_password()
    hashed = auth.hash_password(password)
    ticks = {c: bool(permissions.get(c)) for c in PERMISSION_COLUMNS}
    columns = ["username", "password_hash", "password_salt", "scrypt_n",
               "scrypt_r", "scrypt_p", "role", "sees",
               "must_change_password", *ticks]
    values = [username, hashed["password_hash"], hashed["password_salt"],
              hashed["scrypt_n"], hashed["scrypt_r"], hashed["scrypt_p"],
              role, sees, True, *ticks.values()]
    with connect(settings) as conn:
        cur = conn.execute(
            f"INSERT INTO users ({', '.join(columns)})"
            f" VALUES ({', '.join(['%s'] * len(values))}) RETURNING id",
            values)
        new_id = cur.fetchone()[0]
        conn.commit()
    return new_id, password


def update(settings: Settings, user_id: int, *, role: str, sees: str,
           active: bool, permissions: dict, by: int) -> None:
    """Change what a person is and may do.

    Two refusals protect the admin from themselves: nobody may deactivate
    or demote their own account, and no change may leave the farm with no
    active admin at all - the one way to lock everyone out for good.
    """
    if role not in ROLES or sees not in SIGHTS:
        raise ValueError("role must be admin or operator; sees all or own")
    if user_id == by and (not active or role != "admin"):
        raise ValueError("you cannot deactivate or demote yourself")
    ticks = {c: bool(permissions.get(c)) for c in PERMISSION_COLUMNS}
    with connect(settings) as conn:
        if role != "admin" or not active:
            cur = conn.execute(
                "SELECT count(*) FROM users WHERE role = 'admin' AND active"
                " AND id <> %s", (user_id,))
            if cur.fetchone()[0] == 0:
                conn.rollback()
                raise ValueError("that would leave no active admin")
        sets = ["role = %s", "sees = %s", "active = %s"]
        params: list = [role, sees, active]
        for column, value in ticks.items():
            sets.append(f"{column} = %s")
            params.append(value)
        params.append(user_id)
        conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = %s",
                     params)
        conn.commit()


def reset_password(settings: Settings, user_id: int) -> str:
    """Mint a new one-time password for a person and return it once."""
    from . import auth

    password = mint_password()
    hashed = auth.hash_password(password)
    with connect(settings) as conn:
        conn.execute(
            "UPDATE users SET password_hash = %s, password_salt = %s,"
            " scrypt_n = %s, scrypt_r = %s, scrypt_p = %s,"
            " must_change_password = true WHERE id = %s",
            (hashed["password_hash"], hashed["password_salt"],
             hashed["scrypt_n"], hashed["scrypt_r"], hashed["scrypt_p"],
             user_id))
        conn.commit()
    return password


def set_password(settings: Settings, user_id: int, password: str) -> None:
    """A person choosing their own. Clears must_change_password."""
    from . import auth

    if len(password) < PASSWORD_MIN:
        raise ValueError(f"a password needs at least {PASSWORD_MIN} "
                         f"characters")
    hashed = auth.hash_password(password)
    with connect(settings) as conn:
        conn.execute(
            "UPDATE users SET password_hash = %s, password_salt = %s,"
            " scrypt_n = %s, scrypt_r = %s, scrypt_p = %s,"
            " must_change_password = false WHERE id = %s",
            (hashed["password_hash"], hashed["password_salt"],
             hashed["scrypt_n"], hashed["scrypt_r"], hashed["scrypt_p"],
             user_id))
        conn.commit()
