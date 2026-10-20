"""Who and why, for the database change log (D-03, D-65; DESIGN.md §10.3 layer 2).

slim-domain's hash-chained `audit_log` (installed by the host on Postgres) records every row change
and reads who/why from two transaction-local settings. slim-data sets the same two itself on every
write, so it doesn't depend on slim-domain. On SQLite there is no audit log, so it does nothing.
"""

from sqlalchemy import text
from sqlalchemy.orm import Session

from slim_data.domain.actor import Actor

CHANGED_BY = "app.changed_by"  # the names slim-domain's infrastructure/audit.py reads
CHANGE_REASON = "app.change_reason"


def set_change_context(session: Session, actor: Actor, reason: str) -> None:
    if session.get_bind().dialect.name != "postgresql":
        return
    session.execute(
        text("SELECT set_config(:by_key, :by, true), set_config(:why_key, :why, true)"),
        {"by_key": CHANGED_BY, "by": actor.id, "why_key": CHANGE_REASON, "why": reason},
    )
