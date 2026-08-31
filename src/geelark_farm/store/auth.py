"""Passwords, hashed with what the standard library already ships.

scrypt via hashlib: no new dependency, which is the point - the repo runs
six deps now and fought for every one. The parameters ride beside the hash
in the users table so they can be raised later without invalidating anyone;
`verify` reads them from the row, never from here.
"""

from __future__ import annotations

import hashlib
import hmac
import os

#: Today's cost. 2**14 blocks, the libsodium interactive default - this box
#: has one core and a login should cost tens of milliseconds, not seconds.
SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1
_KEY_LEN = 64


def hash_password(password: str) -> dict:
    """The columns a new user row needs. A fresh salt every call."""
    salt = os.urandom(32)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                            n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
                            dklen=_KEY_LEN)
    return dict(password_hash=digest, password_salt=salt,
                scrypt_n=SCRYPT_N, scrypt_r=SCRYPT_R, scrypt_p=SCRYPT_P)


def verify_password(password: str, row: dict) -> bool:
    """Against the parameters stored WITH the hash, so old rows keep
    verifying after the defaults above are raised. Constant-time compare,
    because a login endpoint is the one place timing is an oracle."""
    digest = hashlib.scrypt(password.encode("utf-8"),
                            salt=bytes(row["password_salt"]),
                            n=row["scrypt_n"], r=row["scrypt_r"],
                            p=row["scrypt_p"], dklen=_KEY_LEN)
    return hmac.compare_digest(digest, bytes(row["password_hash"]))
