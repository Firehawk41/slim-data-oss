"""What an auditor reads (D-112): a report's whole trail, and what a window on an instrument touched."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from slim_data.domain.enums import EventKind, ReadingRole, SourceKind
from slim_data.domain.results import ReportLine
from slim_data.persistence._rows import NotFound
from slim_data.service import audit
from slim_data.service import workflow as wf
from slim_data.service.imports import import_run
from tests.factories import ANA
from tests.service.fakes import FakeStaff
from tests.service.test_workflow import CU, HEADER, REV, ROWS, S1, S2, SHA, edit, lab  # noqa: F401 (fixture)

DAY_START = datetime(2026, 10, 19, tzinfo=UTC)
DAY_END = datetime(2026, 10, 20, tzinfo=UTC)


def released_and_issued(session, lab):  # noqa: F811
    lab()
    wf.review_batch(session, "PB-0001", REV)
    wf.release(session, S1, 1, REV)
    return wf.record_issue(session, "TR00042", (ReportLine(sample=S1, analyte=CU, version=1),), SHA, REV)


def test_a_report_audit_shows_everything_behind_each_line(session, lab):  # noqa: F811
    receipt = released_and_issued(session, lab)
    (issue,) = audit.issues_between(session, DAY_START)
    assert issue.id == receipt.issue_id and audit.issues_between(session, end=DAY_START) == []
    found = audit.report_audit(session, receipt.issue_id)
    (line,) = found.lines
    assert (line.sample, line.analyte, line.version, line.latest_version) == (S1, CU, 1, 1)
    assert line.value and line.batch_code == "PB-0001" and line.prep_method == "microwave HNO3"
    assert line.ruleset_name and len(line.ruleset_hash) == 64 and line.replay is None  # replays exactly
    assert {r.role for r in line.readings} >= {ReadingRole.CUP} and all(
        r.sample in (S1, None) for r in line.readings
    )
    (run,) = line.runs
    assert run.instrument == "ICP-MS 1" and run.file_name == "run42.csv" and run.source_kind == SourceKind.CSV
    assert run.reader == "generic_csv/1" and run.file_kept and len(run.file_sha256) == 64
    assert [e.kind for e in line.events] == [EventKind.REVIEWED, EventKind.RELEASED]
    assert line.made_by.id == "ana" and "ana" in line.analyst_ids


def test_unknown_issues_and_versions_are_not_found(session, lab):  # noqa: F811
    released_and_issued(session, lab)
    with pytest.raises(NotFound):
        audit.report_audit(session, 999)
    with pytest.raises(NotFound):
        audit.result_audit(session, S1, CU, 2)


def test_released_in_window_lists_what_a_nonconformity_may_have_touched(session, lab):  # noqa: F811
    released_and_issued(session, lab)
    (hit,) = audit.released_in_window(session, "ICP-MS 1", "Cu", DAY_START, DAY_END)
    assert (hit.sample, hit.version, hit.batch_code, hit.runs) == (S1, 1, "PB-0001", ("RUN-0042",))
    assert hit.reports and hit.released_by == REV and hit.latest_version == 1  # S2 not released
    assert audit.released_in_window(session, "ICP-MS 1", None, DAY_START, DAY_END) == [hit]
    assert audit.released_in_window(session, "ICP-MS 2", "Cu", DAY_START, DAY_END) == []
    assert audit.released_in_window(session, "ICP-MS 1", "Cu", DAY_END, DAY_END) == []


def test_a_qc_reading_carries_its_recovery(session, lab):  # noqa: F811
    header = HEADER + ",expected,qc_material"
    rows = [row + ",," for row in ROWS]
    check = edit(ROWS[0], lab_sample_id="CHECK", role="CHECK", value="9.8") + ",10,CCV 10 ppb"
    content = ("\n".join([header, *rows, check]) + "\n").encode()
    import_run(session, content, "run42.csv", REV, lab.lookup, FakeStaff())
    wf.evaluate_batch(session, "PB-0001", ANA, lab.lookup)
    found = audit.result_audit(session, S2, CU, 1)
    (qc,) = [r for r in found.readings if r.role == ReadingRole.CHECK]
    assert (qc.expected, qc.recovery, qc.sample) == (10, Decimal("0.98"), None)


def test_o1_every_batchs_samples_in_one_read(session, lab):  # noqa: F811  (app review 2026-10-30b)
    from slim_data.persistence.observations import get_batch
    from slim_data.service.views import samples_by_batch

    lab()
    found = samples_by_batch(session)
    assert found == {"PB-0001": get_batch(session, "PB-0001").samples}
    assert samples_by_batch(session, as_of=DAY_START) == {}
