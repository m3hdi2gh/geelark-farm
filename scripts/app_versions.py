"""The app center's version ids for the apps we uploaded ourselves.

    python scripts/app_versions.py                       # what is written down
    python scripts/app_versions.py --check 2400          # ours against a phone's
    python scripts/app_versions.py --from-phone 2400 --write
    python scripts/app_versions.py --set com.anthropic.claude=209858...746=1.260910.12
    python scripts/app_versions.py --forget com.anthropic.claude

GeeLark's `/v1/app/installable/list` is its own catalogue - three hundred
and twenty-five apps, Spotify among them - and an apk uploaded through
`/v1/app/upload` never joins it. `/v1/app/list` answers for one phone, and
only for what the center installed there: a phone that got ChatGPT from
the Play Store does not list it at all (measured on phones 2315 and 2332,
2026-09-12). So the id comes back once, from the upload, and after that
there is no call that finds it: this is where it is written down, and
`apps.uploaded` is what reads it.

--check is the recurring question. Build a phone, install the three apps
from the Play Store by hand, and point this at it: it reads each app's
version off the device itself - `dumpsys package`, which does not care
where the app came from - and says which of ours Play has moved past.

Then the round trip for one that has:

  1. pull the Play-signed splits off that phone (adb, `pm path`) and zip
     them into a .xapk - `/v1/app/upload` takes apk and xapk, and refuses
     .apkm;
  2. upload it, and install it on one phone from GeeLark's console;
  3. `--from-phone <that phone> --write`, which reads the new id and
     version back off `/v1/app/list` and records them.

Every builder picks the new id up within `apps.STATE_SECONDS`, with no
restart.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from geelark_farm import apps, phones, shell  # noqa: E402
from geelark_farm.api import Client  # noqa: E402
from geelark_farm.config import Settings  # noqa: E402

#: The packages this farm installs that GeeLark's catalogue does not
#: carry, and what each is called. Spotify is not here on purpose: it is
#: GeeLark's own, the listing finds it, and pinning an id for it would
#: only go stale.
OURS = {"com.openai.chatgpt": "ChatGPT", "com.anthropic.claude": "Claude"}
#: What `dumpsys package` answers with.
_VERSION = re.compile(r"versionName=(\S+)")


def on_phone(client: Client, phone_id: str) -> list[dict]:
    """What `/v1/app/list` says the center put on one phone: the package,
    the version a person can read, and the id the builder needs."""
    reply = client.post("/v1/app/list", {"envId": phone_id, "page": 1,
                                         "pageSize": 100}, strict=False)
    return (reply.get("data") or {}).get("items") or []


def installed_version(client: Client, phone_id: str, package: str) -> str:
    """The version of `package` as the device itself reports it, or ""
    when it is not there. Asked of the device rather than of GeeLark
    because an app the Play Store installed is in neither listing."""
    try:
        said = shell.run(client, phone_id,
                         f"dumpsys package {package} | grep -m1 versionName",
                         strict=False) or ""
    except Exception as exc:                                       # noqa: BLE001
        raise SystemExit(f"could not ask the phone about {package} ({exc}). "
                         f"It has to be running.") from exc
    found = _VERSION.search(said)
    return found.group(1) if found else ""


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


def show(known: dict) -> None:
    print("\nwritten down now:")
    for package in sorted(known):
        entry = known[package]
        version = f" ({entry['version']})" if entry["version"] else ""
        when = f", recorded {entry['at']}" if entry["at"] else ""
        print(f"  {OURS.get(package, package)}: {package} = "
              f"{entry['id']}{version}{when}")
    if not known:
        print("  nothing - every app goes through GeeLark's catalogue or Play")


def check(client: Client, known: dict, serial: str) -> tuple[int, int]:
    """Ours against what is on one phone. Returns how many differ and how
    many could not be compared at all."""
    phone_id = find_phone(client, serial)
    print(f"phone {serial} ({phone_id}) against what we uploaded:\n")
    differ = unknown = 0
    for package, name in OURS.items():
        theirs = installed_version(client, phone_id, package)
        entry = known.get(package) or {"id": "", "version": "", "at": ""}
        ours = entry["version"]
        if not theirs:
            said = "not installed on that phone - nothing to compare"
            unknown += 1
        elif not entry["id"]:
            said = "we have never uploaded this one"
            unknown += 1
        elif not ours:
            said = ("we never wrote down which version ours is - "
                    "--from-phone a phone that has it, with --write")
            unknown += 1
        elif theirs == ours:
            said = "same - nothing to do"
        else:
            # Which way round is not something a version string can be
            # asked; the two are printed and the operator reads them.
            said = "DIFFERENT - worth extracting and uploading"
            differ += 1
        print(f"  {name:<8} phone {theirs or '-':<14} "
              f"ours {ours or '-':<14} {said}")
    return differ, unknown


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", default="", metavar="SERIAL",
                        help="compare ours against a running phone's apps")
    parser.add_argument("--from-phone", default="", metavar="SERIAL",
                        help="read the center's ids off a phone")
    parser.add_argument("--set", action="append", default=[],
                        metavar="PACKAGE=ID[=VERSION]",
                        help="write one id down")
    parser.add_argument("--forget", action="append", default=[],
                        metavar="PACKAGE", help="drop one package")
    parser.add_argument("--write", action="store_true",
                        help="with --from-phone: record what was read")
    args = parser.parse_args()

    settings = Settings.load()
    if not settings.store_enabled:
        raise SystemExit("STORE_ENABLED is off; there is nowhere to write")

    changed: dict[str, tuple[str, str]] = {}
    for pair in args.set:
        package, _, rest = pair.partition("=")
        version_id, _, version_name = rest.partition("=")
        if not package.strip() or not version_id.strip():
            raise SystemExit(f"--set takes PACKAGE=ID[=VERSION], not {pair!r}")
        changed[package.strip()] = (version_id.strip(), version_name.strip())
    for package in args.forget:
        changed[package.strip()] = ("", "")

    client = None
    if args.check or args.from_phone:
        client = Client(settings)

    if args.check:
        differ, unknown = check(client, apps.recorded(settings), args.check)
        print(f"\n{differ or 'none'} of ours "
              f"{'differs' if differ == 1 else 'differ'} from that phone"
              + (f", and {unknown} could not be compared." if unknown
                 else "."))

    if args.from_phone:
        phone_id = find_phone(client, args.from_phone)
        found = {str(item.get("packageName") or ""): item
                 for item in on_phone(client, phone_id)}
        print(f"\nthe center's apps on phone {args.from_phone} ({phone_id}):")
        for package in OURS:
            item = found.get(package)
            if item is None:
                print(f"  {package}: the center did not install it here")
                continue
            version_id = str(item.get("appVersionId") or "")
            version_name = str(item.get("versionName") or "")
            print(f"  {package}: {version_name} -> {version_id}")
            if args.write and version_id:
                changed[package] = (version_id, version_name)
        for package, item in found.items():
            if package not in OURS:
                print(f"  {package}: {item.get('versionName')} "
                      f"(GeeLark's own; not recorded)")

    for package, (version_id, version_name) in changed.items():
        apps.remember(settings, package, version_id, version_name=version_name)
        print(f"{'forgot' if not version_id else 'wrote'} {package}"
              f"{f' = {version_id} ({version_name})' if version_id else ''}")

    show(apps.recorded(settings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
