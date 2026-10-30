"""The handoff API slim-prep calls (D-116): register, correct, read; nothing else of slim-data's."""

from decimal import Decimal

import pytest

from slim_data.domain.enums import ReadingRole
from slim_data.domain.observations import CupDilution
from slim_data.service import handoff
from tests.factories import ANA, sample
from tests.persistence.conftest import make_batch


def test_register_read_and_correct_a_prepped_batch(session):
    dilution = CupDilution(sample=sample(1), role=ReadingRole.CUP, replicate=1, dilution=Decimal("10"))
    batch = make_batch().model_copy(update={"dilutions": (dilution,)})
    assert handoff.prepped_batch(session, batch.code) is None
    stored = handoff.register_prepped_batch(session, batch, ANA)
    assert stored.id and handoff.prepped_batch(session, batch.code).dilutions == (dilution,)
    with pytest.raises(handoff.CodeTaken):
        handoff.register_prepped_batch(session, batch, ANA)
    fixed = dilution.model_copy(update={"dilution": Decimal("20")})
    corrected, changed = handoff.correct_prepped_batch(session, batch.code, ANA, "misread weight", [fixed])
    assert changed == 1 and corrected.prep_dilution(sample(1), ReadingRole.CUP, 1) == 20
