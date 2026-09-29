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
import unicodedata

from ..config import Settings
from .db import connect

#: The five things an operator may be allowed to do, in the order the Users
#: page lists them: (column, label, what it unlocks). An admin has all of
#: them implicitly and drives the service besides.
PERMISSIONS: tuple[tuple[str, str, str], ...] = (
    ("may_add_gmail", "add gmails", "the Gmail Pool add form"),
    ("may_add_gpt", "add GPT accounts", "the manual section of Gpt Pool"),
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

#: The longest display name the Station's profile takes.
NAME_MAX = 24
#: A username a person chooses for themselves: 3-20 of a-z 0-9 . _,
#: starting with a letter or a digit. Admin create keeps USERNAME.
SELF_USERNAME = re.compile(r"^[a-z0-9][a-z0-9._]{2,19}$")
#: The shape alone, so a name that only starts wrong is told so.
_SELF_SHAPE = re.compile(r"^[a-z0-9._]{3,20}$")
#: Bidi controls that would turn the rest of a line around: U+202A-202E and
#: U+2066-2069. ZWNJ and ZWJ are not among them - Persian names use them.
_BIDI = frozenset(chr(c) for c in (*range(0x202A, 0x202F),
                                   *range(0x2066, 0x206A)))

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


class WrongPassword(ValueError):
    """The current password was not right."""


def shown_name(user: dict) -> str:
    """The name a page greets a person by: theirs, or their username."""
    return (str(user.get("display_name") or "").strip()
            or str(user.get("username") or "") or "?")


def clean_name(text: str) -> str:
    """Stripped; 1-24 characters; no control or bidi-control characters
    (ZWNJ and ZWJ are allowed - Persian names use them)."""
    name = str(text or "").strip()
    if not name:
        raise ValueError("Your name cannot be empty.")
    if len(name) > NAME_MAX:
        raise ValueError("Your name is at most 24 characters.")
    if any(unicodedata.category(ch) == "Cc" or ch in _BIDI for ch in name):
        raise ValueError("Your name has a character that cannot be shown.")
    return name


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
            " must_change_password = true, password_changed_at = now()"
            " WHERE id = %s",
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
            " must_change_password = false, password_changed_at = now()"
            " WHERE id = %s",
            (hashed["password_hash"], hashed["password_salt"],
             hashed["scrypt_n"], hashed["scrypt_r"], hashed["scrypt_p"],
             user_id))
        conn.commit()


# ----------------------------------------------------- the person's own
def set_name(settings: Settings, user_id: int, name: str) -> str:
    """Save the name a person is greeted by. Returns the saved name."""
    with connect(settings) as conn:
        row = conn.execute(
            "UPDATE users SET display_name = %s WHERE id = %s AND active"
            " RETURNING display_name", (name, int(user_id))).fetchone()
        conn.commit()
    if row is None:
        raise ValueError("Your account is not active.")
    return str(row[0])


def _verified(settings: Settings, user_id: int, current: str) -> dict:
    """The person's password columns, once `current` has matched them."""
    from . import auth

    with connect(settings) as conn:
        cur = conn.execute(
            "SELECT password_hash, password_salt, scrypt_n, scrypt_r, scrypt_p"
            " FROM users WHERE id = %s AND active", (int(user_id),))
        row = cur.fetchone()
        names = [d.name for d in cur.description]
        conn.rollback()
    found = dict(zip(names, row, strict=True)) if row is not None else None
    if found is None or not auth.verify_password(str(current or ""), found):
        raise WrongPassword("That is not your current password.")
    return found


def set_username(settings: Settings, user_id: int, username: str) -> str:
    """A person renaming themselves. Unique whatever its case; no current
    password is asked (the prototype's one-field form, the lead's call).
    Returns the saved username."""
    new = str(username or "").strip().lower()
    if not _SELF_SHAPE.match(new):
        raise ValueError("3 to 20 small letters, digits, dots or underscores.")
    if not SELF_USERNAME.match(new):
        raise ValueError("Start it with a letter or a digit.")
    with connect(settings) as conn:
        now = conn.execute("SELECT username FROM users WHERE id = %s AND active",
                           (int(user_id),)).fetchone()
        if now is not None and now[0] == new:
            conn.rollback()
            return new
        try:
            row = conn.execute(
                "UPDATE users SET username = %s WHERE id = %s AND active"
                "   AND NOT EXISTS (SELECT 1 FROM users o"
                "                    WHERE lower(o.username) = %s AND o.id <> %s)"
                " RETURNING username",
                (new, int(user_id), new, int(user_id))).fetchone()
        except Exception as exc:
            conn.rollback()
            if type(exc).__name__ == "UniqueViolation":
                raise ValueError("That username is taken.") from exc
            raise
        if row is None:
            conn.rollback()
            raise ValueError("That username is taken.")
        conn.commit()
    return str(row[0])


def change_password(settings: Settings, user_id: int, current: str, new: str,
                    *, token: str, hours: float) -> str | None:
    """A person changing their own password: the current one first, then
    the new hash and this browser's new seat in one transaction, which
    ends every other seat. Returns the new raw token, or None when this
    browser's seat was already gone (every seat is then ended, and the
    password is still changed). Any failure changes nothing."""
    from . import auth, sessions

    row = _verified(settings, user_id, current)
    if len(new) < PASSWORD_MIN:
        raise ValueError("The new password needs at least 8 characters.")
    if new == current:
        raise ValueError("The new password is the same as the current one.")
    if len(new) > 256:
        raise ValueError("The new password is at most 256 characters.")
    hashed = auth.hash_password(new)
    with connect(settings) as conn:
        changed = conn.execute(
            "UPDATE users SET password_hash = %s, password_salt = %s,"
            " scrypt_n = %s, scrypt_r = %s, scrypt_p = %s,"
            " must_change_password = false, password_changed_at = now()"
            " WHERE id = %s AND password_hash = %s"
            " RETURNING id",
            (hashed["password_hash"], hashed["password_salt"],
             hashed["scrypt_n"], hashed["scrypt_r"], hashed["scrypt_p"],
             int(user_id), row["password_hash"])).fetchone()
        if changed is None:
            conn.rollback()
            raise ValueError("Your password was changed somewhere else a moment"
                             " ago - reload and try again.")
        seat = sessions.rotate(conn, token, int(user_id), hours=hours)
        conn.commit()
    return seat
