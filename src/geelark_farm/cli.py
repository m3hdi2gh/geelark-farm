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
import time

from . import __version__, accounts, phones, proxy, screen, shell
from .accounts import AccountError
from .api import ApiError, Client, TransportError, build_client
from .config import ConfigError, Settings
from .flows import google_login, play_install
from .ledger import Ledger
from .proxy import ProxyError
from .shell import TypingError

# Command -> the roadmap phase that implements it.
PENDING = {
    "rows": 6,
    "run": 7,
}

# Stand-in for the spreadsheet until phase 6. Same columns.
DEV_ACCOUNTS = "secrets/accounts-dev.tsv"


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
    p_phones = sub.add_parser("phones", help="list phones on the account")
    p_phones.add_argument("--ledger", action="store_true",
                          help="also show what the local ledger knows")

    p_create = sub.add_parser(
        "create", help="create a phone bound to a proxy (this costs money)"
    )
    p_create.add_argument("--proxy", required=True, metavar="URL",
                          help="socks5://user:pass@host:port, or host:port:user:pass")
    p_create.add_argument("--name", metavar="NAME")
    p_create.add_argument("--label", metavar="TEXT", default="",
                          help="what this phone is for, recorded in the ledger")
    p_create.add_argument("--start", action="store_true",
                          help="boot it too (starts billing)")

    p_delete = sub.add_parser("delete", help="delete a phone permanently")
    p_delete.add_argument("--phone", required=True, metavar="ID")
    p_delete.add_argument("--yes", action="store_true",
                          help="skip the confirmation prompt")

    p_start = sub.add_parser("start", help="boot a phone (starts billing)")
    p_start.add_argument("--phone", metavar="ID")
    p_start.add_argument("--wait", action="store_true",
                         help="block until it is running and settled")

    p_stop = sub.add_parser("stop", help="stop a phone, ending its billing")
    p_stop.add_argument("--phone", metavar="ID")
    p_stop.add_argument("--all", action="store_true", help="stop every running phone")

    p_reap = sub.add_parser(
        "reap", help="stop running phones that nothing is accountable for"
    )
    p_reap.add_argument("--dry-run", action="store_true",
                        help="report what would be stopped, change nothing")

    p_proxy = sub.add_parser(
        "proxy", help="check a proxy without creating anything"
    )
    p_proxy.add_argument("url", metavar="URL")

    # -------------------------------------------------- single-step flows
    p_login = sub.add_parser("login", help="sign one account in on one phone")
    p_login.add_argument("--row", type=int, required=True, metavar="N",
                         help="which account row to use (1-based)")
    p_login.add_argument("--phone", metavar="ID",
                         help="existing phone; omit to create one on the row's proxy")
    p_login.add_argument("--keep", action="store_true",
                         help="leave the phone running afterwards")
    p_login.add_argument("--watch", action="store_true",
                         help="print the live-view link and wait for Enter before "
                              "driving, so you can open it in time")

    p_install = sub.add_parser(
        "install", help="install the target package from the Play Store"
    )
    p_install.add_argument("--phone", metavar="ID")
    p_install.add_argument("--package", metavar="PKG",
                           help="default: TARGET_PACKAGE from .env")
    p_install.add_argument("--keep", action="store_true",
                           help="leave the phone running afterwards")
    p_install.add_argument("--watch", action="store_true",
                           help="print a fresh live-view link and wait for Enter "
                                "before driving")

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


def refuse_if_busy(settings: Settings, phone_id: str) -> None:
    """Stop a second flow from driving a phone another run is already driving.

    Not a nicety: `uiautomator dump` cannot run twice at once, so two flows on
    one phone corrupt each other's screen reads - and the victim is usually the
    long-running login, which then fails for a reason that has nothing to do
    with Google. The ledger already tracks who holds a phone; this is what that
    claim is for.
    """
    entry = Ledger.load(settings.state_dir).get(phone_id)
    if entry and entry.is_claimed and not entry.is_stale:
        raise SystemExit(
            f"phone {phone_id} is in use by another run "
            f"({entry.label or 'unknown'}).\n"
            f"Wait for it to finish - driving the same phone twice corrupts "
            f"both reads.\n"
            f"If that run is dead, 'geelark reap' will release it."
        )


def cmd_phones(settings: Settings, args) -> int:
    client = build_client(settings)
    ledger = Ledger.load(settings.state_dir)
    # Phones also get deleted from the GeeLark panel directly; drop their
    # entries so the ledger describes what actually exists.
    phones.prune_ledger(client, ledger)
    items = phones.listing(client)
    print(f"{len(items)} phone(s)")
    running = 0
    for item in items:
        state = item.get("status")
        running += state == phones.RUNNING
        equipment = item.get("equipmentInfo") or {}
        line = (f"  {item.get('id')}  serial {item.get('serialNo', '?'):>5}  "
                f"{PHONE_STATUS.get(state, state):8}  "
                f"{equipment.get('deviceBrand', '?')} "
                f"{equipment.get('osVersion', '?')}")
        if args.ledger:
            entry = ledger.get(item.get("id"))
            if entry is None:
                line += "   [not in ledger]"
            elif entry.is_claimed:
                line += f"   [claimed: {entry.label or '-'}]"
            else:
                line += f"   [{entry.label or 'recorded'}]"
        print(line)
    if running:
        print(f"\n{running} RUNNING and billing - 'geelark stop --all' ends that.")
    return 0


def cmd_create(settings: Settings, args) -> int:
    client = build_client(settings)
    parsed = proxy.parse(args.proxy)
    print(f"checking {parsed} before creating anything")
    result = proxy.check(client, parsed)
    print(f"  outbound {result.get('outboundIP')} / "
          f"{result.get('country') or 'unknown country'}")

    ledger = Ledger.load(settings.state_dir)
    entry = phones.create(client, settings, parsed, ledger=ledger,
                          name=args.name, label=args.label)
    print(f"created {entry.phone_id} (serial {entry.serial}), recorded in the ledger")

    if args.start:
        url = phones.start(client, entry.phone_id)
        ledger.claim(entry.phone_id, label=args.label)
        phones.wait_until_running(client, entry.phone_id)
        print(f"running - watch it live:\n  {url}")
        print("remember: 'geelark stop' ends billing")
    return 0


def cmd_delete(settings: Settings, args) -> int:
    client = build_client(settings)
    state = phones.status(client, args.phone)
    if state in (phones.RUNNING, phones.STARTING):
        print(f"phone {args.phone} is {PHONE_STATUS.get(state)} - "
              f"stop it first ('geelark stop --phone {args.phone}')",
              file=sys.stderr)
        return 1
    if not args.yes:
        answer = input(f"permanently delete {args.phone}? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("cancelled")
            return 1
    ledger = Ledger.load(settings.state_dir)
    phones.delete(client, [args.phone], ledger=ledger)
    print(f"deleted {args.phone}")
    return 0


def cmd_reap(settings: Settings, args) -> int:
    client = build_client(settings)
    ledger = Ledger.load(settings.state_dir)
    verdicts = phones.reapable(client, ledger)
    if not verdicts:
        print("nothing to reap - no phone is running unaccounted for")
        return 0
    for phone_id, reason in verdicts:
        print(f"  {phone_id}: {reason}")
    if args.dry_run:
        print(f"\n{len(verdicts)} phone(s) would be stopped (--dry-run)")
        return 0
    phones.reap(client, ledger)
    print(f"\nstopped {len(verdicts)} phone(s) - billing ended")
    return 0


def cmd_proxy(settings: Settings, args) -> int:
    client = build_client(settings)
    parsed = proxy.parse(args.url)
    print(f"{parsed}")
    result = proxy.check(client, parsed)
    outbound = result.get("outboundIP")
    print("  works       : yes")
    print(f"  outbound IP : {outbound}")
    if outbound and outbound != parsed.host:
        print(f"                (a gateway - Google judges the exit IP, "
              f"not {parsed.host})")
    # GeeLark's country lookup returns empty for addresses that resolve fine
    # elsewhere, so it is reported as-is and never treated as a verdict.
    print(f"  country     : {result.get('country') or 'not reported by GeeLark'}")
    print("  reputation  : not checkable here - see docs/runbook.md")
    return 0


def cmd_start(settings: Settings, args) -> int:
    client = build_client(settings)
    phone_id = resolve_phone(client, args.phone)
    url = phones.start(client, phone_id)
    if args.wait:
        phones.wait_until_running(client, phone_id)
    print(f"starting {phone_id} - billing has begun; 'geelark stop' ends it")
    if url:
        print(f"watch it live:\n  {url}")
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


def pick_account(settings: Settings, row: int):
    path = settings.state_dir.parent / DEV_ACCOUNTS
    loaded = accounts.load_dev_accounts(path)
    if not 1 <= row <= len(loaded):
        raise SystemExit(f"--row {row} is out of range (1..{len(loaded)})")
    return loaded[row - 1]


def cmd_login(settings: Settings, args) -> int:
    """Sign one account in, on a phone created for its proxy unless one is given.

    A phone is stopped afterwards unless --keep, and released in the ledger
    either way, so an interrupted experiment cannot leave one billing.
    """
    client = build_client(settings)
    ledger = Ledger.load(settings.state_dir)
    account = pick_account(settings, args.row)
    print(f"account: {account.label}")

    created = False
    if args.phone:
        phone_id = args.phone
    else:
        parsed = proxy.parse(account.proxy)
        result = proxy.check(client, parsed)
        print(f"proxy: {parsed} -> {result.get('outboundIP')}")
        entry = phones.create(client, settings, parsed, ledger=ledger,
                              label=account.label)
        phone_id = entry.phone_id
        created = True
        print(f"created phone {phone_id} (serial {entry.serial})")

    ledger.claim(phone_id, label=account.label)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    artifact_dir = settings.artifact_dir / f"{stamp}-login-row{args.row}"

    try:
        url = phones.ensure_running(client, phone_id)
        if url:
            print(f"watch it live:\n  {url}")
        outcome = google_login.sign_in(
            client, phone_id, account,
            budget_seconds=settings.login_budget_seconds,
            artifact_dir=artifact_dir,
        )
        print(f"\noutcome: {outcome}")
        for path in outcome.artifacts:
            print(f"  saved: {path}")
        link = phones.screenshot(client, phone_id)
        if link:
            print(f"  screen: {link}")
        print(f"  accounts on device: "
              f"{shell.device_accounts(client, phone_id) or 'NONE'}")
        if created and not outcome.ok:
            print(f"  phone kept for inspection: {phone_id} "
                  f"('geelark delete --phone {phone_id}' when done)")
        return 0 if outcome.ok else 1
    finally:
        ledger.release(phone_id, note="login attempt finished")
        if not args.keep:
            phones.stop(client, phone_id)
            print(f"  stopped {phone_id} - billing ended")
        else:
            print(f"  {phone_id} LEFT RUNNING - 'geelark stop' ends billing")


def cmd_install(settings: Settings, args) -> int:
    """Install the target package, on a phone that must already be signed in.

    Checked up front rather than discovered halfway through: without an account
    the Play Store shows a sign-in wall instead of the package page, and the
    failure would otherwise read as "no Install button".
    """
    client = build_client(settings)
    package = args.package or settings.target_package
    phone_id = resolve_phone(client, args.phone)
    refuse_if_busy(settings, phone_id)
    phones.ensure_running(client, phone_id)

    accounts_on_device = shell.device_accounts(client, phone_id)
    if not accounts_on_device:
        print(f"phone {phone_id} has no Google account - the Play Store cannot "
              f"install. Run 'geelark login --row N --phone {phone_id}' first.",
              file=sys.stderr)
        return 1
    print(f"signed in as {accounts_on_device[0]}")

    if args.watch:
        # Minted here rather than at boot: the live-view token expires within
        # seconds, so it is only useful immediately before the flow acts.
        url = phones.start(client, phone_id)
        if url:
            print(f"\nWATCH IT LIVE:\n  {url}\n", flush=True)
        input("Open it, then press Enter here to start the install... ")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    artifact_dir = settings.artifact_dir / f"{stamp}-install"
    try:
        outcome = play_install.install(
            client, phone_id, package,
            budget_seconds=settings.install_budget_seconds,
            artifact_dir=artifact_dir,
        )
        print(f"\noutcome: {outcome}")
        for path in outcome.artifacts:
            print(f"  saved: {path}")
        installed = shell.third_party_packages(client, phone_id)
        print(f"  third-party packages on device: {installed}")
        return 0 if outcome.ok else 1
    finally:
        if not args.keep:
            phones.stop(client, phone_id)
            print(f"  stopped {phone_id} - billing ended")
        else:
            print(f"  {phone_id} LEFT RUNNING - 'geelark stop' ends billing")


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
        "create": cmd_create,
        "delete": cmd_delete,
        "start": cmd_start,
        "stop": cmd_stop,
        "reap": cmd_reap,
        "proxy": cmd_proxy,
        "dump": cmd_dump,
        "tap": cmd_tap,
        "shell": cmd_shell,
        "type": cmd_type,
        "screenshot": cmd_screenshot,
        "login": cmd_login,
        "install": cmd_install,
    }
    handler = handlers.get(args.command)
    if not handler:
        parser.error(f"unknown command {args.command!r}")
        return 2

    try:
        return handler(settings, args)
    except AccountError as exc:
        print(f"account: {exc}", file=sys.stderr)
        return 1
    except ProxyError as exc:
        print(f"proxy: {exc}", file=sys.stderr)
        return 1
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
