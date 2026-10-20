"""Preview and all-or-nothing import (DESIGN.md §9, D-24, D-66)."""

from dataclasses import dataclass, field
from pathlib import Path

import pytest
from sqlalchemy import func, select

from slim_data.domain.enums import IdentitySource, ReadingRole
from slim_data.persistence.observations import evaluation_input, get_batch, get_run, register_batch
from slim_data.persistence.tables import prep_batches, readings, runs
from slim_data.service.imports import ImportRefused, import_run, preview_import
from tests.factories import ANA, ROB, analyte, qty, sample
from tests.persistence.conftest import make_batch

GOOD = (Path(__file__).parents[1] / "pipeline" / "fixtures" / "good.csv").read_bytes()
HEADER, *ROWS = GOOD.decode().splitlines()


@dataclass
class FakeLookup:
    analyses: dict = field(default_factory=lambda: {"metals_icpms": 1, "anions_ic": 2})
    samples: set = field(default_factory=lambda: {sample(1), sample(2), sample(3)})
    unordered: set = field(default_factory=set)  # (sample, analysis_id) not ordered

    def analysis_id(self, code):
        return self.analyses.get(code)

    def sample_exists(self, ref):
        return ref in self.samples

    def sample_requests(self, ref, analysis_id):
        return (ref, analysis_id) not in self.unordered

    def analyte_valid(self, a):
        return a.code in {"Cu", "Zn", "Fe", "Cl-"}

    def limits_for(self, ref, a):
        return None


class FakeStaff:
    def find(self, staff_id):
        return {"ana": ANA, "rob": ROB}.get(staff_id)


def preview(session, content=GOOD, name="run42.csv", lookup=None):
    return preview_import(session, content, name, ROB, lookup or FakeLookup(), FakeStaff())


def imported(session, content=GOOD, name="run42.csv", lookup=None):
    return import_run(session, content, name, ROB, lookup or FakeLookup(), FakeStaff())


def csv(*rows: str) -> bytes:
    return ("\n".join([HEADER, *rows]) + "\n").encode()


def edit(row: str, **cells) -> str:
    values = row.split(",")
    for column, value in cells.items():
        values[HEADER.split(",").index(column)] = value
    return ",".join(values)


def count(session, table):
    return session.scalar(select(func.count()).select_from(table))


def test_preview_writes_nothing_and_shows_the_whole_plan(session):
    plan = preview(session)
    assert plan.ok and plan.errors == ()
    assert [b.code for b in plan.new_batches] == ["PB-0001"]
    assert plan.new_batches[0].samples == (sample(1), sample(2))
    assert plan.new_batches[0].spike_added(sample(1), analyte("Cu")) == qty("20")
    assert len(plan.readings) == 6 and plan.source.reader == "generic_csv/1"
    assert plan.run.analyst.id == "ana" and plan.run.analyst.identity_source == IdentitySource.ASSERTED
    assert plan.run.imported_by == ROB and plan.run.entered_by == {"ana", "rob"}
    assert count(session, runs) == count(session, prep_batches) == 0


def test_import_registers_the_batch_and_stores_every_reading(session):
    receipt = imported(session)
    assert receipt.readings == 6 and receipt.batches_registered == ("PB-0001",)
    stored = get_run(session, receipt.run_id)
    assert stored.imported_by == ROB and stored.analyst.identity_source == IdentitySource.ASSERTED
    data = evaluation_input(session, "PB-0001")
    assert [str(r.value.value) for r in data.readings][:2] == ["10.00", "10.20"]
    assert data.readings[-1].role == ReadingRole.BLANK and data.readings[-1].sample is None


def test_the_same_file_twice_is_refused_with_a_pointer(session):
    receipt = imported(session)
    with pytest.raises(ImportRefused, match=f"already imported as run {receipt.run_id}"):
        imported(session, name="renamed.csv")


def test_any_error_means_nothing_is_written(session):
    bad = csv(*ROWS[:3], edit(ROWS[3], lab_sample_id="TR00042-09"))
    with pytest.raises(ImportRefused) as refused:
        imported(session, bad)
    assert [str(e) for e in refused.value.plan.errors] == ["line 5, lab_sample_id: no sample TR00042-09"]
    assert count(session, runs) == count(session, readings) == count(session, prep_batches) == 0


@pytest.mark.parametrize(
    "row, column, message",
    [
        (edit(ROWS[0], analysis_code="nope"), "analysis_code", "unknown analysis 'nope'"),
        (edit(ROWS[0], analyte="Xx"), "analyte", "Xx is not an analyte of metals_icpms"),
        (edit(ROWS[0], analyte="Cl-", analysis_code="anions_ic"), "analysis_code", "mixes analyses"),
        (edit(ROWS[0], mdl="0"), "", "MDL must be positive"),
        (edit(ROWS[0], dilution_factor="0.5"), "", "at least 1"),
        (edit(ROWS[1], replicate="1"), "replicate", "same reading as line 2"),
        (edit(ROWS[0], prep_method="hot block", replicate="3"), "prep_method", "more than one prep_method"),
        (
            edit(ROWS[2], spike_added="25", replicate="2"),
            "spike_added",
            "differs from the amount on another row",
        ),
        (edit(ROWS[3], role="SPIKE", spike_added="-5"), "prep_batch", "positive amount"),
    ],
)
def test_row_errors_in_a_new_batch(session, row, column, message):
    rows = [ROWS[0], ROWS[1], ROWS[2], row] if row is not ROWS[0] else [row]
    plan = preview(session, csv(*rows))
    assert any(e.column == column and message in e.message for e in plan.errors), plan.errors
    assert not plan.ok


def test_a_new_batch_needs_a_prep_method(session):
    plan = preview(session, csv(edit(ROWS[0], prep_method="")))
    assert "needs prep_method to register it" in plan.errors[0].message


def test_unknown_analyst_and_unordered_analysis(session):
    plan = preview(session, csv(*(edit(r, analyst="zed") for r in ROWS)))
    assert plan.run is None and plan.errors[0].message == "no staff member 'zed'"
    unordered = FakeLookup(unordered={(sample(2), 1)})
    plan = preview(session, lookup=unordered)
    assert {e.message for e in plan.errors} == {"metals_icpms was not ordered for TR00042-02"}


def test_a_file_can_add_readings_to_a_registered_batch(session):
    register_batch(session, make_batch(), ANA)  # samples 1 and 2, spike 20 ppb Cu on sample 1
    receipt = imported(session, csv(*(edit(r, prep_method="") for r in ROWS)))
    assert receipt.batches_registered == () and receipt.readings == 6
    assert get_batch(session, "PB-0001").samples == (sample(1), sample(2))


@pytest.mark.parametrize(
    "row, column, message",
    [
        (edit(ROWS[0], lab_sample_id="TR00042-03"), "lab_sample_id", "TR00042-03 is not in batch PB-0001"),
        (edit(ROWS[0], prep_method="hot block"), "prep_method", "was prepped by 'microwave HNO3'"),
        (edit(ROWS[2], spike_added="25"), "spike_added", "records 20 ppb for this spike"),
        (edit(ROWS[3], analyte="Zn", role="SPIKE", spike_added="5"), "spike_added", "records no amount"),
        (edit(ROWS[0], analyte="Cl-", analysis_code="anions_ic"), "analysis_code", "for another analysis"),
    ],
)  # fmt: skip
def test_rows_must_fit_a_registered_batch(session, row, column, message):
    register_batch(session, make_batch(), ANA)
    plan = preview(session, csv(row))
    assert any(e.column == column and message in e.message for e in plan.errors), plan.errors


def test_a_registered_batch_refuses_a_reading_it_already_has(session):
    imported(session)
    plan = preview(session, csv(edit(ROWS[0], run_code="RUN-0043")), name="run43.csv")
    assert plan.errors[0].message == "batch PB-0001 already has this reading"


def test_reader_errors_come_through_in_line_order(session):
    plan = preview(session, csv(edit(ROWS[1], value="x"), edit(ROWS[0], analysis_code="nope")))
    assert [(e.line, e.column) for e in plan.errors] == [(2, "value"), (3, "analysis_code")]
