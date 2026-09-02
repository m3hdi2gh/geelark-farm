-- The farm's schema, replacing the spreadsheet a stage at a time.
--
-- Read this the way you would read pools.py: the shape encodes decisions.
-- Three of them matter most:
--
-- **Rows have ids, and the id is the handle.** The sheet's row numbers were
-- explicitly not durable - a sibling build deleting a row shifted every row
-- below it, and phone 751's result landed on the wrong phone (2026-08-14).
-- `sheet_row` survives only as an ordering hint during the mirror period,
-- because "the first usable one" is what the operator sees when they look
-- at the tab, and claiming out of sheet order would make the tab lie.
--
-- **One lease, one number.** `claims` carries the lease for everything a run
-- holds - the phone and its credentials together - because the day those
-- were two numbers, a dead run's Gmail went back in the pool while its phone
-- still read as held, and one address was signed into two phones for 115
-- minutes (2026-08-28).
--
-- **Ownership is in the schema from day one.** `taken` in the sheet meant
-- "out with somebody" and could not say who. `owner_id` says who, and adding
-- it after the tables fill would be a migration; adding it now is a column.
--
-- Applied by store.db.ensure_schema(), which is idempotent: every statement
-- here must be CREATE ... IF NOT EXISTS or otherwise safe to re-run, because
-- it runs on every store-enabled start and a failed half-application must
-- converge on the next one.

CREATE TABLE IF NOT EXISTS schema_meta (
    key   text PRIMARY KEY,
    value text NOT NULL
);

-- ---------------------------------------------------------------- users
-- Two axes, deliberately not one list of role names: what buttons you have
-- (role) and what rows you see (sees). The owner's "both" answer is these
-- two axes composed, with no third role invented for it.
CREATE TABLE IF NOT EXISTS users (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username      text NOT NULL UNIQUE,
    -- scrypt, from the standard library - no new dependency. Salt and
    -- parameters ride beside the hash so they can be raised later without
    -- invalidating anyone.
    password_hash bytea NOT NULL,
    password_salt bytea NOT NULL,
    scrypt_n      integer NOT NULL DEFAULT 16384,
    scrypt_r      integer NOT NULL DEFAULT 8,
    scrypt_p      integer NOT NULL DEFAULT 1,
    role          text NOT NULL CHECK (role IN ('admin', 'operator')),
    sees          text NOT NULL CHECK (sees IN ('all', 'own')),
    active        boolean NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------- resources
-- Gmails, proxies and app accounts in one table, because Pool is one class:
-- they share the whole claim/spend/release lifecycle and differ only in
-- their identity columns. Partial unique indexes give each kind the
-- duplicate rule _flag_duplicates could only flag after the fact.
CREATE TABLE IF NOT EXISTS resources (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind        text NOT NULL CHECK (kind IN ('gmail', 'proxy', 'app')),
    -- The sheet's ordering contract, kept while the sheet lives. NULL once
    -- a row is born in the web UI and has no sheet twin.
    sheet_row   integer,
    status      text NOT NULL DEFAULT '',
    -- gmail / app identity
    address     text,
    password    text,
    totp_secret text,
    -- app accounts whose only way in is an emailed one-time code.
    email_code_only boolean NOT NULL DEFAULT false,
    recovery_email  text,
    seller      text,
    -- proxy identity, exactly the three columns pools._identity joins.
    host        text,
    port        integer,
    username    text,
    proxy_pass  text,
    proxy_name  text,
    last_exit_ip text,
    times_used  integer NOT NULL DEFAULT 0,
    -- Who this row is reserved for. NULL means the shared pool.
    owner_id    bigint REFERENCES users(id),
    note        text NOT NULL DEFAULT '',
    -- Set when validation refused the row at write time; a broken row in
    -- the sheet looked free (blank status) and sat invisible for days.
    error       text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Which phone a spent credential is on - the sheet's "Phone Serial"
-- column, mirrored so "held against a phone that is gone" is answerable
-- from the store alone. ALTER, because CREATE IF NOT EXISTS does not add
-- columns to a table that already exists; additive-only, like everything
-- in this file.
ALTER TABLE resources ADD COLUMN IF NOT EXISTS serial text NOT NULL DEFAULT '';

CREATE UNIQUE INDEX IF NOT EXISTS resources_addr_ident
    ON resources (kind, lower(address))
    WHERE kind IN ('gmail', 'app') AND address IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS resources_proxy_ident
    ON resources (host, port, username)
    WHERE kind = 'proxy';
CREATE INDEX IF NOT EXISTS resources_claimable
    ON resources (kind, status, times_used, sheet_row);

-- --------------------------------------------------------------- phones
-- The PhoneLog equivalent: current state of what runs produced. Unlike the
-- sheet, rows are closed (done_at set) rather than deleted, so "what did we
-- build on Tuesday" has an answer here too, not only in events.
CREATE TABLE IF NOT EXISTS phones (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    serial      text NOT NULL,
    phone_id    text,                 -- GeeLark's 20-digit id
    status      text NOT NULL DEFAULT 'building',
    -- The person-channel, exactly the sheet's State words. Free text there,
    -- constrained here: `dome` was silently nothing in a sheet cell.
    state       text NOT NULL DEFAULT '' CHECK (state IN ('', 'unused', 'taken', 'done', 'failed')),
    app_installed boolean,            -- three-valued on purpose: NULL = nobody looked
    gmail       text,
    app_account text,
    proxy_name  text,
    tries       integer NOT NULL DEFAULT 0,
    owner_id    bigint REFERENCES users(id),
    note        text NOT NULL DEFAULT '',
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    done_at     timestamptz
);

-- One live row per serial; history keeps the closed ones.
CREATE UNIQUE INDEX IF NOT EXISTS phones_live_serial
    ON phones (serial) WHERE done_at IS NULL;
CREATE INDEX IF NOT EXISTS phones_owner ON phones (owner_id) WHERE done_at IS NULL;

-- --------------------------------------------------------------- claims
-- What a run is holding right now, with the one lease. A row here says
-- "resource X (and phone Y) belong to run Z until lease_until"; the
-- heartbeat moves lease_until, and a claim whose lease has passed is free
-- to be swept - visible to the atomic claim query itself, which is the
-- part a spreadsheet could never do.
CREATE TABLE IF NOT EXISTS claims (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id      text NOT NULL,        -- the [rN] batch id from the logs
    machine     text NOT NULL,
    resource_id bigint REFERENCES resources(id),
    phone_row   bigint REFERENCES phones(id),
    taken_at    timestamptz NOT NULL DEFAULT now(),
    lease_until timestamptz NOT NULL,
    released_at timestamptz,
    outcome     text NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS claims_live
    ON claims (lease_until) WHERE released_at IS NULL;

-- ---------------------------------------------------------------- codes
-- The emailed one-time-code queue, persistent so a Watchdog restart cannot
-- orphan a bot's in-flight answer - codes.Pending is process memory and
-- that is exactly its console-shaped gap.
CREATE TABLE IF NOT EXISTS code_requests (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    address     text NOT NULL,
    asked_at    timestamptz NOT NULL DEFAULT now(),
    deadline    timestamptz NOT NULL,
    code        text,
    answered_at timestamptz,
    answered_by bigint REFERENCES users(id),
    -- expired / malformed / given_up - typed, because Pending collapsed
    -- them into one False and every client re-derived the difference.
    refusal     text
);
CREATE INDEX IF NOT EXISTS code_requests_open
    ON code_requests (deadline) WHERE answered_at IS NULL AND refusal IS NULL;

-- --------------------------------------------------------------- events
-- Created here so stage 2 only starts writing; append-only; every row
-- carries who or what did it. This is History's successor and the
-- monitoring substrate, and it is the table the friend's every-verb-used
-- cutover criterion is measured against.
CREATE TABLE IF NOT EXISTS events (
    id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at       timestamptz NOT NULL DEFAULT now(),
    kind     text NOT NULL,
    machine  text NOT NULL DEFAULT '',
    run_id   text NOT NULL DEFAULT '',
    build    text NOT NULL DEFAULT '',
    serial   text NOT NULL DEFAULT '',
    status   text NOT NULL DEFAULT '',
    user_id  bigint REFERENCES users(id),
    seconds  real,
    detail   text NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS events_at ON events (at);
CREATE INDEX IF NOT EXISTS events_kind_at ON events (kind, at);
CREATE INDEX IF NOT EXISTS events_serial_at ON events (serial, at) WHERE serial <> '';

-- --------------------------------------------------------------- actions
-- The command queue: how a web button reaches the sheet without a second
-- writer. A POST inserts a row; the serve pass drains it with its own Book
-- and writes status/result back. Control verbs are drained ABOVE the
-- Stop-everything check (or a web restart could never run); everything
-- else drains below it, so a stopped service still does not delete phones.
-- idem_key is UNIQUE: a double-submit inserts once and the second attempt
-- finds the first row - indistinguishable from success, by design.
CREATE TABLE IF NOT EXISTS actions (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    verb         text NOT NULL,
    payload      jsonb NOT NULL DEFAULT '{}'::jsonb,
    requested_by bigint NOT NULL REFERENCES users(id),
    requested_at timestamptz NOT NULL DEFAULT now(),
    status       text NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued', 'awaiting_confirm', 'running',
                                   'done', 'failed', 'refused', 'cancelled')),
    result       text NOT NULL DEFAULT '',
    detail       jsonb,
    executed_at  timestamptz,
    idem_key     text UNIQUE
);
CREATE INDEX IF NOT EXISTS actions_open
    ON actions (requested_at)
    WHERE status IN ('queued', 'awaiting_confirm');

-- ------------------------------------------------------- users, rev 4 (C1)
-- What an operator may do, one boolean each, ticked by an admin on the
-- Users page. An admin has every one of them implicitly (users.may), so
-- the columns only ever matter on operator rows. Booleans rather than a
-- role table because there are six of them and they are read on every
-- request - and because "which box is ticked" is exactly the question the
-- owner asked to be able to answer per person.
ALTER TABLE users ADD COLUMN IF NOT EXISTS may_add_gmail        boolean NOT NULL DEFAULT false;
ALTER TABLE users ADD COLUMN IF NOT EXISTS may_add_gpt          boolean NOT NULL DEFAULT false;
ALTER TABLE users ADD COLUMN IF NOT EXISTS may_add_proxy        boolean NOT NULL DEFAULT false;
ALTER TABLE users ADD COLUMN IF NOT EXISTS may_login_accounts   boolean NOT NULL DEFAULT false;
ALTER TABLE users ADD COLUMN IF NOT EXISTS may_change_proxy     boolean NOT NULL DEFAULT false;
ALTER TABLE users ADD COLUMN IF NOT EXISTS may_take_phones      boolean NOT NULL DEFAULT false;
-- Set by a create or a reset: the one-time password was shown once, and
-- the first thing this person does after signing in is choose their own.
ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change_password boolean NOT NULL DEFAULT false;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at        timestamptz;

-- --------------------------------------------------- resources, rev 5 (C2)
-- The columns the sheet pools kept beside a row and the mirror never
-- carried, needed the day this table becomes the pool itself rather than
-- a picture of one. Text where the sheet held free text (a purchase date
-- typed by a person is not a DATE), timestamptz where the program stamps.
ALTER TABLE resources ADD COLUMN IF NOT EXISTS claimed_at   timestamptz;
ALTER TABLE resources ADD COLUMN IF NOT EXISTS used_at      text NOT NULL DEFAULT '';
ALTER TABLE resources ADD COLUMN IF NOT EXISTS purchased_on text NOT NULL DEFAULT '';
-- Where a row came from: the sheet funnel, the customer panel, or a person
-- typing it into the web (added_by names them). 'sheet' for everything
-- that predates the question.
ALTER TABLE resources ADD COLUMN IF NOT EXISTS source       text NOT NULL DEFAULT 'sheet';
ALTER TABLE resources ADD COLUMN IF NOT EXISTS added_by     bigint REFERENCES users(id);

-- ------------------------------------------------------ service_state (C5)
-- Small facts a pass learns that belong to no row - the proxies GeeLark
-- holds that the Proxy tab never heard of, the counts the Service board
-- shows - written whole each pass so a page can show them without a
-- GeeLark call. One row per key, jsonb, replaced in place.
CREATE TABLE IF NOT EXISTS service_state (
    key        text PRIMARY KEY,
    value      jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- --------------------------------------------------- actions, rev 7 (C7)
-- When a command settled. `executed_at` is when a pass took it; a command
-- that starts phone work stays `running` for minutes after that, and the
-- Requests page wants "how long did it take", which is this minus that.
ALTER TABLE actions ADD COLUMN IF NOT EXISTS finished_at timestamptz;
