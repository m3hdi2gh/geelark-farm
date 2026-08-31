"""The read-only web UI. Imported by `serve` only inside the
`settings.web_enabled` check - the same trunk rule the store lives under,
and the same AST exemption: this package is the gated side of the rule.
"""

from .app import start

__all__ = ["start"]
