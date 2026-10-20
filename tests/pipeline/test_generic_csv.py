"""generic_csv/1 (DESIGN.md §9): golden files, and one test per kind of error."""

from decimal import Decimal
from pathlib import Path

import pytest

from slim_data.domain.enums import ReadingRole
from slim_data.pipeline.generic_csv import GenericCsv
from slim_data.pipeline.reader import RowError, reader_key
from tests.factories import sample

FIXTURES = Path(__file__).parent / "fixtures"
GOOD = (FIXTURES / "good.csv").read_text()
HEADER, FIRST, *_ = GOOD.splitlines()


def parse(text: str):
    return GenericCsv().parse(text.encode())


def with_cell(column: str, value: str, line: str = FIRST) -> str:
    cells = line.split(",")
    cells[HEADER.split(",").index(column)] = value
    return f"{HEADER}\n{','.join(cells)}\n"


def test_the_good_file_parses_with_its_digits_kept():
    parsed = GenericCsv().parse((FIXTURES / "good.csv").read_bytes())
    assert parsed.errors == () and len(parsed.rows) == 6 and reader_key(GenericCsv()) == "generic_csv/1"
    assert parsed.run.run_code == "RUN-0042" and parsed.run.analyst_id == "ana"
    first, _, spike, diluted, no_mdl, blank = parsed.rows
    assert first.sample == sample(1) and str(first.value) == "10.00" and first.line == 2
    assert spike.role == ReadingRole.SPIKE and spike.spike_added == Decimal(20)
    assert diluted.dilution == Decimal(10) and no_mdl.mdl is None
    assert blank.sample is None and blank.dilution == Decimal(1)


def test_a_bom_crlf_and_blank_lines_are_fine_and_optional_columns_may_be_left_out():
    parsed = GenericCsv().parse((FIXTURES / "bom_crlf.csv").read_bytes())
    assert parsed.errors == () and len(parsed.rows) == 2
    assert parsed.rows[0].prep_method is None and parsed.rows[0].dilution == Decimal(1)
    assert parsed.rows[1].line == 4  # the blank line still counts


@pytest.mark.parametrize(
    "content, message",
    [
        (b"", "empty file"),
        (b"\xff\xfe\x00", "not UTF-8"),
        (HEADER.encode(), "only a header"),
        (HEADER.replace(",unit", "").encode(), "missing column unit"),
        ((HEADER + ",colour").encode(), "unknown column 'colour'"),
        ((HEADER + ",unit").encode(), "column unit appears twice"),
    ],
)
def test_file_errors(content, message):
    parsed = GenericCsv().parse(content)
    assert parsed.run is None and parsed.rows == ()
    assert any(e.line == 0 and message in e.message for e in parsed.errors), parsed.errors


@pytest.mark.parametrize(
    "column, value, message",
    [
        ("value", "", "is empty"),
        ("value", "12,4", None),  # a comma splits the row: wrong number of fields
        ("value", "twelve", "not a number"),
        ("value", "NaN", "not a number"),
        ("mdl", "x", "not a number"),
        ("role", "STANDARD", "expected CUP, SPIKE or BLANK"),
        ("replicate", "0", "whole number from 1"),
        ("replicate", "1.5", "whole number from 1"),
        ("lab_sample_id", "TR42-1", "not a lab sample ID"),
        ("lab_sample_id", "BLANK", "not a lab sample ID"),
        ("analyzed_at", "19/10/2026", "ISO 8601"),
        ("analyzed_at", "2026-10-19T09:30:00", "UTC offset"),
        ("spike_added", "20", "only a SPIKE row"),
        ("prep_batch", "", "is empty"),
    ],
)
def test_row_errors_name_the_line_and_column(column, value, message):
    parsed = parse(with_cell(column, value))
    [error] = parsed.errors
    assert error.line == 2
    if message is None:
        assert error.message == "wrong number of fields"
    else:
        assert error.column == column and message in error.message
        assert str(error).startswith(f"line 2, {column}: ")


def test_a_blank_row_needs_the_blank_id():
    blank = GOOD.splitlines()[-1]
    parsed = parse(with_cell("lab_sample_id", "TR00042-01", blank))
    assert parsed.errors[0].message == "a blank's lab_sample_id is BLANK"


def test_one_file_is_one_run():
    second = FIRST.replace("RUN-0042", "RUN-0043").replace(",CUP,1,", ",CUP,2,")
    parsed = parse(f"{HEADER}\n{FIRST}\n{second}\n")
    assert parsed.run is None and "run columns differ" in parsed.errors[0].message
    assert str(RowError(line=0, message="x")) == "file: x"


def test_rejected_rows_carry_their_reason():
    parsed = parse(with_cell("note", "drift", with_cell("rejected", "Yes").splitlines()[1]))
    assert parsed.rows[0].rejected and parsed.rows[0].note == "drift"
    assert not parse(with_cell("rejected", "")).rows[0].rejected
    assert "expected yes or no" in parse(with_cell("rejected", "maybe")).errors[0].message
