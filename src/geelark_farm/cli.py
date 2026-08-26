"""Command-line entry point.

argparse rather than a CLI framework: the dependency list is already carrying
network, crypto and spreadsheet libraries, and subcommands with a few flags do
not justify another one.

Commands are grouped by what they are for:

  producing phones       build, finish
  the console            ui
  what the sheet holds   pools
  setup and credentials  verify, ping, plan, proxy
  phone lifecycle        phones, create, delete, start, stop, reap
  device diagnostics     dump, tap, shell, type, screenshot
  one step at a time     login, install

It named `run` and `rows`, which were renamed and removed, and omitted
fourteen that exist - while claiming to be the full surface. A test now
checks this list against the parser rather than against nothing.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import sys
import time

from . import __version__, accounts, phones, proxy, screen, shell
from .accounts import AccountError
from .api import ApiError, Client, TransportError, build_client
from .config import REPO_ROOT, ConfigError, Settings
from .flows import google_login, play_install
from .gsheet import GSpreadError, SheetError
from .ledger import Ledger
from .proxy import ProxyError
from .shell import ShellError, TypingError

# Local fallback when no sheet is configured. Same columns as the sheet.
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
    p_build = sub.add_parser(
        "build", help="build N phones from the Gmails/Proxy/Gpt Info tabs"
    )
    p_build.add_argument("--count", type=int, default=1, metavar="N",
                         help="how many phones to build (default: 1)")
    p_build.add_argument("--workers", type=int, metavar="N",
                         help="how many to build at once "
                              "(default: MAX_CONCURRENT_PHONES)")
    p_build.add_argument("--dry-run", action="store_true",
                         help="show what each pool holds, spend nothing")
    p_build.add_argument("--watch", action="store_true",
                         help="print a live-view link and wait for Enter "
                              "before driving each phone")

    p_finish = sub.add_parser(
        "finish", help="sign an app account into phones that are one step short"
    )
    p_finish.add_argument("--limit", type=int, metavar="N",
                          help="finish at most N phones")
    p_finish.add_argument("--workers", type=int, metavar="N",
                          help="how many at once (default: MAX_CONCURRENT_PHONES)")
    p_finish.add_argument("--dry-run", action="store_true",
                          help="show which phones would be finished, spend nothing")

    p_pools = sub.add_parser(
        "pools", help="what the resource tabs hold, and what is stuck"
    )
    p_pools.add_argument("--sync-lists", action="store_true",
                         help="rewrite the Status dropdowns from failures.py, "
                              "so each offers what a run can actually write")
    p_pools.add_argument("--release-stuck", action="store_true",
                         help="free rows a dead run left claimed as in_use. "
                              "Only when no other run is in progress")
    p_pools.add_argument("--no-sync", action="store_true",
                         help="report the tabs as they stand, without first "
                              "bringing them into agreement with the panel")

    # ------------------------------------------------------ diagnostics
    sub.add_parser("ping", help="verify API credentials and list phones")
    sub.add_parser("verify",
                   help="check the whole setup and say what is missing")

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

    sub.add_parser("ui", help="interactive console - every feature, one screen")

    sub.add_parser("plan", help="subscription limits and free slots")

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


@contextlib.contextmanager
def device(settings: Settings, client: Client, requested: str | None):
    """A phone to run one diagnostic command against, and afterwards.

    Three things every one of these needs and five of them did none of:

    - **Refuse a phone something else is driving.** `uiautomator dump` cannot
      run twice at once. `geelark dump` is what you reach for while watching a
      build go wrong - which is when a build is on that phone - and
      `resolve_phone` prefers the running one, so a bare `dump` aims at it
      automatically. The guard existed and only `login` and `install` used it.

    - **Boot it, because shell commands fail confusingly on a stopped phone.**
      That part was already here.

    - **Stop it again if this command is what started it.** `phones.py` says
      anything that starts a phone owns stopping it; these started one and
      walked away, leaving it up with nothing in the ledger accounting for it.
      A phone that was already running is left running - it belongs to
      whatever had it.

    `ensure_running` returns a URL when it did the starting and None when the
    phone was already up, which is exactly the ownership question.
    """
    phone_id = resolve_phone(client, requested)
    refuse_if_busy(settings, phone_id)
    started = phones.ensure_running(client, phone_id)
    if started:
        print(f"started {phone_id} - it will be stopped again afterwards")
    try:
        yield phone_id
    finally:
        if started:
            phones.stop(client, phone_id)
            print(f"stopped {phone_id} - billing ended")


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
        # `or`, not a `.get` default: the key is present and null on a phone
        # GeeLark has not numbered yet, so the default never applies and
        # formatting None raises. Every other reader in the package already
        # writes it this way (2026-08-23).
        line = (f"  {item.get('id')}  "
                f"serial {str(item.get('serialNo') or '?'):>5}  "
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
        # Not claimed. A claim means "a run is working on this right now", and
        # `reapable` reads a fresh one as exactly that and leaves the phone
        # alone - so claiming here started a phone billing and hid it from the
        # reaper for the two hours it takes a claim to go stale. Nothing is
        # working on it; `geelark start` does not claim either.
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
    phones.reap(client, ledger, verdicts=verdicts)
    print(f"\nstopped {len(verdicts)} phone(s) - billing ended")
    return 0


def cmd_ui(settings: Settings, args) -> int:
    """The interactive console.

    Imported here rather than at module load so the plain commands never pay
    for rich, and a broken terminal cannot stop the plain commands from
    working.
    """
    from .ui import run_console
    return run_console(settings)


def cmd_plan(settings: Settings, args) -> int:
    """What the subscription allows, and how much of it is left."""
    client = build_client(settings)
    info = phones.plan(client)
    used_by_phones = len(phones.listing(client))
    total = info.get("profiles") or 0
    free = info.get("availableProfiles") or 0

    # Through the console's helper, so the two say the same thing about a
    # field GeeLark did not send - this printed 1970-01-01 for it. Imported
    # here rather than at the top, the way `cmd_ui` does: rich is a heavy
    # import and every other command pays for it.
    from .ui import plan_expiry

    expires = plan_expiry(info, "%Y-%m-%d")
    print(f"plan            : {'Pro' if info.get('plan') == 1 else 'Base'}"
          f"  (${info.get('monthlyFee')}/month, expires {expires})")
    print(f"profile slots   : {total} total, {free} free")
    print(f"  cloud phones  : {used_by_phones}")
    other = total - free - used_by_phones
    if other > 0:
        # The pool is shared with browser profiles, which this API cannot list -
        # they live behind the local agent. Naming the gap saves the search.
        print(f"  something else: {other}  (browser profiles share this pool; "
              f"check the GeeLark app)")
    parallels = info.get("parallels")
    note = ("" if parallels else
            "  - concurrent phones beyond this may cost extra; check billing "
            "before raising --workers")
    print(f"parallel limit  : {parallels}{note}")
    if free == 0:
        print("\nNo free slots: creating another phone will fail with [44002].")
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
    ledger = Ledger.load(settings.state_dir)
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
        # Release the claim too. Stopping a phone by hand is a deliberate "I am
        # done with this", and leaving the claim set would make every later
        # command refuse the phone as busy until the claim went stale hours on.
        ledger.release(phone_id, note="stopped by hand")
        print(f"stopped {phone_id} - billing ended")
    return 0


def cmd_dump(settings: Settings, args) -> int:
    client = build_client(settings)
    with device(settings, client, args.phone) as phone_id:
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
    with device(settings, client, args.phone) as phone_id:
        elements = screen.read_screen(client, phone_id)
        if screen.tap_label(client, phone_id, elements, args.label):
            return 0
        print(f"no element matching {args.label!r}. On screen:",
              file=sys.stderr)
        for element in elements:
            print(f"   {element.label or f'(empty {element.cls})'}",
                  file=sys.stderr)
    return 1


def cmd_shell(settings: Settings, args) -> int:
    client = build_client(settings)
    with device(settings, client, args.phone) as phone_id:
        # strict: this is a debugging command, and "it printed nothing" and
        # "it did not run" are the two answers you are actually choosing
        # between. It reported success for both.
        print(shell.run(client, phone_id, args.cmd, strict=True), end="")
    return 0


def cmd_type(settings: Settings, args) -> int:
    client = build_client(settings)
    with device(settings, client, args.phone) as phone_id:
        try:
            shell.type_text(client, phone_id, args.text)
        except TypingError as exc:
            print(f"typing: {exc}", file=sys.stderr)
            return 1
        print(f"typed {len(args.text)} character(s) into the focused field")
    return 0


def cmd_screenshot(settings: Settings, args) -> int:
    client = build_client(settings)
    with device(settings, client, args.phone) as phone_id:
        link = phones.screenshot(client, phone_id)
        if not link:
            print("screenshot failed", file=sys.stderr)
            return 1
        print(link)
    return 0


def pick_account(settings: Settings, row: int):
    """The Nth usable Gmail, with a proxy to reach Google through.

    Read, never claimed: this is the diagnostic path, and a debugging session
    that quietly consumed stock from under a running build would be its own
    kind of bug. It means two of these at once would drive the same address,
    which is the caller's business to avoid.

    Falls back to the gitignored TSV so the tool still works before the sheet
    is set up.
    """
    if settings.sheet_id:
        from .pools import Book

        book = Book.open(settings)
        usable = book.gmails.available
        if not 1 <= row <= len(usable):
            raise SystemExit(f"--row {row} is out of range "
                             f"(1..{len(usable)} usable Gmails)")
        found = usable[row - 1]
        exits = book.proxies.available
        if not exits:
            raise SystemExit("no proxy is free in the Proxy tab")
        return accounts.Account(
            email=found.credentials.email,
            password=found.credentials.password,
            totp_secret=found.credentials.totp_secret,
            proxy=str(exits[0].values.get("Proxy String") or ""),
            row=row,
        )

    # From the project root, not from beside `state/`. The two are the same
    # by default and stop being so the moment STATE_DIR points anywhere else -
    # a mounted volume, for one - and then this looks for the file in whatever
    # directory happens to be the parent of that. The same arithmetic-on-a-
    # configured-path that sent `.env` into site-packages (2026-08-23).
    path = REPO_ROOT / DEV_ACCOUNTS
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
        # Same collision as `install`: two flows on one phone corrupt each
        # other's screen reads, and a login is the longer, costlier victim.
        refuse_if_busy(settings, phone_id)
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
        if args.watch:
            # The flag was declared, helped, and never read - `geelark login
            # --watch` took it and drove straight past (2026-08-23). Guarded
            # the way `build --watch` is: with no terminal there is nobody to
            # wait for, and the input would be an EOFError over a phone that
            # is already running.
            if sys.stdin.isatty():
                input("Open it, then press Enter to start the login... ")
            else:
                print("(nothing to wait for - no terminal)", flush=True)
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


def cmd_build(settings: Settings, args) -> int:
    """Build phones out of the resource tabs, rather than one row at a time."""
    from . import builder

    client = build_client(settings)

    def announce(phone_id: str) -> None:
        # Minted here, not at boot: the live-view token expires within seconds.
        url = phones.start(client, phone_id)
        if url:
            print(f"\nWATCH IT LIVE:\n  {url}\n", flush=True)
        if not sys.stdin.isatty():
            # Nobody to wait for. Without this the flag turns a build into an
            # EOFError the moment it runs anywhere without a terminal - a
            # container, a cron entry, a piped shell - and the phone it had
            # just started is left up with the run dead underneath it.
            return
        input("Open it, then press Enter to start this phone... ")

    builds = builder.run(client, settings, count=args.count,
                         workers=args.workers, dry_run=args.dry_run,
                         on_ready=announce if args.watch else None)
    if args.dry_run:
        return 0
    if not builds:
        print("nothing to do - no phone is waiting and the pools are empty")
        return 0
    print(builder.summarise(builds))
    # An empty result is success: a finished pool is the normal state, and
    # exiting non-zero for it would make this unusable from cron.
    return 0 if all(b.ok for b in builds) else 1


def cmd_finish(settings: Settings, args) -> int:
    """Complete phones that have everything but an app account."""
    from . import builder

    client = build_client(settings)
    builds = builder.finish_run(client, settings, limit=args.limit,
                                workers=args.workers, dry_run=args.dry_run)
    if args.dry_run:
        return 0
    if not builds:
        print("nothing to finish - no phone is waiting on an app account")
        return 0
    print(builder.summarise(builds))
    return 0 if all(b.ok for b in builds) else 1


def cmd_pools(settings: Settings, args) -> int:
    """What the resource tabs hold. Spends nothing."""
    from . import builder
    from .pools import Book

    book = Book.open(settings)
    if args.sync_lists:
        for column, values in book.sync_lists().items():
            print(f"{column}: {', '.join(values)}")
        return 0

    if args.release_stuck:
        freed = book.release_stuck()
        print(f"released {freed} row(s) that were left claimed.")
        return 0

    # Corrected before it is reported, so the numbers below are what a run
    # would actually find rather than what the sheet last recorded - and by
    # the same call a run makes, so the two cannot answer differently.
    client = build_client(settings)
    if args.no_sync:
        print("(reporting the tabs as they stand; --no-sync)\n")
    for label, items in ({} if args.no_sync else builder.sync_sheet(
            client, book, Ledger.load(settings.state_dir),
            # A report does not delete phones. `geelark build` carries out the
            # State column, and the console does after showing what it will do.
            apply_marks=False)).items():
        print(f"{label}: {', '.join(items)}")
    book.reload()

    for pool in (book.proxies, book.gmails, book.apps):
        print(f"\n{pool.tab}")
        print(f"  {len(pool.available):>3} available")
        if pool.stuck:
            print(f"  {len(pool.stuck):>3} stuck as in_use - "
                  f"'geelark pools --release-stuck' frees them")
        if pool.broken:
            print(f"  {len(pool.broken):>3} unusable:")
            for resource in pool.broken:
                print(f"      row {resource.sheet_row}: {resource.error}")
    return 0


def cmd_install(settings: Settings, args) -> int:
    """Install the target package, on a phone that must already be signed in.

    Checked up front rather than discovered halfway through: without an account
    the Play Store shows a sign-in wall instead of the package page, and the
    failure would otherwise read as "no Install button".
    """
    client = build_client(settings)
    ledger = Ledger.load(settings.state_dir)
    package = args.package or settings.target_package
    phone_id = resolve_phone(client, args.phone)
    refuse_if_busy(settings, phone_id)
    # Answer the question the line above asks. That guard only works if
    # something on the other side sets a claim for it to find, and this drives
    # the phone for up to `install_budget_seconds` - long enough for a `dump`,
    # a `tap` or a build to arrive, find nothing holding it, and corrupt both
    # screen reads. `login` claimed and this did not (2026-08-27).
    ledger.claim(phone_id, label=f"install {package}")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    artifact_dir = settings.artifact_dir / f"{stamp}-install"
    try:
        # Inside the try, because it starts the phone. Above it, a phone
        # booted here and then turned away at the account check below was
        # left running with the command already dead underneath it - the same
        # walking away `device()` was written to stop.
        phones.ensure_running(client, phone_id)

        accounts_on_device = shell.device_accounts(client, phone_id)
        if not accounts_on_device:
            print(f"phone {phone_id} has no Google account - the Play Store "
                  f"cannot install. Run 'geelark login --row N --phone "
                  f"{phone_id}' first.", file=sys.stderr)
            return 1
        print(f"signed in as {accounts_on_device[0]}")

        if args.watch:
            # Minted here rather than at boot: the live-view token expires
            # within seconds, so it is only useful immediately before the
            # flow acts.
            url = phones.start(client, phone_id)
            if url:
                print(f"\nWATCH IT LIVE:\n  {url}\n", flush=True)
            if sys.stdin.isatty():
                input("Open it, then press Enter here to start the install... ")
            else:
                # `cmd_build` has guarded this since the flag was added, and
                # says why: with no terminal the input is an EOFError over a
                # phone this has just started.
                print("(nothing to wait for - no terminal)", flush=True)

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
        ledger.release(phone_id, note="install finished")
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
    # Through `listing`, which reads every page. Asking the endpoint directly
    # returned one page and then printed the account's true total beside it,
    # so an account with more than a hundred phones was told about all of them
    # and shown a hundred.
    items = phones.listing(client)
    print(f"authenticated as appId {settings.app_id[:6]}...  "
          f"{len(items)} phone(s) visible")

    running = 0
    for item in items:
        status = item.get("status")
        if status == phones.RUNNING:
            running += 1
        equipment = item.get("equipmentInfo") or {}
        print(f"  {item.get('id')}  "
              f"serial {str(item.get('serialNo') or '?'):>5}  "
              f"{PHONE_STATUS.get(status, status):8}  "
              f"{equipment.get('deviceBrand', '?')} "
              f"{equipment.get('osVersion', '?')}")

    if running:
        # Running phones bill per minute, so this is the one thing worth
        # flagging loudly on an otherwise informational command.
        print(f"\n{running} phone(s) RUNNING and billing. "
              f"'geelark stop --all' ends that.")
    return 0


MARKS = {"ok": "ok", "fatal": "FAIL", "warn": "warn",
         "info": "--", "skip": "--"}


def cmd_verify(settings: Settings, args) -> int:
    """Check every part of the setup, in the order they depend on each other.

    Written for someone running this for the first time, or on a new machine.
    Each piece used to fail in its own place at its own time - and two of them,
    a spreadsheet shared as a Viewer and a tab missing a column, only surface
    partway through a build that has already paid for a phone.
    """
    from . import verify

    checks = verify.run_checks(settings)
    width = max(len(c.name) for c in checks)
    for check in checks:
        head, *rest = check.detail.split("\n")
        print(f"  {MARKS.get(check.state, check.state):<5} "
              f"{check.name:<{width}}  {head}")
        # What to do about it, under the line it is about. Indented here
        # rather than in each message, so a detail that carries a newline of
        # its own - an API error usually does - lines up like the rest.
        for line in rest:
            print(f"  {'':<5} {'':<{width}}  {line.strip()}")

    bad = verify.failed(checks)
    if bad:
        print(f"\n{len(bad)} thing(s) to fix: "
              f"{', '.join(c.name for c in bad)}")
        return 1
    if any(c.state == verify.WARN for c in checks):
        print("\nUsable. The warnings above are things a run would stop on, "
              "not things that are broken.")
        return 0
    print("\nEverything checks out.")
    return 0


def _configure_logging(settings: Settings):
    """Console as before, plus a file that keeps everything.

    Until now the log went to the console and died with the terminal. A
    problem hit on one machine was undebuggable from the other - and even on
    the same one, closing the window destroyed the only record. The file gets
    DEBUG whatever LOG_LEVEL says: the console is for watching a run, the file
    is for finding out what happened after the fact, and those want different
    volumes.

    One file per day per machine, appended, with a banner per invocation - so
    "what happened on the Mac yesterday" is one file, not a dig through forty.

    Never fatal: a machine where the directory cannot be written gets console
    logging and a warning, not a dead CLI.
    """
    from .config import machine

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    console = logging.StreamHandler()
    console.setLevel(getattr(logging, settings.log_level, logging.INFO))
    console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(console)

    # Third parties narrate every connection at DEBUG. That is their debugging,
    # not ours, and it would bury a day of real events in socket chatter.
    for noisy in ("urllib3", "google", "googleapiclient", "requests"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    try:
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        path = settings.log_dir / f"{time.strftime('%Y%m%d')}-{machine()}.log"
        file = logging.FileHandler(path, encoding="utf-8")
    except OSError as exc:
        print(f"warning: no log file ({exc}); console only", file=sys.stderr)
        return None
    file.setLevel(logging.DEBUG)
    # The build-context filter is attached here, at creation, so
    # install_build_logging leaves this handler's format alone - it skips
    # handlers that already carry the filter. Without that it would replace
    # this formatter with the console's, and the file would lose its
    # timestamps, which are the point of a file.
    from .builder import BuildContextFilter
    file.addFilter(BuildContextFilter())
    file.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(row)s] %(name)s: %(message)s"))
    root.addHandler(file)
    return path


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

    log_path = _configure_logging(settings)
    if log_path is not None:
        logging.getLogger(__name__).info(
            "geelark %s `%s` - logging to %s", __version__,
            args.command or "", log_path)

    handlers = {
        "ping": cmd_ping,
        "verify": cmd_verify,
        "phones": cmd_phones,
        "create": cmd_create,
        "delete": cmd_delete,
        "start": cmd_start,
        "stop": cmd_stop,
        "reap": cmd_reap,
        "ui": cmd_ui,
        "plan": cmd_plan,
        "proxy": cmd_proxy,
        "dump": cmd_dump,
        "tap": cmd_tap,
        "shell": cmd_shell,
        "type": cmd_type,
        "screenshot": cmd_screenshot,
        "login": cmd_login,
        "install": cmd_install,
        "build": cmd_build,
        "finish": cmd_finish,
        "pools": cmd_pools,
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
    except SheetError as exc:
        print(f"sheet: {exc}", file=sys.stderr)
        return 2
    except GSpreadError as exc:
        # A refusal gspread does not turn into a SheetError - a revoked key, a
        # bad range, a quota surfacing from a read. Named, because the console
        # names it and this is the same failure arriving at the other door.
        print(f"sheet: {exc}", file=sys.stderr)
        return 2
    except ConfigError as exc:
        # Settings.load raises this above, and so does `require_sheets` when a
        # command opens the book - which is most of them.
        print(f"config: {exc}", file=sys.stderr)
        return 2
    except ProxyError as exc:
        print(f"proxy: {exc}", file=sys.stderr)
        return 1
    except phones.PhoneError as exc:
        print(f"phone: {exc}", file=sys.stderr)
        return 1
    except ShellError as exc:
        # A command the device refused to run. Its own line rather than a
        # traceback, because it says something an operator can act on: the
        # phone is up but not answering, so start it, or look at it.
        print(f"device: {exc}", file=sys.stderr)
        return 1
    except ApiError as exc:
        print(f"api: {exc}", file=sys.stderr)
        return 1
    except TransportError as exc:
        print(f"network: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
