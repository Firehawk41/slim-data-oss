"""`verify` (D-17, D-76): every stored version replays exactly; the audit chain is the host's to check."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from slim_data.demo.scenarios import seed
from slim_data.domain.values import Quantity
from slim_data.persistence.observations import get_batch
from slim_data.persistence.results import add_version, load_result
from slim_data.service.verify import verify
from tests.factories import analyte, sample


@pytest.fixture
def demo(session):
    return seed(session, datetime(2026, 10, 20, 9, 0, tzinfo=UTC))


def test_every_demo_version_replays_and_the_chain_is_reported_unchecked(session, demo):
    report = verify(session)
    assert report.ok and report.versions > 9 and report.mismatches == ()
    assert not report.chain_checked  # no checker passed: unchecked, not "intact"


def test_the_hosts_chain_check_is_run_and_its_broken_entries_reported(session, demo):
    seen = []
    report = verify(session, chain_check=lambda connection: seen.append(connection) or [7, 9])
    assert seen and report.chain_checked and report.broken_entries == (7, 9) and not report.ok
    assert verify(session, chain_check=lambda connection: []).ok


def test_a_version_the_rules_did_not_make_is_found(session, demo):
    cu = analyte("Cu", analysis_id=2)  # clean_pass is on 36 Elements (D-78)
    s1 = sample(1, "TR00101")
    current = load_result(session, s1, cu).current
    forged = current.model_copy(
        update={
            "version": current.version + 1,
            "reason": "typed in by hand",
            "reported": Quantity(value=Decimal("99.000"), unit=current.reported.unit),
        }
    )
    add_version(session, s1, cu, forged, get_batch(session, "PB-00101").id)
    report = verify(session)
    assert not report.ok
    assert report.mismatches == (f"{s1} Cu v{forged.version}: differs from a replay in reported",)
