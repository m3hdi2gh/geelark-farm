"""The app center's version ids for the apps we uploaded ourselves.

    python scripts/app_versions.py                       # what is written down
    python scripts/app_versions.py --from-phone 2184     # learn them off a phone
    python scripts/app_versions.py --from-phone 2184 --write
    python scripts/app_versions.py --set com.anthropic.claude=2098583520775839746
    python scripts/app_versions.py --forget com.anthropic.claude

GeeLark's `/v1/app/installable/list` is its own catalogue - three hundred
and twenty-five apps, Spotify among them - and an apk uploaded through
`/v1/app/upload` never joins it. `/v1/app/list` answers for one phone, so
a phone with nothing on it cannot be asked either. The id comes back once,
from the upload, and after that there is no call that finds it: this is
where it is written down, and `apps.uploaded` is what reads it.

So the round trip when a fresh copy of ChatGPT or Claude is wanted:

  1. pull the Play-signed splits off a phone that has it (adb, `pm path`),
     zip them into a .xapk - `/v1/app/upload` takes apk and xapk, and
     refuses .apkm;
  2. upload it, install it on one phone by hand from GeeLark's console;
  3. `--from-phone <that phone> --write`, which reads the id back off
     `/v1/app/list` and records it.

Every builder picks the new id up within `apps.STATE_SECONDS`, with no
restart.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from geelark_farm import apps, phones  # noqa: E402
from geelark_farm.api import Client  # noqa: E402
from geelark_farm.config import Settings  # noqa: E402

#: The packages this farm installs that GeeLark's catalogue does not
#: carry. Spotify is not here on purpose: it is GeeLark's own, the
#: listing finds it, and pinning an id for it would only go stale.
OURS = ("com.openai.chatgpt", "com.anthropic.claude")


def written_down(settings: Settings) -> dict[str, str]:
    from geelark_farm.store import state as store_state

    row = store_state.get(settings, apps.STATE_KEY, {}) or {}
    return {str(k): str(v) for k, v in dict(row).items() if v}


def on_phone(client: Client, phone_id: str) -> list[dict]:
    """What `/v1/app/list` says is installed on one phone: the package,
    the version the operator can read, and the id the builder needs."""
    reply = client.post("/v1/app/list", {"envId": phone_id, "page": 1,
                                         "pageSize": 100}, strict=False)
    return (reply.get("data") or {}).get("items") or []


def find_phone(client: Client, said: str) -> str:
    """A serial as a person writes it - "2184" - to GeeLark's env id. An
    id given as-is is passed through."""
    want = said.strip()
    if want.isdigit() and len(want) > 12:
        return want
    for phone in phones.listing(client):
        # GeeLark's serialName is "2184 - TinViper482719".
        name = str(phone.get("serialName") or "")
        if name == want or name.split(" - ")[0].strip() == want:
            return str(phone.get("id") or "")
    raise SystemExit(f"no phone called {said!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-phone", default="",
                        help="read the ids off a phone that has the apps on it")
    parser.add_argument("--set", action="append", default=[],
                        metavar="PACKAGE=ID", help="write one id down")
    parser.add_argument("--forget", action="append", default=[],
                        metavar="PACKAGE", help="drop one package")
    parser.add_argument("--write", action="store_true",
                        help="with --from-phone: record what was read")
    args = parser.parse_args()

    settings = Settings.load()
    if not settings.store_enabled:
        raise SystemExit("STORE_ENABLED is off; there is nowhere to write")

    changed: dict[str, str] = {}
    for pair in args.set:
        package, _, version = pair.partition("=")
        if not package.strip() or not version.strip():
            raise SystemExit(f"--set takes PACKAGE=ID, not {pair!r}")
        changed[package.strip()] = version.strip()
    for package in args.forget:
        changed[package.strip()] = ""

    if args.from_phone:
        client = Client(settings)
        phone_id = find_phone(client, args.from_phone)
        found = {str(item.get("packageName") or ""): item
                 for item in on_phone(client, phone_id)}
        print(f"on phone {args.from_phone} ({phone_id}):")
        for package in OURS:
            item = found.get(package)
            if item is None:
                print(f"  {package}: not installed")
                continue
            version = str(item.get("appVersionId") or "")
            print(f"  {package}: {item.get('versionName')} -> {version}")
            if args.write and version:
                changed[package] = version
        for package, item in found.items():
            if package not in OURS:
                print(f"  {package}: {item.get('versionName')} "
                      f"(GeeLark's own; not recorded)")

    for package, version in changed.items():
        apps.remember(settings, package, version)
        print(f"{'forgot' if not version else 'wrote'} {package}"
              f"{f' = {version}' if version else ''}")

    known = written_down(settings)
    print("\nwritten down now:")
    for package in sorted(known) or ():
        print(f"  {package} = {known[package]}")
    if not known:
        print("  nothing - every app goes through GeeLark's catalogue or Play")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
