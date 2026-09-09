# Architecture

One image, four roles, one store. Everything that has to be shared lives
in Postgres; a container keeps nothing on its disk that another
container would need. (Scale-out, 2026-09-10.)

## Roles

| Role      | Container         | Runs                                                        | Scales |
|-----------|-------------------|-------------------------------------------------------------|--------|
| `web`     | `geelark-web`     | the console: pages, login, queueing commands                | by number, behind Caddy |
| `keeper`  | `geelark`         | the pass: stock, decisions, housekeeping, the control lane   | one    |
| `builder` | `builder` (n)     | jobs from the queue: Google sign-in, installs, app sign-in  | `--scale builder=n` |
| `caddy`   | `caddy`           | TLS and the public name, proxied to a `web`                 | one    |

`ROLE` in the environment picks the shape; `all` is every role in one
process, for a laptop.

## What each role reads and writes

| Table / key                | web              | keeper                        | builder                  |
|----------------------------|------------------|-------------------------------|--------------------------|
| `resources` (pools)        | reads, edits     | reads, claims for finishes    | claims, releases, marks  |
| `phones`                   | reads, states    | reads, settles, marks         | writes rows for builds   |
| `actions` (commands)       | writes, NOTIFY   | drains, runs the lane verbs   | -                        |
| `jobs` (build queue)       | -                | orders, reads results         | takes (SKIP LOCKED), beats, finishes |
| `phone_claims` (ledger)    | -                | reads, settles, prunes        | claims, beats, releases  |
| `service_state`            | reads the pulse  | pulse, breaker, controls      | -                        |
| `artifacts`                | reads            | prunes                        | mirrors a build's screens |
| `logs`                     | reads            | writes                        | writes                   |
| `sessions`, `users`        | owns             | -                             | -                        |

## The bells

Postgres `NOTIFY`, so a press reaches a process in another container
inside a second and a missed bell costs a wait and nothing else:

- `geelark_actions` - rung by `store.actions.enqueue`; the keeper's
  `serve.Listener` wakes the lane and the pass.
- `geelark_jobs` - rung by `store.jobs.queue`; every builder's listener
  wakes its take loop.

## Liveness

- A builder beats its running jobs every minute; the keeper marks a job
  `lost` after `JOB_LOST_SECONDS` without a beat and orders again.
- A process beats its phone claims every minute; a claim without a beat
  for `STALE_CLAIM_SECONDS` is a dead process's, and the keeper settles
  the phone.
- Docker's healthcheck runs `geelark serve --healthcheck` in every
  container; each role answers from its own heartbeat.

## Deploying one role

- console: `sudo docker compose build && sudo docker compose up -d web`
- builders: `sudo docker compose up -d builder` (add `--scale builder=n`);
  a restart loses only the jobs in flight, which come back as `lost`.
- keeper: through the gate (`/tmp/deploy3.sh`: tick Pause, wait for live
  builds to land, `up -d geelark`, untick) - a pass mid-way is the one
  thing a restart interrupts.
- Caddy: edit `/etc/caddy/Caddyfile`, then `sudo docker restart caddy`
  (`caddy reload` does not apply).

Never a bare `docker compose up -d` after a build: it recreates every
service whose image changed, the keeper included.

## A second host

Point its `.env` at the same store, the same GeeLark key and the same
CapSolver key, and run `builder` replicas there. Nothing else is
needed: the queue, the ledger, the pools, the screens and the logs are
all in the store. A second `web` works the same way behind a Caddy that
lists both.

## What still assumes one host

- `state/` holds each container's own heartbeat file - local by design.
- The disk copy of `artifacts/` is kept beside the store's; the console
  reads the store first.
