"""What the quality charts read (D-107): slim-data's QC history, read-only, for the host to hand to
slim-quality. Modules never import each other: the host adapts these records to slim-quality's port.

* `qc_history()`: every method blank, check standard and CRM reading, with its run (instrument, when,
  run order, conditions) and, for a CHECK or CRM, its expected value and recovery as `qc_check` works
  it out (measured × dilution ÷ expected); and every run's calibration (r, R², LOD, BEC).

Takes `since` (a run's analysis time) and `as_of` (D-21), like every other read.
"""

from datetime import datetime
from decimal import Decimal

from pydantic import AwareDatetime
from sqlalchemy.orm import Session

from slim_data.domain.base import Dec, Frozen
from slim_data.domain.enums import ReadingRole
from slim_data.domain.values import AnalyteRef
from slim_data.engine.numbers import engine_arithmetic
from slim_data.persistence import observations


class QcReading(Frozen):
    reading_id: int
    run_id: int
    run_code: str
    instrument: str
    analyzed_at: AwareDatetime
    sequence: int | None  # its line in the run's file (D-97)
    analyst_id: str
    batch_code: str
    analyte: AnalyteRef
    conditions: str | None  # e.g. the plasma mode (D-106)
    role: ReadingRole  # BLANK, CHECK or CRM
    measured: Dec  # value × dilution: what was in the solution prepped
    unit: str
    expected: Dec | None = None  # a CHECK or CRM's
    recovery: Dec | None = None  # measured ÷ expected, a fraction
    material: str | None = None
    lot: str | None = None
    rsd: Dec | None = None  # %
    istd_recovery: Dec | None = None  # %
    excluded: bool = False  # an analyst marked it bad (D-32)


class QcCalibration(Frozen):
    run_id: int
    run_code: str
    instrument: str
    analyzed_at: AwareDatetime
    analyte: AnalyteRef
    conditions: str | None
    unit: str
    correlation: Dec | None = None
    determination: Dec | None = None
    lod: Dec | None = None
    bec: Dec | None = None


class QcHistory(Frozen):
    readings: tuple[QcReading, ...] = ()
    calibrations: tuple[QcCalibration, ...] = ()


def qc_history(session: Session, since: datetime | None = None, as_of: datetime | None = None) -> QcHistory:
    rows, excluded, cals = observations.qc_history(session, since, as_of)
    found = []
    for row in rows:
        role = ReadingRole(row.role)
        analyte = AnalyteRef(analysis_id=row.analysis_id, code=row.analyte_code)
        with engine_arithmetic():
            measured = row.value * row.dilution
            recovery = None if row.expected is None else measured / row.expected
        found.append(
            QcReading(
                reading_id=row.id,
                run_id=row.run_id,
                run_code=row.run_code,
                instrument=row.instrument,
                analyzed_at=row.analyzed_at,
                sequence=row.sequence,
                analyst_id=row.analyst_id,
                batch_code=row.batch_code,
                analyte=analyte,
                conditions=row.conditions,
                role=role,
                measured=Decimal(measured),
                unit=row.unit,
                expected=row.expected,
                recovery=recovery,
                material=row.material,
                lot=row.lot,
                rsd=row.rsd,
                istd_recovery=row.istd_recovery,
                excluded=row.id in excluded,
            )
        )
    calibrations = tuple(
        QcCalibration(
            run_id=c.run_id,
            run_code=c.run_code,
            instrument=c.instrument,
            analyzed_at=c.analyzed_at,
            analyte=AnalyteRef(analysis_id=c.analysis_id, code=c.analyte_code),
            conditions=c.conditions,
            unit=c.unit,
            correlation=c.correlation,
            determination=c.determination,
            lod=c.lod,
            bec=c.bec,
        )
        for c in cals
    )
    return QcHistory(readings=tuple(found), calibrations=calibrations)
