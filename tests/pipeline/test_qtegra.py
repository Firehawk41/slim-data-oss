"""qtegra/1 (D-89) on an invented export in Qtegra's layout."""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from slim_data.domain.enums import ReadingRole, SourceKind
from slim_data.pipeline.qtegra import Qtegra
from slim_data.pipeline.reader import reader_key
from tests.factories import sample
from tests.pipeline.qtegra_export import RUN, export, measurement

CHICAGO = ZoneInfo("America/Chicago")
READER = Qtegra(prep_batch="PB-0001", analysis_code="metals_icpms", analyst_id="ana", timezone=CHICAGO)


def parse(content=None, reader=READER):
    return reader.parse(export() if content is None else content)


def rows_for(parsed, analyte):
    return [r for r in parsed.rows if r.analyte_code == analyte]


def test_an_instrument_reader():
    assert reader_key(READER) == "qtegra/1" and READER.source_kind == SourceKind.INSTRUMENT


def test_the_run_comes_from_the_file_and_the_import_screen():
    parsed = parse()
    assert parsed.errors == ()
    run = parsed.run
    assert run.instrument == "iCAPevoRQ" and run.analyst_id == "ana"
    assert run.analyzed_at == datetime(2026, 10, 19, 9, 30, 0, 123456, tzinfo=CHICAGO)  # first start
    assert run.run_code == "iCAPevoRQ-20261019-0930"
    named = Qtegra(
        prep_batch="PB-0001", analysis_code="m", analyst_id="ana", timezone=CHICAGO, run_code="R-7"
    )
    assert parse(reader=named).run.run_code == "R-7"


def test_cups_from_the_comment_with_value_rsd_internal_standard_and_lod_as_mdl():
    cu = rows_for(parse(), "Cu")
    cups = [r for r in cu if r.role == ReadingRole.CUP]
    assert [(r.sample, r.replicate, r.value) for r in cups] == [
        (sample(1), 1, Decimal("10.00")),
        (sample(1), 2, Decimal("10.20")),
        (sample(2), 1, Decimal("5.1")),
    ]
    first = cups[0]
    assert first.rsd == Decimal("1.2") and first.istd_recovery == Decimal("91.0")
    assert first.mdl == Decimal("0.020") and first.unit == "ppb"
    assert (
        first.prep_batch == "PB-0001" and first.analysis_code == "metals_icpms" and first.spike_added is None
    )


def test_qtegra_applied_the_dilution_so_the_reading_is_stored_before_it():
    diluted = next(r for r in rows_for(parse(), "Cu") if r.sample == sample(2))
    assert diluted.dilution == 10 and diluted.value == Decimal("5.1")  # Qtegra exported 51.0


def test_blank_and_check_standard_belong_to_the_batch():
    zn = rows_for(parse(), "Zn")
    blank = next(r for r in zn if r.role == ReadingRole.BLANK)
    assert blank.sample is None and blank.value == Decimal("0.040") and blank.replicate == 1
    check = next(r for r in zn if r.role == ReadingRole.CHECK)
    assert check.sample is None and check.expected == 5 and check.qc_material == "5 ppb"
    assert check.value == Decimal("5.20") and check.istd_recovery == Decimal("97.0")


def test_every_measured_element_comes_through_and_the_internal_standard_is_never_a_reading():
    parsed = parse()
    assert {r.analyte_code for r in parsed.rows} == {"Li", "Cu", "Zn"}  # the import drops Li
    assert all(r.analyte_code != "In" for r in parsed.rows)


def test_calibration_and_standards_are_not_readings():
    parsed = parse()
    assert {c.analyte_code for c in parsed.calibrations} == {"Li", "Cu", "Zn"}  # no In
    cu = next(c for c in parsed.calibrations if c.analyte_code == "Cu")
    assert (cu.correlation, cu.determination, cu.lod, cu.bec, cu.unit) == (
        Decimal("0.9998"),
        Decimal("0.9996"),
        Decimal("0.020"),
        Decimal("0.004"),
        "ppb",
    )
    assert len(parsed.rows) == 2 + 2 + 2 + 3 + 2  # blank, cups A and B, sample 2, check (+ Li once)


def test_rows_left_out_are_listed():
    ignored = [(e.line, e.column, e.message) for e in parse().ignored]
    assert ignored == [
        (9, "Comment", "no sample ID in the comment"),
        (12, "", "no values (an aborted measurement?)"),
    ]


def test_each_check_standard_and_blank_of_the_run_is_its_own_reading():
    second = measurement("QC", "CCV", qc_action="CCV", values={"63Cu": "5.1"}, reference={"63Cu": "5"})
    checks = [r for r in rows_for(parse(export((*RUN, second))), "Cu") if r.role == ReadingRole.CHECK]
    assert [r.replicate for r in checks] == [1, 2]


def test_a_fuller_or_thinner_export_reads_by_header_names():
    thin = parse(export(leave_out=("ExtCal.RSD", "ExtCal.LOD", "ExtCal.BEC", "Raw.Average")))
    assert thin.errors == () and all(r.rsd is None and r.mdl is None for r in thin.rows)
    no_istd = parse(export(leave_out=("115In (KED)",)))
    assert no_istd.errors == () and all(r.istd_recovery is None for r in no_istd.rows)


@pytest.mark.parametrize("missing", ["Comment", "StartTime", "Sample Type", "ExtCal.Average"])
def test_a_scheme_without_what_the_reader_needs_says_what_to_tick(missing):
    parsed = parse(export(leave_out=(missing,)))
    assert parsed.run is None and len(parsed.errors) == 1
    assert "not in the export" in parsed.errors[0].message and "D-89" in parsed.errors[0].message


@pytest.mark.parametrize(
    "row, column, message",
    [
        (
            measurement("UNKNOWN", "x", comment="wafer 3", values={"63Cu": "1"}),
            "Comment",
            "expected a sample ID",
        ),
        (
            measurement("UNKNOWN", "x", comment="TR42-1 A", values={"63Cu": "1"}),
            "Comment",
            "not a lab sample ID",
        ),
        (
            measurement("UNKNOWN", "x", comment="TR00042-01 A", values={"63Cu": "abc"}),
            "ExtCal.Average",
            "not a number",
        ),
        (
            measurement("UNKNOWN", "x", comment="TR00042-01 A", values={"63Cu": "NaN"}),
            "ExtCal.Average",
            "not a number",
        ),
        (
            measurement("UNKNOWN", "x", comment="BLANK", dilution="0.5", values={"63Cu": "1"}),
            "Dilution Factor",
            "at least 1",
        ),
        (
            measurement("QC", "CCV", qc_action="CCV", values={"63Cu": "5"}),
            "ExtCal.QC.ReferenceAmount",
            "export Quality Control",
        ),
        (
            measurement("UNKNOWN", "x", comment="BLANK", values={"63Cu": "1"}, start="yesterday"),
            "StartTime",
            "not a date-time",
        ),
    ],
)
def test_row_errors_name_the_line_and_column(row, column, message):
    parsed = parse(export((*RUN, row)))
    assert [(e.line, e.column) for e in parsed.errors] == [(len(RUN) + 5, column)]
    assert message in parsed.errors[0].message


@pytest.mark.parametrize(
    "row, column, message",
    [
        (
            measurement("QC", "x", qc_action="CCB", values={"63Cu": "0"}),
            "QC Action",
            "a QC row with action 'CCB'",
        ),
        (measurement("SPIKE", "x", values={"63Cu": "1"}), "Sample Type", "sample type SPIKE"),
    ],
)
def test_rows_the_reader_does_not_take_are_ignored_not_errors(row, column, message):
    parsed = parse(export((*RUN, row)))
    assert parsed.errors == ()
    assert (len(RUN) + 5, column, message) in [(e.line, e.column, e.message) for e in parsed.ignored]


@pytest.mark.parametrize(
    "content, message",
    [
        (b"<!DOCTYPE x [<!ENTITY a 'b'>]><x/>", "DOCTYPE"),
        (b"lab_sample_id,value\n", "not XML"),
        (b'<ss:Workbook xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet"/>', "no worksheet"),
    ],
)
def test_not_a_qtegra_export(content, message):
    parsed = parse(content)
    assert parsed.run is None and message in parsed.errors[0].message


def test_two_isotopes_of_one_element_is_a_file_error():
    parsed = parse(export(elements=("63Cu", "65Cu", "66Zn")))
    assert "two isotopes of Cu" in parsed.errors[0].message


def test_an_odd_isotope_heading_is_a_file_error():
    parsed = parse(export(elements=("Copper", "66Zn")))
    assert "not an isotope heading" in parsed.errors[0].message


def test_a_file_with_only_standards_has_no_readings():
    parsed = parse(export(RUN[:4]))
    assert parsed.rows == () and "no readings" in parsed.errors[0].message


def test_a_reading_row_without_a_start_time():
    row = measurement("UNKNOWN", "x", comment="BLANK", values={"63Cu": "1"}, start="")
    parsed = parse(export((*RUN, row)))
    assert parsed.run is None and parsed.errors[0].column == "StartTime"


def test_cells_without_an_index_follow_the_one_before():
    content = export().replace(b' ss:Index="2"', b"", 1)  # SpreadsheetML may leave it out
    assert parse(content).errors == ()
