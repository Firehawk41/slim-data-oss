"""Result versions and events: immutable, numbered, readable as of any time (D-04, D-12, D-21)."""

from decimal import Decimal

import pytest

from slim_data.domain.enums import EventKind, Qualifier, ResultStatus, Severity
from slim_data.domain.results import ResultEvent
from slim_data.persistence._rows import ConcurrentChange, NotFound
from slim_data.persistence.results import add_event, add_version, exceptions, load_result, results_for_tr
from tests.factories import ANA, ROB, SUE, analyte, flag, qty, sample, version
from tests.persistence.conftest import instant

CU = analyte("Cu")


@pytest.fixture
def first(session, stored):
    ruleset, batch, _ = stored
    v1 = version(
        1,
        flag(Severity.WARN, "CUP_CHOSEN_BY_SPIKE"),
        ruleset_id=ruleset.id,
        raw=Decimal("10.1000"),
        reported=qty("10.100"),
        recovery=Decimal("0.99"),
        trace={"steps": [{"step": "agreement@1", "cup_1": "10.00"}]},
        limits={"unit": "ppb", "high": "50"},
    )
    return add_version(session, sample(1), CU, v1, batch.id), batch


def test_a_version_round_trips_exactly(session, first):
    stored, _ = first
    loaded = load_result(session, sample(1), CU)
    assert loaded.current == stored and stored.created_at is not None
    assert str(loaded.current.reported.value) == "10.100" and str(loaded.current.raw) == "10.1000"
    assert loaded.current.flags[0].code == "CUP_CHOSEN_BY_SPIKE"


def test_versions_are_numbered_and_as_of_reads_the_past(session, first):
    stored, batch = first
    add_event(session, sample(1), CU, ResultEvent(kind=EventKind.REVIEWED, version=1, actor=ROB))
    before = instant(session)
    v2 = version(2, ruleset_id=stored.ruleset_id, reason="re-run in RUN-0043")
    add_version(session, sample(1), CU, v2, batch.id)
    now = load_result(session, sample(1), CU)
    assert [v.version for v in now.versions] == [1, 2] and now.status == ResultStatus.DRAFT
    past = load_result(session, sample(1), CU, as_of=before)
    assert [v.version for v in past.versions] == [1] and past.status == ResultStatus.REVIEWED
    with pytest.raises(ConcurrentChange, match="expected version 3"):
        add_version(session, sample(1), CU, version(2, ruleset_id=stored.ruleset_id), batch.id)


def test_a_result_did_not_exist_before_its_first_version(session, stored):
    ruleset, batch, _ = stored
    before = instant(session)
    add_version(session, sample(1), CU, version(1, ruleset_id=ruleset.id), batch.id)
    assert load_result(session, sample(1), CU, as_of=before) is None
    assert load_result(session, sample(2), CU) is None
    with pytest.raises(ConcurrentChange, match="expected version 1"):
        add_version(session, sample(2), CU, version(2, ruleset_id=ruleset.id), batch.id)


def test_events_need_a_result(session, stored):
    with pytest.raises(NotFound):
        add_event(session, sample(1), CU, ResultEvent(kind=EventKind.REVIEWED, version=1, actor=ROB))


def test_results_for_a_tr_and_the_lab_wide_exception_log(session, first):
    stored, batch = first
    add_version(session, sample(2), CU, version(1, ruleset_id=stored.ruleset_id), batch.id)
    add_version(session, sample(1), analyte("Zn"), version(1, ruleset_id=stored.ruleset_id), batch.id)
    exception = ResultEvent(
        kind=EventKind.EXCEPTION,
        version=1,
        actor=SUE,
        reason_category="customer deadline",
        note="rerun impossible",
    )
    before = instant(session)
    add_event(session, sample(2), CU, exception)
    assert [(str(r.sample), r.analyte.code) for r in results_for_tr(session, "TR00042")] == [
        ("TR00042-01", "Cu"),
        ("TR00042-01", "Zn"),
        ("TR00042-02", "Cu"),
    ]
    assert results_for_tr(session, "TR00042", as_of=before)[2].events == ()
    [(where, what, event)] = exceptions(session)
    assert (where, what, event.reason_category) == (sample(2), CU, "customer deadline")
    assert exceptions(session, as_of=before) == []


def test_below_mdl_and_nothing_reportable_round_trip(session, stored):
    ruleset, batch, _ = stored
    below = version(
        1, ruleset_id=ruleset.id, qualifier=Qualifier.BELOW_MDL, uncertainty=None, reported=qty("0.050")
    )
    add_version(session, sample(1), CU, below, batch.id)
    nothing = version(1, ruleset_id=ruleset.id, reported=None, uncertainty=None, analysts=("ana", "bo"))
    add_version(session, sample(2), CU, nothing, batch.id)
    assert load_result(session, sample(1), CU).current.qualifier == Qualifier.BELOW_MDL
    loaded = load_result(session, sample(2), CU).current
    assert loaded.reported is None and loaded.analyst_ids == {"ana", "bo"} and loaded.created_by == ANA
