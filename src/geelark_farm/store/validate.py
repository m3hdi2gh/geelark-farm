"""Write-time validation: a bad row never enters the store at all.

The sheet validated on READ - `Pool.load` judged every row each pass, and a
row that failed sat in the tab with a blank Status, looking exactly like
free stock to a person. One good account sat out of circulation for days
that way, over a single extra character in its secret (2026-08-31,
Mamadovskii). Moving the same judgement to write time turns that from a
silent days-long hole into a form error the person fixes on the spot.

Nothing here re-implements a rule. Every check CALLS the code that already
holds it - `Credentials.validate`, `normalize_totp_secret`, `proxy.parse` -
because two copies of a rule is how the sheet ended up with its schema
declared in five places. What this module owns is only the translation:
store-shaped dicts in, the same exceptions out, plus the row shaped for the
`resources` table.
"""

from __future__ import annotations

from ..accounts import AccountError, Credentials, normalize_totp_secret
from ..proxy import ProxyError
from ..proxy import parse as parse_proxy

__all__ = ["AccountError", "ProxyError", "gmail_row", "app_row", "proxy_row"]


def gmail_row(*, address: str, password: str, secret: str = "",
              seller: str = "", note: str = "") -> dict:
    """A Gmails row, validated the way GmailPool._interpret judges one.

    The `@`-split is the same decisive test pools.py uses: base32 has no `@`
    in it, and no address is without one - so one Secret cell carries either
    an authenticator key or a recovery address, never ambiguously.
    """
    secret = (secret or "").strip()
    recovery = secret if "@" in secret else ""
    creds = Credentials(
        email=(address or "").strip(),
        password=(password or "").strip(),
        totp_secret="" if recovery else normalize_totp_secret(secret),
        recovery_email=recovery,
    )
    creds.validate(what="gmail")
    _check_seller_promise(seller, creds)
    return dict(kind="gmail", address=creds.email, password=creds.password,
                totp_secret=creds.totp_secret,
                recovery_email=creds.recovery_email,
                seller=(seller or "").strip(), note=note or "")


def app_row(*, address: str, password: str = "", secret: str = "",
            email_code_only: bool = False, note: str = "") -> dict:
    """A Gpt Info row. `email_code_only` is the checkbox: the address is the
    whole credential and a password is not required - the same contract
    AppPool reads off the sheet."""
    creds = Credentials(
        email=(address or "").strip(),
        password=(password or "").strip(),
        totp_secret=normalize_totp_secret((secret or "").strip()),
        email_code_only=email_code_only,
    )
    creds.validate(what="app account")
    return dict(kind="app", address=creds.email, password=creds.password,
                totp_secret=creds.totp_secret,
                email_code_only=email_code_only, note=note or "")


def proxy_row(*, raw: str, name: str = "", note: str = "") -> dict:
    """A Proxy row, through the same parser that reads the sheet's four
    vendor formats. The identity triple (host, port, username) is what the
    partial unique index enforces - the join pools._identity made in
    Python."""
    proxy = parse_proxy(raw)
    return dict(kind="proxy", host=proxy.host, port=proxy.port,
                username=proxy.username or "", proxy_pass=proxy.password or "",
                proxy_name=(name or "").strip(), note=note or "")


def _check_seller_promise(seller: str, creds: Credentials) -> None:
    """The Seller rule, exactly as GmailPool holds it: only a known seller
    carries a promise, and only the WRONG kind is refused - never an empty
    cell, which is how password-only accounts stay welcome."""
    promised = SELLERS.get((seller or "").strip().lower())
    if not promised:
        return
    carries = ("a recovery address" if creds.recovery_email
               else "an authenticator key" if creds.totp_secret else "")
    if carries and carries != promised:
        raise AccountError(
            f"gmail {creds.email}: seller {seller!r} accounts come with "
            f"{promised}, but this one carries {carries} - the Secret cell "
            f"and the Seller disagree, and one of them is a typo")


#: Mirrors GmailPool.SELLERS. Imported here rather than from pools so the
#: store never imports the sheet module - the vocabulary moves to a shared
#: home when pools grows its store backend, and this line is the reminder.
SELLERS = {"usa": "an authenticator key", "egypt": "a recovery address"}
