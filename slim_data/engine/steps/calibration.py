"""calibration@1 (D-88, D-90): a run whose calibration failed for an element puts every result of
that element from the run in doubt.

CAL_FAILED when the run's R² for the element is below the ruleset's minimum, on each result whose
cups or spikes came from that run (demo RERUN). A run with no R² for the element isn't judged: a hand-made
file carries no calibration. The trace keeps each run's R²: what a chart plots.
"""

from pydantic import Field

from slim_data.domain.base import Dec, Frozen
from slim_data.engine.model import BatchState
from slim_data.engine.numbers import text
from slim_data.engine.step import Step


class CalibrationParams(Frozen):
    min_determination: Dec = Field(gt=0, le=1)  # R², e.g. 0.995


class CalibrationCheck(Step):
    name = "calibration"
    version = 1
    Params = CalibrationParams
    flag_codes = frozenset({"CAL_FAILED"})

    def run(self, state: BatchState, params: CalibrationParams) -> None:
        for cell in state.cells.values():
            runs = sorted(
                {r.run_id for r in (*cell.cups.values(), *cell.spikes.values()) if r.run_id is not None}
            )
            judged, failed = [], []
            for run_id in runs:
                calibration = state.calibrations.get((run_id, cell.analyte))
                if calibration is None:
                    continue  # a run without a calibration (e.g. a CSV) leaves no trace
                r2 = calibration.determination
                judged.append({"run": run_id, "r2": None if r2 is None else text(r2)})
                if r2 is not None and r2 < params.min_determination:
                    failed.append(f"run {run_id} R² {text(r2)}")
            if failed:
                self.flag(
                    cell, "CAL_FAILED", f"{', '.join(failed)} (minimum {text(params.min_determination)})"
                )
            self.trace(cell, runs=judged)
