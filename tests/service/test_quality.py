"""The QC history slim-quality reads (D-107), through a qtegra/2 import that keeps the modes (D-106)."""

from decimal import Decimal
from zoneinfo import ZoneInfo

from slim_data.demo.qtegra_export import export
from slim_data.domain.enums import ExclusionKind, ReadingRole
from slim_data.domain.observations import ReadingExclusion
from slim_data.persistence.observations import exclude_reading
from slim_data.pipeline.qtegra import Qtegra2
from slim_data.service.imports import import_run
from slim_data.service.quality import qc_history
from tests.factories import ANA, ROB
from tests.persistence.conftest import instant
from tests.service.fakes import FakeLookup, FakeStaff


def reader():
    return Qtegra2(
        prep_batch="PB-0009",
        analysis_code="metals_icpms",
        analyst_id="ana",
        timezone=ZoneInfo("America/Chicago"),
        prep_method="microwave HNO3",
    )


def imported(session):
    content = export(modes={"66Zn": "CP-Off-None"})
    return import_run(session, content, "labbook.xml", ROB, FakeLookup(), FakeStaff(), reader())


def test_blanks_and_checks_with_their_run_conditions_and_recovery(session):
    imported(session)
    history = qc_history(session)
    assert {r.role for r in history.readings} == {ReadingRole.BLANK, ReadingRole.CHECK}
    check = next(r for r in history.readings if r.role == ReadingRole.CHECK and r.analyte.code == "Cu")
    assert check.instrument == "iCAPevoRQ" and check.conditions == "KED" and check.batch_code == "PB-0009"
    assert check.measured == Decimal("4.90") and check.expected == Decimal("5")
    assert check.recovery == Decimal("0.98") and check.material == "5 ppb" and check.istd_recovery == 97
    zinc = next(r for r in history.readings if r.analyte.code == "Zn")
    assert zinc.conditions == "CP-Off-None"
    blank = next(r for r in history.readings if r.role == ReadingRole.BLANK)
    assert blank.expected is None and blank.recovery is None
    assert {(c.analyte.code, c.conditions) for c in history.calibrations} == {
        ("Cu", "KED"),
        ("Zn", "CP-Off-None"),
    }
    stored = {c.run_id for c in history.calibrations} | {r.run_id for r in history.readings}
    assert len(stored) == 1
    assert sorted(r.sequence for r in history.readings) == [r.sequence for r in history.readings]


def test_excluded_readings_are_marked_and_since_and_as_of_filter(session):
    before = instant(session)
    imported(session)
    history = qc_history(session)
    check = next(r for r in history.readings if r.role == ReadingRole.CHECK)
    exclusion = ReadingExclusion(
        reading_id=check.reading_id, kind=ExclusionKind.EXCLUDED, reason="bubble", actor=ANA
    )
    exclude_reading(session, exclusion)
    marked = {r.reading_id: r.excluded for r in qc_history(session).readings}
    assert marked[check.reading_id] is True
    assert qc_history(session, as_of=before) == qc_history(
        session, since=check.analyzed_at.replace(year=2100)
    )
    assert qc_history(session, as_of=before).readings == ()


def test_a_qc_only_run_registers_a_qc_only_batch_with_nothing_to_review(session):  # D-108
    from slim_data.demo.qtegra_export import RUN
    from slim_data.service.views import batches

    qc_rows = tuple(m for m in RUN if m["kind"] != "UNKNOWN" or m["comment"] == "BLANK")
    content = export(run=qc_rows)
    receipt = import_run(session, content, "qc.xml", ROB, FakeLookup(), FakeStaff(), reader())
    assert receipt.batches_registered == ("PB-0009",)
    (summary,) = batches(session)
    assert summary.qc_only
    assert {r.role for r in qc_history(session).readings} == {ReadingRole.BLANK, ReadingRole.CHECK}
