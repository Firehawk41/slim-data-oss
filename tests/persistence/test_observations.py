"""Batches, runs, readings and exclusions; reading a batch back as of any time (D-21)."""

import pytest

from slim_data.domain.enums import ExclusionKind, ReadingRole
from slim_data.domain.observations import ReadingExclusion
from slim_data.engine.runner import evaluate
from slim_data.persistence._rows import NotFound
from slim_data.persistence.observations import (
    AlreadyImported,
    evaluation_input,
    exclude_reading,
    get_batch,
    get_run,
    record_run,
    register_batch,
)
from tests.engine.helpers import demo
from tests.factories import ANA
from tests.persistence.conftest import instant, make_batch, make_reading, make_run, standard_readings


def test_a_batch_round_trips_with_its_spikes(session):
    stored = register_batch(session, make_batch(), ANA)
    assert get_batch(session, "PB-0001") == stored and stored.id is not None
    with pytest.raises(ValueError, match="already exists"):
        register_batch(session, make_batch(), ANA)
    with pytest.raises(NotFound):
        get_batch(session, "PB-9999")


def test_readings_keep_their_digits_and_blanks_have_no_sample(session, stored):
    _, batch, run = stored
    assert get_run(session, run.id) == run
    data = evaluation_input(session, batch.code)
    assert [str(r.value.value) for r in data.readings] == ["10.00", "10.20", "30.0", "5.10", "5.30", "0.010"]
    blank = data.readings[-1]
    assert blank.role == ReadingRole.BLANK and blank.sample is None
    with pytest.raises(NotFound):
        get_run(session, 999)


def test_the_same_file_is_refused_with_a_pointer_to_the_first_import(session, stored):
    _, batch, run = stored
    assert get_run(session, run.id).imported_by.id == "rob"
    with pytest.raises(AlreadyImported) as refused:
        record_run(session, make_run(), [(batch.code, make_reading(1, ReadingRole.CUP, 1, "1"))])
    assert refused.value.run_id == run.id


@pytest.mark.parametrize(
    "reading, message",
    [
        (make_reading(3, ReadingRole.CUP, 1, "1"), "not in batch"),
        (make_reading(1, ReadingRole.CUP, 1, "1", analysis_id=2), "not batch PB-0001's analysis"),
    ],
)
def test_readings_must_fit_their_batch(session, stored, reading, message):
    with pytest.raises(ValueError, match=message):
        record_run(session, make_run("other.csv"), [("PB-0001", reading)])


def test_a_run_needs_readings_and_a_known_batch(session, stored):
    with pytest.raises(ValueError, match="at least one reading"):
        record_run(session, make_run("empty.csv"), [])
    with pytest.raises(NotFound):
        record_run(session, make_run("x.csv"), [("PB-9999", make_reading(1, ReadingRole.CUP, 1, "1"))])


def test_exclusions_are_events_and_as_of_sees_the_batch_before_them(session, stored):
    _, batch, _ = stored
    data = evaluation_input(session, batch.code)
    cup, spike = data.readings[0], data.readings[2]
    before = instant(session)
    excluded = exclude_reading(
        session, ReadingExclusion(reading_id=cup.id, reason="bubble in cup", actor=ANA)
    )
    assert excluded.at is not None
    assert evaluation_input(session, batch.code).excluded_reading_ids == {cup.id}
    assert evaluation_input(session, batch.code, as_of=before).excluded_reading_ids == frozenset()
    with pytest.raises(ValueError, match="already excluded"):
        exclude_reading(session, ReadingExclusion(reading_id=cup.id, reason="again", actor=ANA))
    exclude_reading(session, ReadingExclusion(reading_id=spike.id, reason="spike spilled", actor=ANA))
    assert evaluation_input(session, batch.code).excluded_reading_ids == {cup.id, spike.id}
    with pytest.raises(NotFound):
        exclude_reading(session, ReadingExclusion(reading_id=999, reason="x", actor=ANA))


def test_before_any_reading_there_is_nothing_to_evaluate(session):
    before = instant(session)
    register_batch(session, make_batch(), ANA)
    with pytest.raises(NotFound, match="no readings yet"):
        evaluation_input(session, "PB-0001")
    with pytest.raises(NotFound, match="no prep batch"):
        evaluation_input(session, "PB-0001", as_of=before)


def test_stored_readings_evaluate_exactly_like_the_originals(session, stored):
    _, batch, _ = stored
    loaded = evaluation_input(session, batch.code)
    original = loaded.model_copy(
        update={"readings": tuple(r.model_copy(update={"id": None}) for r in standard_readings())}
    )
    fresh, from_db = evaluate(original, demo()), evaluate(loaded, demo())
    assert [c.model_dump(exclude={"trace"}) for c in fresh.cells] == [
        c.model_dump(exclude={"trace"}) for c in from_db.cells
    ]
    assert [c.trace for c in fresh.cells] == [c.trace for c in from_db.cells]


def test_a_reading_can_be_restored_and_excluded_again_with_its_history_kept(session, stored):
    _, batch, _ = stored
    cup = evaluation_input(session, batch.code).readings[0]

    def change(kind, reason):
        return exclude_reading(
            session, ReadingExclusion(reading_id=cup.id, kind=kind, reason=reason, actor=ANA)
        )

    with pytest.raises(ValueError, match="nothing to restore"):
        change(ExclusionKind.RESTORED, "never excluded")
    change(ExclusionKind.EXCLUDED, "bubble")
    while_excluded = instant(session)
    restored = change(ExclusionKind.RESTORED, "wrong cup excluded")
    assert restored.kind == ExclusionKind.RESTORED
    after_restore = instant(session)
    assert evaluation_input(session, batch.code).excluded_reading_ids == frozenset()
    change(ExclusionKind.EXCLUDED, "bubble after all")
    assert evaluation_input(session, batch.code).excluded_reading_ids == {cup.id}
    assert evaluation_input(session, batch.code, as_of=while_excluded).excluded_reading_ids == {cup.id}
    assert evaluation_input(session, batch.code, as_of=after_restore).excluded_reading_ids == frozenset()
    with pytest.raises(ValueError, match="already excluded"):
        change(ExclusionKind.EXCLUDED, "again")
