"""Command-line entry point.

argparse rather than a CLI framework: the dependency list is already carrying
network, crypto and spreadsheet libraries, and subcommands with a few flags do
not justify another one.

Commands are grouped by what they are for:
  running the pipeline   run
  input inspection       rows
  device diagnostics     dump, tap, shell, screenshot
  phone management       phones, stop, reap

Unimplemented commands fail loudly with the phase that will deliver them, so
`geelark --help` doubles as an honest progress report.
"""

from __future__ import annotations

import argparse
import logging
import sys

from . import __version__, phones, screen, shell
from .api import ApiError, Client, TransportError, build_client
from .config import ConfigError, Settings
from .shell import TypingError

# Command -> the roadmap phase that implements it.
PENDING = {
    "reap": 3,
    "login": 4,
    "install": 5,
    "rows": 6,
    "run": 7,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="geelark",
        description="Provision GeeLark cloud phones from a spreadsheet: create "
                    "behind a proxy, sign into Google, install the target app, "
                    "verify, stop.",
        epilog="Billing is per running minute. 'geelark reap' stops anything "
               "left running.",
    )
    parser.add_argument("--version", action="version",
                        version=f"geelark-farm {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # ---------------------------------------------------------- pipeline
    p_run = sub.add_parser(
        "run", help="process every pending row in the sheet (phase 7)"
    )
    p_run.add_argument("--dry-run", action="store_true",
                       help="show what would be processed, spend nothing")
    p_run.add_argument("--limit", type=int, metavar="N",
                       help="process at most N rows")
    p_run.add_argument("--row", type=int, metavar="N",
                       help="process only this sheet row")
    p_run.add_argument("--retry-failed", action="store_true",
                       help="also retry rows marked failed:*")

    # ------------------------------------------------------------- input
    p_rows = sub.add_parser("rows", help="list and validate the sheet rows (phase 6)")
    p_rows.add_argument("--status", metavar="FILTER",
                        help="only rows whose status matches, e.g. pending")

    # ------------------------------------------------------ diagnostics
    sub.add_parser("ping", help="verify API credentials and list phones (phase 1)")

    p_dump = sub.add_parser("dump", help="print every element on screen")
    p_dump.add_argument("--phone", metavar="ID",
                        help="phone id (default: the only running phone)")
    p_dump.add_argument("--save", metavar="PATH",
                        help="also save the raw XML as a test fixture")

    p_tap = sub.add_parser("tap", help="tap the element with this label")
    p_tap.add_argument("label")
    p_tap.add_argument("--phone", metavar="ID")

    p_shell = sub.add_parser("shell", help="run a shell command on a phone")
    p_shell.add_argument("cmd")
    p_shell.add_argument("--phone", metavar="ID")

    p_type = sub.add_parser(
        "type", help="type text into the focused field (checks it is typeable)"
    )
    p_type.add_argument("text")
    p_type.add_argument("--phone", metavar="ID")

    p_shot = sub.add_parser("screenshot", help="capture the screen")
    p_shot.add_argument("--phone", metavar="ID")

    # --------------------------------------------------------- lifecycle
    sub.add_parser("phones", help="list phones on the account")

    p_stop = sub.add_parser("stop", help="stop a phone, ending its billing")
    p_stop.add_argument("--phone", metavar="ID")
    p_stop.add_argument("--all", action="store_true", help="stop every running phone")

    sub.add_parser(
        "reap", help="stop phones that are running but unaccounted for (phase 3)"
    )

    # -------------------------------------------------- single-step flows
    p_login = sub.add_parser(
        "login", help="sign one account in on one phone (phase 4)"
    )
    p_login.add_argument("--phone", metavar="ID")
    p_login.add_argument("--row", type=int, metavar="N",
                         help="take the credentials from this sheet row")

    p_install = sub.add_parser(
        "install", help="install the target package on one phone (phase 5)"
    )
    p_install.add_argument("--phone", metavar="ID")
    p_install.add_argument("--package", metavar="PKG")

    return parser


PHONE_STATUS = phones.STATUS_NAMES


def resolve_phone(client: Client, requested: str | None) -> str:
    """Decide which phone a device command applies to.

    Explicit --phone always wins. Otherwise prefer the single running phone,
    since that is unambiguously the one being worked on; fall back to the
    newest. Refuse to guess when several are running - picking wrong means
    typing a password into the wrong device.
    """
    if requested:
        return requested

    items = phones.listing(client)
    running = [p for p in items if p.get("status") == phones.RUNNING]
    if len(running) == 1:
        phone_id = running[0]["id"]
        print(f"using the running phone {phone_id}")
        return phone_id
    if len(running) > 1:
        ids = ", ".join(p["id"] for p in running)
        raise SystemExit(f"several phones are running ({ids}) - pass --phone")

    newest = phones.newest(client)
    if not newest:
        raise SystemExit("no phones on this account")
    print(f"no phone running; using the newest, {newest['id']}")
    return newest["id"]


def with_device(client: Client, requested: str | None) -> str:
    """Resolve a phone and make sure it is running, because shell commands
    fail confusingly on a stopped one."""
    phone_id = resolve_phone(client, requested)
    phones.ensure_running(client, phone_id)
    return phone_id


def cmd_phones(settings: Settings, args) -> int:
    client = build_client(settings)
    items = phones.listing(client)
    print(f"{len(items)} phone(s)")
    running = 0
    for item in items:
        state = item.get("status")
        running += state == phones.RUNNING
        equipment = item.get("equipmentInfo") or {}
        print(f"  {item.get('id')}  serial {item.get('serialNo', '?'):>5}  "
              f"{PHONE_STATUS.get(state, state):8}  "
              f"{equipment.get('deviceBrand', '?')} "
              f"{equipment.get('osVersion', '?')}")
    if running:
        print(f"\n{running} RUNNING and billing - 'geelark stop --all' ends that.")
    return 0


def cmd_stop(settings: Settings, args) -> int:
    client = build_client(settings)
    if args.all:
        targets = [p["id"] for p in phones.listing(client)
                   if p.get("status") in (phones.RUNNING, phones.STARTING)]
        if not targets:
            print("nothing is running")
            return 0
    else:
        targets = [resolve_phone(client, args.phone)]
    for phone_id in targets:
        phones.stop(client, phone_id)
        print(f"stopped {phone_id} - billing ended")
    return 0


def cmd_dump(settings: Settings, args) -> int:
    client = build_client(settings)
    phone_id = with_device(client, args.phone)
    xml = screen.capture(client, phone_id)
    if not xml:
        print("could not read the view hierarchy", file=sys.stderr)
        return 1
    elements = screen.parse(xml)
    print(f"{len(elements)} element(s)   "
          f"* clickable  ! disabled  > focused  # password\n")
    for element in elements:
        print(element)
    if args.save:
        saved = screen.save_fixture(xml, args.save)
        print(f"\nfixture saved: {saved}")
    return 0


def cmd_tap(settings: Settings, args) -> int:
    client = build_client(settings)
    phone_id = with_device(client, args.phone)
    elements = screen.read_screen(client, phone_id)
    if screen.tap_label(client, phone_id, elements, args.label):
        return 0
    print(f"no element matching {args.label!r}. On screen:", file=sys.stderr)
    for element in elements:
        print(f"   {element.label or f'(empty {element.cls})'}", file=sys.stderr)
    return 1


def cmd_shell(settings: Settings, args) -> int:
    client = build_client(settings)
    phone_id = with_device(client, args.phone)
    print(shell.run(client, phone_id, args.cmd), end="")
    return 0


def cmd_type(settings: Settings, args) -> int:
    client = build_client(settings)
    phone_id = with_device(client, args.phone)
    try:
        shell.type_text(client, phone_id, args.text)
    except TypingError as exc:
        print(f"typing: {exc}", file=sys.stderr)
        return 1
    print(f"typed {len(args.text)} character(s) into the focused field")
    return 0


def cmd_screenshot(settings: Settings, args) -> int:
    client = build_client(settings)
    phone_id = with_device(client, args.phone)
    link = phones.screenshot(client, phone_id)
    if not link:
        print("screenshot failed", file=sys.stderr)
        return 1
    print(link)
    return 0


def cmd_ping(settings: Settings, args) -> int:
    """Prove the credentials sign correctly, and show what they can see.

    Listing phones is the cheapest call that exercises the whole path -
    signing, rate limiter, envelope unwrapping - without starting anything.
    """
    client = build_client(settings)
    data = client.data("/v1/phone/list", {"page": 1, "pageSize": 100})
    items = data.get("items") or []
    print(f"authenticated as appId {settings.app_id[:6]}...  "
          f"{data.get('total', len(items))} phone(s) visible")

    running = 0
    for item in items:
        status = item.get("status")
        if status == 0:
            running += 1
        equipment = item.get("equipmentInfo") or {}
        print(f"  {item.get('id')}  serial {item.get('serialNo', '?'):>5}  "
              f"{PHONE_STATUS.get(status, status):8}  "
              f"{equipment.get('deviceBrand', '?')} "
              f"{equipment.get('osVersion', '?')}")

    if running:
        # Running phones bill per minute, so this is the one thing worth
        # flagging loudly on an otherwise informational command.
        print(f"\n{running} phone(s) RUNNING and billing. "
              f"'geelark stop --all' ends that (phase 3).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    # Load settings for every command, so credential problems surface before
    # anything else - including for commands that are not implemented yet.
    try:
        settings = Settings.load()
    except ConfigError as exc:
        print(f"config: {exc}", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
    )

    phase = PENDING.get(args.command)
    if phase:
        print(f"'{args.command}' is not implemented yet - it lands in phase {phase}.")
        print("See docs/roadmap.md for what each phase delivers.")
        return 1

    handlers = {
        "ping": cmd_ping,
        "phones": cmd_phones,
        "stop": cmd_stop,
        "dump": cmd_dump,
        "tap": cmd_tap,
        "shell": cmd_shell,
        "type": cmd_type,
        "screenshot": cmd_screenshot,
    }
    handler = handlers.get(args.command)
    if not handler:
        parser.error(f"unknown command {args.command!r}")
        return 2

    try:
        return handler(settings, args)
    except phones.PhoneError as exc:
        print(f"phone: {exc}", file=sys.stderr)
        return 1
    except ApiError as exc:
        print(f"api: {exc}", file=sys.stderr)
        return 1
    except TransportError as exc:
        print(f"network: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
