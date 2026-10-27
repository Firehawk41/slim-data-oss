"""cup_choice@1 (D-31, D-32, D-35, D-51, D-52): which cup do we believe when the cups disagree?

An evidence ladder; the first conclusive rung decides and the trace records which one:
  1. analyst exclusion: a human-observed fact. Always first, and it applies even if the cups agree
     (a reading marked bad never feeds a mean).
  2. scale fault (D-51): one whole cup scaled (weighing, dilution), seen as the same cup A / cup B
     ratio across most elements of a sample. Decided once per sample from spike witnesses; applies to
     the elements in the cluster. Contamination (element by element) never forms a cluster.
  3+. per-element rungs, in the order the ruleset gives: spike witness, direction prior.
  last: no decision → CUP_CHOICE_UNDECIDED, whose severity the ruleset sets (NEEDS_SUPERVISOR or RERUN).
"""

from collections import defaultdict
from decimal import Decimal
from typing import Literal, Self

from pydantic import Field, model_validator

from slim_data.domain.base import Dec, Frozen
from slim_data.engine.model import BatchState, Cell, Selection
from slim_data.engine.numbers import median, rpd, text
from slim_data.engine.step import Step
from slim_data.engine.steps.agreement import AgreementParams, replicates_agree

Direction = Literal["CONTAMINATION_PRONE", "LOSS_PRONE", "NEUTRAL"]
Rung = Literal["spike_witness", "direction_prior"]


class ScaleFaultParams(Frozen):
    enabled: bool = True
    min_elements: int = Field(default=5, ge=2)
    cluster_fraction: Dec = Field(default=Decimal("0.75"), gt=0, le=1)
    ratio_tolerance: Dec = Field(default=Decimal("0.05"), gt=0)
    min_witnesses: int = Field(default=2, ge=1)


class PriorRule(Frozen):
    code: str = Field(min_length=1)
    prep_method: str | None = None  # None = the element's default; a prep-specific row wins (D-52)
    direction: Direction


class CupChoiceParams(Frozen):
    rungs: tuple[Rung, ...] = ("spike_witness", "direction_prior")
    spike_precision: Dec = Field(default=Decimal("0.05"), gt=0)  # D-35
    scale_fault: ScaleFaultParams = ScaleFaultParams()
    direction_prior: tuple[PriorRule, ...] = ()

    @model_validator(mode="after")
    def _unique(self) -> Self:
        if len(set(self.rungs)) != len(self.rungs):
            raise ValueError("a rung appears twice")
        keys = [(r.code, r.prep_method) for r in self.direction_prior]
        if len(set(keys)) != len(keys):
            raise ValueError("two direction-prior rows for the same element and prep method")
        return self

    def direction(self, code: str, prep_method: str) -> tuple[Direction, str]:
        default: tuple[Direction, str] = ("NEUTRAL", "no row")
        for rule in self.direction_prior:
            if rule.code == code and rule.prep_method == prep_method:
                return rule.direction, f"{code} / {prep_method}"
            if rule.code == code and rule.prep_method is None:
                default = (rule.direction, f"{code} default")
        return default


def spike_witness(
    cell: Cell, added: Decimal | None, agreement: AgreementParams, precision: Decimal
) -> tuple[int | None, dict]:
    """D-35: w = spiked − added picks the nearer cup, only if the gap clears the spike's own noise."""
    if not cell.spikes:
        return None, {"witness": "no spike"}
    if added is None:
        return None, {"witness": "no spike amount recorded"}
    spikes = [cell.spikes[r].value.value * cell.spikes[r].dilution for r in sorted(cell.spikes)]
    if len(spikes) >= 2:
        agree, zone = replicates_agree(spikes[0], spikes[1], cell.mdl, agreement)  # D-34
        if not agree:
            return None, {"witness": "spikes disagree", "zone": zone.value}
        spiked = (spikes[0] + spikes[1]) / 2
    else:
        spiked = spikes[0]
    w = spiked - added
    d1, d2 = abs(w - cell.cup_value(1)), abs(w - cell.cup_value(2))
    margin, noise = abs(d1 - d2), spiked * precision
    detail = {"w": text(w), "margin": text(margin), "noise": text(noise)}
    if margin <= noise:
        return None, {"witness": "inconclusive", **detail}
    return (1 if d1 < d2 else 2), {"witness": "conclusive", **detail}


class CupChoice(Step):
    name = "cup_choice"
    version = 1
    Params = CupChoiceParams
    requires = ("cup_shape", "agreement")
    flag_codes = frozenset(
        {
            "CUP_CHOSEN_BY_EXCLUSION",
            "ALL_CUPS_EXCLUDED",
            "SCALE_FAULT_CUP_CHOSEN",
            "SCALE_FAULT_UNRESOLVED",
            "CUP_CHOSEN_BY_SPIKE",
            "CUP_CHOSEN_BY_PRIOR",
            "CUP_CHOICE_UNDECIDED",
        }
    )

    def run(self, state: BatchState, params: CupChoiceParams) -> None:
        agreement: AgreementParams = state.shared["agreement"]
        witnesses = {
            key: spike_witness(cell, self._added(state, cell), agreement, params.spike_precision)
            for key, cell in state.cells.items()
            if cell.usable
        }
        scale = self._scale_faults(state, params, agreement, witnesses) if params.scale_fault.enabled else {}
        for key, cell in state.cells.items():
            if not cell.usable:
                continue
            if cell.excluded:
                self._by_exclusion(cell)
            elif key in scale:
                self._by_scale_fault(cell, *scale[key])
            elif cell.agree:
                continue
            else:
                self._ladder(state, cell, params, witnesses[key])

    # --- rungs -------------------------------------------------------------------------------------

    def _by_exclusion(self, cell: Cell) -> None:
        remaining = sorted(set(cell.cups) - cell.excluded)
        if not remaining:
            cell.selection = Selection.NONE
            self.flag(cell, "ALL_CUPS_EXCLUDED", "every cup was excluded by the analyst")
            self.trace(cell, rung="analyst_exclusion", chosen_replicate=None)
            return
        self._choose(cell, remaining[0], "analyst_exclusion", "CUP_CHOSEN_BY_EXCLUSION")
        self.trace(
            cell, rung="analyst_exclusion", chosen_replicate=remaining[0], excluded=sorted(cell.excluded)
        )

    def _by_scale_fault(self, cell: Cell, replicate: int | None, detail: dict) -> None:
        if replicate is None:
            cell.selection = Selection.NONE
            self.flag(
                cell, "SCALE_FAULT_UNRESOLVED", "one cup is scaled across the sample; no witness decides"
            )
        else:
            self._choose(cell, replicate, "scale_fault", "SCALE_FAULT_CUP_CHOSEN")
        self.trace(cell, rung="scale_fault", chosen_replicate=replicate, **detail)

    def _ladder(self, state: BatchState, cell: Cell, params: CupChoiceParams, witness) -> None:
        tried = []
        for rung in params.rungs:
            if rung == "spike_witness":
                replicate, detail = witness
                tried.append({"rung": rung, **detail})
                if replicate is not None:
                    self._choose(cell, replicate, rung, "CUP_CHOSEN_BY_SPIKE")
                    self.trace(cell, rung=rung, chosen_replicate=replicate, tried=tried)
                    return
            else:
                direction, row = params.direction(cell.analyte.code, state.batch.prep_method)
                tried.append({"rung": rung, "direction": direction, "row": row})
                if direction != "NEUTRAL":
                    low, high = sorted((1, 2), key=cell.cup_value)
                    replicate = low if direction == "CONTAMINATION_PRONE" else high
                    self._choose(cell, replicate, rung, "CUP_CHOSEN_BY_PRIOR")
                    self.trace(cell, rung=rung, chosen_replicate=replicate, tried=tried)
                    return
        cell.selection = Selection.NONE
        self.flag(cell, "CUP_CHOICE_UNDECIDED", "no rung could choose a cup")
        self.trace(cell, rung="no_decision", chosen_replicate=None, tried=tried)

    def _choose(self, cell: Cell, replicate: int, rung: str, code: str) -> None:
        cell.selection, cell.chosen_replicate, cell.chosen_by = Selection.CHOSEN, replicate, rung
        self.flag(cell, code, f"believed cup {replicate} ({rung})")

    # --- scale fault (D-51) -------------------------------------------------------------------------

    @staticmethod
    def _added(state: BatchState, cell: Cell) -> Decimal | None:
        added = state.batch.spike_added(cell.sample, cell.analyte)
        if added is None or added.unit != cell.unit:
            return None
        return added.value

    def _scale_faults(self, state, params, agreement, witnesses) -> dict:
        by_sample = defaultdict(list)
        for key, cell in state.cells.items():
            if not cell.usable or cell.excluded or cell.mdl is None:
                continue
            switch = agreement.switch_multiple * cell.mdl
            if cell.cup_value(1) >= switch and cell.cup_value(2) >= switch:
                by_sample[cell.sample].append(key)
        decided = {}
        p = params.scale_fault
        for keys in by_sample.values():
            if len(keys) < p.min_elements:
                continue
            ratios = {k: state.cells[k].cup_value(1) / state.cells[k].cup_value(2) for k in keys}
            middle = median(ratios.values())
            cluster = [k for k, r in ratios.items() if abs(r / middle - 1) <= p.ratio_tolerance]
            if (
                Decimal(len(cluster)) < p.cluster_fraction * len(keys)
                or rpd(middle, Decimal(1)) <= agreement.rpd_limit
            ):
                continue
            votes = {witnesses[k][0] for k in cluster if witnesses[k][0] is not None}
            voters = sum(1 for k in cluster if witnesses[k][0] is not None)
            replicate = votes.pop() if len(votes) == 1 and voters >= p.min_witnesses else None
            detail = {
                "sample_ratio_median": text(middle),
                "cluster": sorted(k[1].code for k in cluster),
                "elements_checked": len(keys),
                "witnesses": voters,
            }
            for key in cluster:
                decided[key] = (replicate, detail)
        return decided


# --- cup_choice@2 (D-88 point 5, D-92) ----------------------------------------------------------------

Rung2 = Literal["instrument_precision", "spike_witness", "direction_prior"]


class InstrumentPrecision(Frozen):
    """When a cup's reading counts as unstable: the same figures `precision@1` suggests on (D-90)."""

    rsd_limit: Dec | None = Field(default=None, gt=0)  # %
    rsd_from_mdl_multiple: Dec = Field(default=Decimal(10), ge=0)
    istd_low: Dec | None = Field(default=None, gt=0)  # %
    istd_high: Dec | None = Field(default=None, gt=0)


class CupChoice2Params(CupChoiceParams):
    rungs: tuple[Rung2, ...] = ("instrument_precision", "spike_witness", "direction_prior")
    instrument_precision: InstrumentPrecision = InstrumentPrecision()


def instrument_state(cell: Cell, replicate: int, p: InstrumentPrecision) -> tuple[str, list[str]]:
    """("unstable" | "stable" | "unknown", reasons) for one cup. Unstable: its RSD above the limit
    (judged only at or above the multiple × effective MDL) or its internal standard outside the
    window. Stable: at least one of those judged, and passed. Unknown: no evidence to judge."""
    reading = cell.cups[replicate]
    reasons, judged = [], 0
    if p.rsd_limit is not None and reading.rsd is not None and reading.mdl is not None:
        if abs(cell.cup_value(replicate)) >= p.rsd_from_mdl_multiple * reading.effective_mdl.value:
            judged += 1
            if reading.rsd > p.rsd_limit:
                reasons.append(f"RSD {text(reading.rsd)} %")
    recovery = reading.istd_recovery
    if p.istd_low is not None and p.istd_high is not None and recovery is not None:
        judged += 1
        if not p.istd_low <= recovery <= p.istd_high:
            reasons.append(f"internal standard {text(recovery)} %")
    if reasons:
        return "unstable", reasons
    return ("stable" if judged else "unknown"), []


class CupChoice2(CupChoice):
    """cup_choice@1's ladder with one more rung, `instrument_precision`: when the cups disagree and one
    cup's reading was unstable on the instrument (RSD, internal standard) while the other's was
    steady, believe the steady cup. Without the evidence (a CSV), the rung is inconclusive and the
    ladder goes on as in @1. The analyst's exclusion and a scale fault still come first (D-88)."""

    version = 2
    Params = CupChoice2Params
    flag_codes = CupChoice.flag_codes | {"CUP_CHOSEN_BY_PRECISION"}

    def _ladder(self, state: BatchState, cell: Cell, params: CupChoice2Params, witness) -> None:
        tried = []
        for rung in params.rungs:
            if rung == "instrument_precision":
                states = {r: instrument_state(cell, r, params.instrument_precision) for r in (1, 2)}
                detail = {f"cup_{r}": {"state": s, "reasons": why} for r, (s, why) in states.items()}
                tried.append({"rung": rung, **detail})
                kinds = {r: s for r, (s, _) in states.items()}
                if sorted(kinds.values()) == ["stable", "unstable"]:
                    replicate = next(r for r, s in kinds.items() if s == "stable")
                    self._choose(cell, replicate, rung, "CUP_CHOSEN_BY_PRECISION")
                    self.trace(cell, rung=rung, chosen_replicate=replicate, tried=tried)
                    return
            elif rung == "spike_witness":
                replicate, detail = witness
                tried.append({"rung": rung, **detail})
                if replicate is not None:
                    self._choose(cell, replicate, rung, "CUP_CHOSEN_BY_SPIKE")
                    self.trace(cell, rung=rung, chosen_replicate=replicate, tried=tried)
                    return
            else:
                direction, row = params.direction(cell.analyte.code, state.batch.prep_method)
                tried.append({"rung": rung, "direction": direction, "row": row})
                if direction != "NEUTRAL":
                    low, high = sorted((1, 2), key=cell.cup_value)
                    replicate = low if direction == "CONTAMINATION_PRONE" else high
                    self._choose(cell, replicate, rung, "CUP_CHOSEN_BY_PRIOR")
                    self.trace(cell, rung=rung, chosen_replicate=replicate, tried=tried)
                    return
        cell.selection = Selection.NONE
        self.flag(cell, "CUP_CHOICE_UNDECIDED", "no rung could choose a cup")
        self.trace(cell, rung="no_decision", chosen_replicate=None, tried=tried)
