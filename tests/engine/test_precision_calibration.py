"""precision@1 and calibration@1 (D-88, D-90): suggestions from the instrument's evidence, and a failed
calibration as a rule. Demo: RSD 10 % above 10 × MDL × DF, internal standard 50–150 %, R² ≥ 0.995."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from slim_data.domain.enums import Severity, Verdict
from slim_data.domain.observations import Calibration
from slim_data.engine.runner import evaluate
from slim_data.engine.steps.calibration import CalibrationParams
from slim_data.engine.steps.precision import PrecisionParams
from tests.engine.helpers import codes, cups, demo, evaluation_input, step_trace
from tests.factories import analyte


def evidence(readings, rsd=(None, None), istd=(None, None), run_id=None):
    """Gives each cup its RSD and internal standard recovery (and run), as qtegra/1 would."""
    return [
        r.model_copy(
            update={
                "rsd": None if s is None else Decimal(s),
                "istd_recovery": None if i is None else Decimal(i),
                "run_id": run_id,
            }
        )
        for r, s, i in zip(readings, rsd, istd, strict=True)
    ]


def run(readings, calibrations=(), **changes):
    data = evaluation_input(readings).model_copy(update={"calibrations": tuple(calibrations)})
    (cell,) = evaluate(data, demo(**changes)).cells
    return cell


def test_steady_cups_raise_nothing_and_the_trace_keeps_rsd_and_recovery():
    cell = run(evidence(cups(1, "Cu", "10.0", "10.2"), rsd=("2.1", "3.0"), istd=("95", "97")))
    assert codes(cell, "precision") == []
    trace = step_trace(cell, "precision")["cups"]
    assert trace[0] == {
        "replicate": 1,
        "rsd": "2.1",
        "rsd_judged": True,
        "rsd_floor": "0.50",
        "istd_recovery": "95",
    }


def test_a_high_rsd_well_above_the_mdl_is_a_suggestion_not_a_block():
    cell = run(evidence(cups(1, "Cu", "10.0", "10.2"), rsd=("17.2", "3.0")))
    assert codes(cell, "precision") == ["RSD_HIGH"]
    flag = next(f for f in cell.flags if f.code == "RSD_HIGH")
    assert flag.message == "cup 1 17.2 % (limit 10 % above 10 × MDL)"
    assert flag.severity == Severity.WARN  # a suggestion in the demo: the analyst decides


def test_near_the_mdl_a_high_rsd_is_normal_and_not_judged():
    # 10 × MDL 0.05 = 0.5: a cup at 0.40 is below the zone, whatever its RSD
    cell = run(evidence(cups(1, "Cu", "0.40", "0.42"), rsd=("45", "60")))
    assert codes(cell, "precision") == []
    assert step_trace(cell, "precision")["cups"][0]["rsd_judged"] is False


def test_the_zone_follows_the_dilution():
    # MDL 0.05 × DF 10 = 0.5 effective; 10 × that = 5. A diluted cup worth 4.0 isn't judged.
    cell = run(evidence(cups(1, "Cu", "0.40", "0.41", dilution="10"), rsd=("30", "30")))
    assert codes(cell, "precision") == []
    assert step_trace(cell, "precision")["cups"][0]["rsd_floor"] == "5.00"


def test_no_mdl_means_no_zone_so_rsd_is_not_judged():
    cell = run(evidence(cups(1, "Cu", "10.0", "10.2", mdl=None), rsd=("30", "30")))
    assert "RSD_HIGH" not in codes(cell)


def test_internal_standard_outside_its_window_both_ways():
    low = run(evidence(cups(1, "Cu", "10.0", "10.2"), istd=("49", "97")))
    assert codes(low, "precision") == ["ISTD_LOW"]
    assert next(f for f in low.flags if f.code == "ISTD_LOW").message == "cup 1 49 % (window 50 %–150 %)"
    high = run(evidence(cups(1, "Cu", "10.0", "10.2"), istd=("100", "151")))
    assert codes(high, "precision") == ["ISTD_HIGH"]


def test_an_excluded_cup_is_not_judged():
    readings = evidence(cups(1, "Cu", "10.0", "10.2"), rsd=("40", "3"), istd=("20", "95"))
    data = evaluation_input(readings, excluded=(readings[0].id,))
    (cell,) = evaluate(data, demo()).cells
    assert codes(cell, "precision") == []


def test_missing_evidence_only_matters_when_the_ruleset_requires_it():
    plain = cups(1, "Cu", "10.0", "10.2")  # a CSV: no RSD, no internal standard
    assert codes(run(plain), "precision") == []
    strict = {
        "rsd_limit": "10",
        "istd_low": "50",
        "istd_high": "150",
        "rsd_required": True,
        "istd_required": True,
    }
    cell = run(plain, precision=strict)
    assert codes(cell, "precision") == ["ISTD_MISSING", "RSD_MISSING"]
    assert (
        next(f for f in cell.flags if f.code == "RSD_MISSING").message
        == "cup 1, cup 2: no figure in the file"
    )


def test_without_limits_nothing_is_judged():
    cell = run(evidence(cups(1, "Cu", "10.0", "10.2"), rsd=("40", "40"), istd=("10", "10")), precision={})
    assert codes(cell, "precision") == []


@pytest.mark.parametrize(
    "params, message",
    [
        ({"istd_low": "50"}, "needs both ends"),
        ({"istd_low": "150", "istd_high": "50"}, "low end must be below"),
        ({"rsd_limit": "0"}, "greater than 0"),
    ],
)
def test_precision_params_are_checked(params, message):
    with pytest.raises(ValidationError, match=message):
        PrecisionParams(**params)


# --- calibration@1 -----------------------------------------------------------------------------------


def cal(r2, run_id=7, code="Cu"):
    return Calibration(analyte=analyte(code), run_id=run_id, unit="ppb", determination=Decimal(r2))


def test_a_run_below_the_minimum_r2_fails_every_result_from_it():
    cell = run(evidence(cups(1, "Cu", "10.0", "10.2"), run_id=7), calibrations=[cal("0.9941")])
    assert codes(cell, "calibration") == ["CAL_FAILED"]
    assert next(f for f in cell.flags if f.code == "CAL_FAILED").message == "run 7 R² 0.9941 (minimum 0.995)"
    assert cell.verdict == Verdict.RERUN
    assert step_trace(cell, "calibration")["runs"] == [{"run": 7, "r2": "0.9941"}]


def test_a_good_calibration_passes_and_another_runs_or_elements_doesnt_count():
    good = run(evidence(cups(1, "Cu", "10.0", "10.2"), run_id=7), calibrations=[cal("0.9990")])
    assert codes(good, "calibration") == []
    other = [cal("0.90", run_id=8), cal("0.90", code="Zn")]
    assert (
        codes(run(evidence(cups(1, "Cu", "10.0", "10.2"), run_id=7), calibrations=other), "calibration") == []
    )


def test_no_calibration_or_no_r2_is_not_judged():
    assert step_trace(run(cups(1, "Cu", "10.0", "10.2")), "calibration")["runs"] == []
    no_r2 = Calibration(analyte=analyte("Cu"), run_id=7, unit="ppb", correlation=Decimal("0.99"))
    cell = run(evidence(cups(1, "Cu", "10.0", "10.2"), run_id=7), calibrations=[no_r2])
    assert codes(cell, "calibration") == [] and step_trace(cell, "calibration")["runs"] == [
        {"run": 7, "r2": None}
    ]


def test_calibration_params_are_checked():
    with pytest.raises(ValidationError):
        CalibrationParams(min_determination="1.5")
