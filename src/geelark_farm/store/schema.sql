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

-- ------------------------------------------------------- logs, rev 8 (C8)
-- The process's own INFO-and-up lines, captured in-process and batched in
-- by store.logdb. The JSON file on disk stays the complete record; this
-- is the copy a page can filter by run, phone and level. Pruned to 30 days
-- by the capture thread itself.
CREATE TABLE IF NOT EXISTS logs (
    id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at      timestamptz NOT NULL,
    level   text NOT NULL,
    logger  text NOT NULL,
    run     text NOT NULL DEFAULT '',
    build   text NOT NULL DEFAULT '',
    serial  text NOT NULL DEFAULT '',
    machine text NOT NULL DEFAULT '',
    msg     text NOT NULL,
    extra   jsonb
);
CREATE INDEX IF NOT EXISTS logs_at ON logs (at);
CREATE INDEX IF NOT EXISTS logs_run_at ON logs (run, at) WHERE run <> '';
CREATE INDEX IF NOT EXISTS logs_serial_at ON logs (serial, at) WHERE serial <> '';

-- ------------------------------------------------- the panel API, rev 9 (C9)
-- The customer panel and the Telegram bot, as rows. Two clients, one door:
-- the panel hands the farm accounts and asks what became of them, the bot
-- hands over the one thing only a person can supply - an emailed code.
--
-- A key is a random 32-byte token, not a chosen password, so it is hashed
-- with plain SHA-256 rather than the scrypt the users table uses: there is
-- nothing to brute-force in 256 bits of entropy, and this hash is computed
-- on EVERY request where a password's is computed twice a day. `key_prefix`
-- is the first characters of the token, kept so a person can tell two keys
-- apart on a page without the page ever holding one.
CREATE TABLE IF NOT EXISTS api_clients (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name           text NOT NULL UNIQUE,
    role           text NOT NULL CHECK (role IN ('panel', 'bot')),
    key_hash       bytea NOT NULL,
    key_prefix     text NOT NULL DEFAULT '',
    webhook_url    text NOT NULL DEFAULT '',
    webhook_secret text NOT NULL DEFAULT '',
    active         boolean NOT NULL DEFAULT true,
    created_at     timestamptz NOT NULL DEFAULT now(),
    last_seen_at   timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS api_clients_key ON api_clients (key_hash);

-- What the panel owns about an account, beside what the sheet owns.
--
-- Every one of these is absent from both of shadow._upsert_resource's
-- statements - its INSERT column list and its DO UPDATE SET list - which is
-- what makes them safe: an unlisted column takes its DEFAULT once, on
-- insert, and is never assigned again. That is the same ground `owner_id`
-- stands on, and shadow.py's own docstring says why it must ("a mirror that
-- reset it would un-assign somebody's phone every thirty seconds").
--
-- Every one also has a DEFAULT or is nullable, deliberately. The mirror
-- inserts without naming them, so a NOT NULL with no default would abort
-- the whole mirror transaction - and serve.py swallows that as one warning
-- while the dashboard silently freezes on stale numbers.
ALTER TABLE resources ADD COLUMN IF NOT EXISTS product          text NOT NULL DEFAULT '';
ALTER TABLE resources ADD COLUMN IF NOT EXISTS credential_kind  text NOT NULL DEFAULT '';
ALTER TABLE resources ADD COLUMN IF NOT EXISTS panel_ref        text;
ALTER TABLE resources ADD COLUMN IF NOT EXISTS client_id        bigint REFERENCES api_clients(id);
-- Google backup codes, single-use, in the order they were given. jsonb
-- rather than text[] because the pools already carry jsonb and one array
-- type is one less thing for a reader to know.
ALTER TABLE resources ADD COLUMN IF NOT EXISTS backup_codes     jsonb;
-- How many phones this account has been put on, and how many of those
-- phones failed under it. Both only ever go up; the panel is told them
-- without asking for anything extra.
ALTER TABLE resources ADD COLUMN IF NOT EXISTS attempts         integer NOT NULL DEFAULT 0;
ALTER TABLE resources ADD COLUMN IF NOT EXISTS failures         integer NOT NULL DEFAULT 0;
-- Only ever consulted for a credential_kind whose code comes from a person:
-- until the panel says the customer is ready to answer, the account is held
-- out of the pool rather than put on a phone that would sit billing while
-- nobody typed a code.
ALTER TABLE resources ADD COLUMN IF NOT EXISTS customer_ready   boolean NOT NULL DEFAULT false;
ALTER TABLE resources ADD COLUMN IF NOT EXISTS state_changed_at timestamptz;
ALTER TABLE resources ADD COLUMN IF NOT EXISTS delivered_at     timestamptz;
-- The panel's own reference is the account's public id, so it must be one
-- row exactly. Partial, like the other two identities on this table.
CREATE UNIQUE INDEX IF NOT EXISTS resources_panel_ref
    ON resources (panel_ref) WHERE panel_ref IS NOT NULL;
-- The API's list is a keyset walk over (updated_at, id): every field it
-- publishes is inside the mirror's own IS DISTINCT FROM guard, so a stamp
-- that moved means something the panel can see moved.
CREATE INDEX IF NOT EXISTS resources_api_cursor
    ON resources (kind, updated_at, id);

-- A client's Idempotency-Key and the answer it got, so a retry is one
-- request rather than two accounts. Pruned by age, not by hand.
CREATE TABLE IF NOT EXISTS api_idempotency (
    client_id  bigint NOT NULL REFERENCES api_clients(id) ON DELETE CASCADE,
    key        text NOT NULL,
    method     text NOT NULL,
    path       text NOT NULL,
    status     integer NOT NULL,
    body       jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (client_id, key)
);

-- The one thing that cannot wait for a pass. A code lives minutes, and its
-- consumer is a sign-in flow already stopped at a text box on a phone, so
-- POST /code writes here and the flow reads here - the only write in this
-- program that does not become a queued request.
--
-- ON DELETE CASCADE because a resources row is hard-deleted by "remove from
-- the pool" (verbs.remove_gmail, verbs.remove_proxy); without it that button
-- would start raising the day the first code arrives.
CREATE TABLE IF NOT EXISTS code_inbox (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    resource_id bigint NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    code        text NOT NULL,
    client_id   bigint REFERENCES api_clients(id),
    received_at timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL,
    consumed_at timestamptz
);
CREATE INDEX IF NOT EXISTS code_inbox_waiting
    ON code_inbox (resource_id, received_at) WHERE consumed_at IS NULL;

-- One row per event per client, with its own retry clock. A delivery that
-- gives up stays here and shows on Needs attention rather than vanishing.
CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    client_id  bigint NOT NULL REFERENCES api_clients(id) ON DELETE CASCADE,
    event      jsonb NOT NULL,
    attempts   integer NOT NULL DEFAULT 0,
    next_at    timestamptz NOT NULL DEFAULT now(),
    status     text NOT NULL DEFAULT 'pending'
               CHECK (status IN ('pending', 'delivered', 'gave_up')),
    last_error text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS webhook_due
    ON webhook_deliveries (next_at) WHERE status = 'pending';

-- --------------------------------------------------- actions, rev 9 (C9)
-- A request can now come from a machine. `requested_by` drops NOT NULL and
-- `client_id` names the client instead; `source` says which without a join,
-- and defaults to 'console' so every row already in the table is right.
--
-- Done here, in the deploy where nothing writes a client_id yet, because
-- the three JOIN users that resolve the asker's name have to become LEFT
-- JOINs in the same change - and the deploy where that is discovered is the
-- one where the Requests page silently loses rows.
ALTER TABLE actions ADD COLUMN IF NOT EXISTS source    text NOT NULL DEFAULT 'console';
ALTER TABLE actions ADD COLUMN IF NOT EXISTS client_id bigint REFERENCES api_clients(id);
ALTER TABLE actions ALTER COLUMN requested_by DROP NOT NULL;
