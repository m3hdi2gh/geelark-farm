"""The Postgres store - the spreadsheet's successor, a stage at a time.

Nothing in the rest of the package imports this module at module level.
That is the contract that lets it live on `main` while half-built: the
import happens inside a `settings.store_enabled` check, so a box that has
not opted in never executes a line of it - and a bug here cannot take the
loop down on a machine that never asked for a store.

The cluster is managed (ArvanCloud) and reachable only from inside the
provider's network - measured 2026-08-31: the endpoint refuses the public
internet, answers the farm's server in 25ms, and runs PostgreSQL 17.9.
It does not offer TLS; the private network is the transport security, and
that trade was made knowingly.
"""

from .db import Store, ensure_schema

__all__ = ["Store", "ensure_schema"]
