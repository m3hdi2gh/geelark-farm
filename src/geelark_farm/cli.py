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

from . import __version__
from .api import ApiError, TransportError, build_client
from .config import ConfigError, Settings

# Command -> the roadmap phase that implements it. Phase 0 ships the skeleton.
PENDING = {
    "dump": 2,
    "tap": 2,
    "shell": 2,
    "screenshot": 2,
    "phones": 3,
    "stop": 3,
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

    p_dump = sub.add_parser(
        "dump", help="print every labelled element on screen (phase 2)"
    )
    p_dump.add_argument("--phone", metavar="ID", help="phone id (default: ledger)")
    p_dump.add_argument("--save", metavar="PATH",
                        help="also save the raw XML as a test fixture")

    p_tap = sub.add_parser("tap", help="tap the element with this label (phase 2)")
    p_tap.add_argument("label")
    p_tap.add_argument("--phone", metavar="ID")

    p_shell = sub.add_parser("shell", help="run a shell command on a phone (phase 2)")
    p_shell.add_argument("cmd")
    p_shell.add_argument("--phone", metavar="ID")

    p_shot = sub.add_parser("screenshot", help="capture the screen (phase 2)")
    p_shot.add_argument("--phone", metavar="ID")

    # --------------------------------------------------------- lifecycle
    sub.add_parser("phones", help="list phones on the account (phase 3)")

    p_stop = sub.add_parser("stop", help="stop a phone, ending its billing (phase 3)")
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


PHONE_STATUS = {0: "running", 1: "starting", 2: "stopped", 3: "expired"}


def cmd_ping(settings: Settings) -> int:
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

    handlers = {"ping": cmd_ping}
    handler = handlers.get(args.command)
    if not handler:
        parser.error(f"unknown command {args.command!r}")
        return 2

    try:
        return handler(settings)
    except ApiError as exc:
        print(f"api: {exc}", file=sys.stderr)
        return 1
    except TransportError as exc:
        print(f"network: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
