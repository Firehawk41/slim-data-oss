"""What slim-prep may call (D-116, app review 2026-10-30b R2): the prep book's handoff, in slim-data's
service layer, so slim-prep depends on these three functions and slim-data's domain types, never on its
persistence. D-95 and D-96 unchanged: a batch the prep book registers carries its dilutions and spike
amounts; a correction appends their next versions. The caller's transaction; nothing is committed.
"""

from collections.abc import Sequence

from sqlalchemy.orm import Session

from slim_data.domain.actor import Actor
from slim_data.domain.observations import CupDilution, PrepBatch, SpikeAddition
from slim_data.persistence import observations
from slim_data.persistence._rows import NotFound


class CodeTaken(ValueError):
    """slim-data already has a batch with this code (an instrument file registered it, P-26)."""


def prepped_batch(session: Session, code: str) -> PrepBatch | None:
    """The batch as slim-data holds it now, or None if it has none by this code."""
    try:
        return observations.get_batch(session, code)
    except NotFound:
        return None


def register_prepped_batch(session: Session, batch: PrepBatch, actor: Actor) -> PrepBatch:
    """Registers a batch the prep book sends (D-95). Raises CodeTaken if the code is in use."""
    if prepped_batch(session, batch.code) is not None:
        raise CodeTaken(f"prep batch {batch.code} already exists")
    return observations.register_batch(session, batch, actor)


def correct_prepped_batch(
    session: Session,
    code: str,
    actor: Actor,
    reason: str,
    dilutions: Sequence[CupDilution] = (),
    spikes: Sequence[SpikeAddition] = (),
) -> tuple[PrepBatch, int]:
    """A correction after handoff (D-96): the changed dilutions and spike amounts become their next
    versions. Returns the batch as it now stands and how many entries changed."""
    return observations.correct_prep(session, code, actor, reason, dilutions, spikes)
