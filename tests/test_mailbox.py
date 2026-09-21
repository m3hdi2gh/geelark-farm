"""The mailbox the eco accounts' codes arrive in.

Every fixture here is the shape of a real forwarded message, read off
the live mailbox on 2026-09-19 before a line of `mailbox.py` was
written: AdGuard's relay wrapper, HTML and no plain-text part, the
sender rewritten to the alias, and markup carrying six-digit runs that
are not the code.
"""
from __future__ import annotations

import email
import time

import pytest

from geelark_farm import mailbox

#: The relay's own furniture, with the digits that fooled the first
#: reader: a CDN path and a pixel width, both six digits, both in the
#: markup and neither visible.
WRAPPER = (
    '<!DOCTYPE html><html><head><style>.x{{padding:100024px}}</style></head>'
    '<body><table style="width:100025px"><tr><td>'
    '<img src="https://cdn.adguardcdn.com/website/emails/100026.png">'
    '<span>Forwarded by AdGuard Mail</span> <span>Alias: {alias}</span> '
    '<span>From: noreply@tm.openai.com</span> <a href="#">Block this '
    'sender</a></td></tr><tr><td>{body}</td></tr></table></body></html>')


def message(alias: str, body: str, *, subject: str = "Your temporary "
            "ChatGPT login code", when: float | None = None,
            to: str | None = None) -> email.message.Message:
    """One forwarded message, as AdGuard sends them."""
    stamp = email.utils.formatdate(when if when is not None else time.time())
    raw = (
        f'From: "noreply@tm.openai.com [via AdGuard Mail]"'
        f' <{alias.split("@")[0]}_4uikxxaqg4jo@masked.me>\n'
        f"To: {to or alias}\n"
        f"Delivered-To: purplespoty@gmail.com\n"
        f"Subject: {subject}\n"
        f"Date: {stamp}\n"
        f'Content-Type: text/html; charset="UTF-8"\n\n'
        + WRAPPER.format(alias=alias, body=body))
    return email.message_from_string(raw)


CODE_BODY = ("<p>Enter this temporary verification code to continue:</p>"
             "<h1>314159</h1><p>If you were not trying to log in to ChatGPT, "
             "please reset your password.</p>")
PERSIAN_BODY = ("<p>کد ورود موق"
                "ت شما:</p><h1>271828</h1>")
NOTICE_BODY = ("<p>New sign-in to your OpenAI account from a new device. "
               "If this was not you, secure your account.</p>")


def test_the_code_is_read_from_what_a_person_would_see():
    """The message is HTML and nothing else, and its markup carries
    three six-digit runs that are not the code - a CDN path, a padding,
    a width. Reading the source would return one of those."""
    msg = message("tired.viper.ksmo@masked.me", CODE_BODY)

    assert "100024" in msg.get_payload(), "the trap is in the fixture"
    assert mailbox.code_in_message(msg) == "314159"
    seen = mailbox.text_of(msg)
    assert "100024" not in seen and "cdn.adguardcdn.com" not in seen
    assert seen.startswith("Forwarded by AdGuard Mail")


def test_a_code_in_any_language_is_still_one_six_digit_run():
    """Two of the real messages had Persian subjects and bodies. A rule
    that matched the English wording would have missed them; this one
    reads no words at all."""
    msg = message("tired.viper.ksmo@masked.me", PERSIAN_BODY,
                  subject="کد ورود")

    assert mailbox.code_in_message(msg) == "271828"


def test_a_sign_in_notice_is_not_a_code():
    """Seven of the thirty-nine forwarded messages were "New sign-in to
    your OpenAI account". None holds a six-digit run, and the rule that
    separates them from the codes is exactly that."""
    msg = message("tired.viper.ksmo@masked.me", NOTICE_BODY,
                  subject="New sign-in to your OpenAI account")

    assert mailbox.code_in_message(msg) is None


def test_two_codes_in_one_message_are_no_code_at_all():
    """A message this cannot read is not a choice to make between two
    numbers: typing the wrong one costs the account an attempt, and the
    service counts those."""
    msg = message("a@masked.me", "<p>code 111111 or maybe 222222</p>")

    assert mailbox.code_in_message(msg) is None


def test_an_alias_is_matched_whole_and_never_as_a_substring():
    """Every alias forwards into one mailbox, so the To header is the
    only thing that says which account a message is for - and
    `a@x.com` is a substring of `ba@x.com`. Two accounts signing in at
    once must not be able to take each other's code."""
    msg = message("viper.ksmo@masked.me", CODE_BODY)

    assert mailbox.addressed_to(msg, "viper.ksmo@masked.me")
    assert mailbox.addressed_to(msg, "VIPER.KSMO@MASKED.ME"), "case is not it"
    assert not mailbox.addressed_to(msg, "iper.ksmo@masked.me")
    assert not mailbox.addressed_to(msg, "tired.viper.ksmo@masked.me")
    assert not mailbox.addressed_to(msg, "")
    # The forwarding mailbox is in Delivered-To on every message, and
    # is nobody's alias.
    assert not mailbox.addressed_to(msg, "purplespoty@gmail.com")


def test_a_date_whose_zone_is_unknown_is_read_as_utc():
    """`-0000` and a missing zone both mean "the local zone is not
    known", and a naive datetime read as *this* machine's local time
    put every such message three and a half hours out on a farm whose
    clock is Tehran - far enough to make a code that had just arrived
    look like the last attempt's and be thrown away."""
    now = time.time()
    msg = message("a@masked.me", CODE_BODY, when=now)

    assert msg.get("Date").endswith("-0000"), "the trap is in the fixture"
    assert abs(mailbox.arrived_at(msg) - now) < 2


def test_a_message_with_no_readable_date_is_never_fresh_enough():
    """A message that cannot be dated cannot be told from the previous
    attempt's, whose code is expired and which the page refuses - and
    the run would then blame the account."""
    msg = message("a@masked.me", CODE_BODY)
    del msg["Date"]

    assert mailbox.arrived_at(msg) == 0.0


class FakeIMAP:
    """An IMAP server with a handful of messages in it."""

    def __init__(self, messages, *, refuse_login=False, refuse_search=False):
        self.messages = list(messages)
        self.refuse_login = refuse_login
        self.refuse_search = refuse_search
        self.logged_out = False
        self.searched: list[tuple] = []

    def login(self, user, password):
        if self.refuse_login:
            raise imaplib_error("[AUTHENTICATIONFAILED] Invalid credentials")
        return "OK", [b"welcome"]

    def select(self, folder, readonly=False):
        self.folder = folder
        return "OK", [b"3"]

    def search(self, charset, *query):
        self.searched.append(query)
        if self.refuse_search:
            return "NO", [b""]
        return "OK", [b" ".join(str(i + 1).encode()
                                for i in range(len(self.messages)))]

    def fetch(self, number, what):
        index = int(number) - 1
        raw = self.messages[index].as_bytes()
        return "OK", [(b"1 (BODY[]", raw), b")"]

    def logout(self):
        self.logged_out = True
        return "BYE", [b"see you"]


def imaplib_error(text):
    import imaplib

    return imaplib.IMAP4.error(text)


@pytest.fixture
def box(monkeypatch):
    """A MailboxSource over a fake server, with no sleeping."""
    made = {}

    def make(messages, **more):
        server = FakeIMAP(messages, **more)
        made["server"] = server
        monkeypatch.setattr(mailbox.imaplib, "IMAP4_SSL",
                            lambda host: server)
        monkeypatch.setattr(mailbox.time, "sleep", lambda s: None)
        source = mailbox.MailboxSource(
            mailbox.Mailbox(host="imap.example.com", user="box@example.com",
                            password="app-password"),
            poll_seconds=0.01)
        return source, server

    return make


def test_the_code_for_one_alias_out_of_a_shared_mailbox(box):
    """The whole point: many aliases, one mailbox, and each flow takes
    its own message."""
    now = time.time()
    source, server = box([
        message("other@masked.me", "<p>code 999999</p>", when=now),
        message("mine@masked.me", CODE_BODY, when=now),
    ])

    assert source.code_for("mine@masked.me", since=now - 10) == "314159"
    assert server.logged_out, "the connection is given back"
    assert server.searched[0] == ("X-GM-RAW", '"in:anywhere to:mine@masked.me"')


def test_spam_is_searched_too(box):
    """Gmail files some of these as spam, and a code in the spam folder
    is still the code (the operator, 2026-09-19)."""
    now = time.time()
    source, server = box([message("mine@masked.me", CODE_BODY, when=now)])

    source.code_for("mine@masked.me", since=now - 10)

    assert "in:anywhere" in server.searched[0][1]
    assert server.folder == '"[Gmail]/All Mail"'


def test_the_previous_attempts_code_is_never_taken(box):
    """It is expired, the page refuses it, and the run then blames the
    account - the one mistake here that costs something."""
    now = time.time()
    source, _ = box([message("mine@masked.me", CODE_BODY,
                             when=now - 3600)])

    assert source.code_for("mine@masked.me", since=now, timeout=0) is None


def test_a_clock_a_minute_out_does_not_lose_a_fresh_code(box):
    """Two clocks are involved. A code stamped just before it was asked
    for is a clock difference, not a stale code."""
    now = time.time()
    source, _ = box([message("mine@masked.me", CODE_BODY, when=now - 30)])

    assert source.code_for("mine@masked.me", since=now) == "314159"


def test_nothing_arriving_is_not_the_accounts_fault(box):
    """None, not an exception: the wait ran out, which is a forwarding
    problem or a service that never sent one."""
    now = time.time()
    source, _ = box([message("someone.else@masked.me", CODE_BODY, when=now)])

    assert source.code_for("mine@masked.me", since=now - 10,
                           timeout=0.02) is None


def test_a_mailbox_that_will_not_answer_is_said_out_loud(box):
    """A refused app password is the farm's plumbing, not a verdict on
    the account, so it raises rather than reading as "no code"."""
    source, _ = box([], refuse_login=True)

    with pytest.raises(mailbox.MailboxError) as refused:
        source.code_for("mine@masked.me", since=time.time())
    assert "would not let us in" in str(refused.value)
    assert "app-password" not in str(refused.value), "never the password"


def test_no_mailbox_configured_is_refused_before_any_connection():
    source = mailbox.MailboxSource(mailbox.Mailbox("", "", ""))

    with pytest.raises(mailbox.MailboxError):
        source.code_for("a@b.com", since=time.time())


def test_the_password_is_not_in_the_mailboxes_own_words():
    """These strings end up in logs."""
    box = mailbox.Mailbox(host="imap.gmail.com", user="a@b.com",
                          password="hunter2")

    assert "hunter2" not in str(box) and "a@b.com" in str(box)
    assert box.configured and not mailbox.Mailbox("", "", "").configured


def test_from_settings_is_none_until_a_mailbox_is_configured():
    """Nothing changes for a farm that has not been given one: the
    caller goes on using NoSource, exactly as before."""
    from types import SimpleNamespace

    assert mailbox.from_settings(SimpleNamespace()) is None
    assert mailbox.from_settings(SimpleNamespace(
        mail_imap_host="imap.gmail.com", mail_imap_user="",
        mail_imap_password="x")) is None
    source = mailbox.from_settings(SimpleNamespace(
        mail_imap_host="imap.gmail.com", mail_imap_user="a@b.com",
        mail_imap_password="x"))
    assert isinstance(source, mailbox.MailboxSource)


def test_the_source_is_what_the_flows_already_take():
    """`codes.CodeSource` is a Protocol, and this has to satisfy it or
    the flows cannot be handed one."""
    from geelark_farm import codes

    source = mailbox.MailboxSource(
        mailbox.Mailbox("imap.example.com", "a@b.com", "x"))
    assert isinstance(source, codes.CodeSource)


def test_a_stop_during_the_mailbox_wait_goes_up_and_the_box_is_closed(box):
    """The IMAP poll ran to its timeout with no way to stop it; the
    build's stop is asked before every look, and the connection is still
    given back on the way out."""
    now = time.time()
    source, server = box([])

    class Stop(Exception):
        pass

    def watch():
        raise Stop()

    with pytest.raises(Stop):
        source.code_for("mine@masked.me", since=now, timeout=5, watch=watch)
    assert server.logged_out, "the connection is given back on a stop too"
