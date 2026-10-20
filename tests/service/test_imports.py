"""Preview and all-or-nothing import (DESIGN.md §9, D-24, D-66)."""

from pathlib import Path

import pytest
from sqlalchemy import func, select

from slim_data.domain.enums import IdentitySource, ReadingRole
from slim_data.engine.runner import evaluate
from slim_data.persistence.observations import (
    add_to_batch,
    evaluation_input,
    get_batch,
    get_run,
    register_batch,
)
from slim_data.persistence.tables import prep_batches, readings, runs
from slim_data.service.imports import ImportRefused, import_run, preview_import
from tests.engine.helpers import demo
from tests.factories import ANA, ROB, analyte, qty, sample
from tests.persistence.conftest import instant, make_batch
from tests.service.fakes import FakeLookup, FakeStaff

GOOD = (Path(__file__).parents[1] / "pipeline" / "fixtures" / "good.csv").read_bytes()
HEADER, *ROWS = GOOD.decode().splitlines()


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
        (edit(ROWS[0], prep_method="hot block"), "prep_method", "was prepped by 'microwave HNO3'"),
        (edit(ROWS[2], spike_added="25"), "spike_added", "records 20 ppb for this spike"),
        (edit(ROWS[0], analyte="Cl-", analysis_code="anions_ic"), "analysis_code", "for another analysis"),
    ],
)  # fmt: skip
def test_rows_must_fit_a_registered_batch(session, row, column, message):
    register_batch(session, make_batch(), ANA)
    plan = preview(session, csv(row))
    assert any(e.column == column and message in e.message for e in plan.errors), plan.errors


# --- re-exports, attempts and readings rejected in the run (D-66) ------------------------------------


def test_a_re_export_of_a_growing_file_skips_what_was_imported(session):
    first = imported(session, csv(*ROWS[:3]), name="export1.csv")
    later = imported(session, csv(*ROWS), name="export2.csv")
    assert (first.readings, later.readings, later.skipped) == (3, 3, 3)
    assert len(evaluation_input(session, "PB-0001").readings) == 6
    nothing_new = imported(session, csv(*ROWS, ""), name="export3.csv")
    assert (nothing_new.run_id, nothing_new.readings, nothing_new.skipped) == (None, 0, 6)


def test_a_re_export_that_changes_a_number_is_refused(session):
    imported(session, csv(*ROWS[:3]), name="export1.csv")
    plan = preview(session, csv(edit(ROWS[0], value="10.0"), *ROWS[1:]), name="export2.csv")
    assert [str(e) for e in plan.errors] == [
        "line 2, value: differs from the reading imported before (10.00 ppb)"
    ]


def test_a_cup_rejected_and_rerun_in_the_run_keeps_both_and_the_rerun_counts(session):
    rejected = edit(ROWS[1], value="14.0", rejected="yes", note="drift check failed")
    rerun = edit(ROWS[1], value="10.30")
    receipt = imported(session, csv(ROWS[0], rejected, ROWS[2], rerun, *ROWS[3:]))
    assert (receipt.readings, receipt.rejected) == (7, 1)
    data = evaluation_input(session, "PB-0001")
    cup_2 = [
        r for r in data.readings if r.sample == sample(1) and r.role == ReadingRole.CUP and r.replicate == 2
    ]
    assert [(str(r.value.value), r.attempt) for r in cup_2] == [("14.0", 1), ("10.30", 2)]
    assert data.excluded_reading_ids == {cup_2[0].id}
    outcome = evaluate(data, demo()).cell(sample(1), analyte("Cu"))
    assert str(outcome.raw) == "10.15" and outcome.trace[0]["set_aside"] == [cup_2[0].id]


def test_a_rejection_added_on_re_export_excludes_the_stored_reading(session):
    imported(session, csv(*ROWS[:2]), name="export1.csv")
    receipt = imported(session, csv(ROWS[0], edit(ROWS[1], rejected="x"), *ROWS[2:]), name="export2.csv")
    assert (receipt.skipped, receipt.rejected, receipt.readings) == (2, 1, 4)
    data = evaluation_input(session, "PB-0001")
    assert data.excluded_reading_ids == {data.readings[1].id}


def test_a_rerun_in_a_later_run_is_a_newer_attempt(session):
    imported(session)
    receipt = imported(session, csv(edit(ROWS[0], run_code="RUN-0043", value="10.10")), name="run43.csv")
    assert (receipt.readings, receipt.skipped) == (1, 0)
    cups = [
        r for r in evaluation_input(session, "PB-0001").readings if r.replicate == 1 and r.sample == sample(1)
    ]
    assert [r.attempt for r in cups if r.role == ReadingRole.CUP] == [1, 2]


def test_reader_errors_come_through_in_line_order(session):
    plan = preview(session, csv(edit(ROWS[1], value="x"), edit(ROWS[0], analysis_code="nope")))
    assert [(e.line, e.column) for e in plan.errors] == [(2, "value"), (3, "analysis_code")]


def test_a_later_file_adds_samples_and_spike_amounts_to_the_batch_as_of_when_it_did(session):
    imported(session, csv(*ROWS[:2]), name="export1.csv")  # sample 1's cups only: no spike yet
    before = instant(session)
    receipt = imported(session, csv(*ROWS), name="export2.csv")
    assert (receipt.readings, receipt.skipped, receipt.batches_registered) == (4, 2, ())
    batch = get_batch(session, "PB-0001")
    assert batch.samples == (sample(1), sample(2)) and batch.spike_added(sample(1), analyte("Cu")) == qty(
        "20"
    )
    assert get_batch(session, "PB-0001", as_of=before).samples == (sample(1),)
    assert get_batch(session, "PB-0001", as_of=before).spikes == ()


def test_adding_to_a_batch_never_changes_what_it_has(session):
    register_batch(session, make_batch(), ANA)
    with pytest.raises(ValueError, match="already has TR00042-01"):
        add_to_batch(session, "PB-0001", [sample(1)], [], ANA)
    with pytest.raises(ValueError, match="already has TR00042-01 Cu"):
        add_to_batch(session, "PB-0001", [], [make_batch().spikes[0]], ANA)
    plan = preview(session, csv(edit(ROWS[2], spike_added="25")))
    assert "records 20 ppb for this spike" in plan.errors[0].message
    two = [
        edit(ROWS[3], role="SPIKE", spike_added="5"),
        edit(ROWS[4], role="SPIKE", replicate="2", spike_added="6"),
    ]
    assert "differs from the amount on another row" in preview(session, csv(*two)).errors[0].message
