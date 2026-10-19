"""Runs a validated ruleset over one prep batch, and replays stored traces (D-16, D-17).

Pure: no I/O, no clock, one fixed decimal context. The same input and ruleset always produce the
same flags and the same trace, which is what `replay` checks.
"""

from collections import defaultdict

from slim_data.domain.enums import ReadingRole
from slim_data.domain.results import Flag
from slim_data.domain.values import Quantity
from slim_data.engine.model import BatchEvaluation, BatchState, Cell, CellOutcome, EvaluationInput
from slim_data.engine.numbers import engine_arithmetic
from slim_data.engine.ruleset import ValidatedRuleset


class BatchRejected(ValueError):
    """The batch can't be evaluated under this ruleset at all (e.g. over the size limit, D-07)."""


def _build_state(data: EvaluationInput) -> BatchState:
    cells: dict = {}
    blanks = []
    excluded_by_cell = defaultdict(set)
    for reading in data.readings:
        if reading.role == ReadingRole.BLANK:
            if (
                reading.id is None or reading.id not in data.excluded_reading_ids
            ):  # an excluded blank is dropped
                blanks.append(reading)
            continue
        key = (reading.sample, reading.analyte)
        cell = cells.setdefault(key, Cell(sample=reading.sample, analyte=reading.analyte))
        target = cell.cups if reading.role == ReadingRole.CUP else cell.spikes
        if reading.replicate in target:
            raise ValueError(f"two {reading.role.name} readings with replicate {reading.replicate} for {key}")
        target[reading.replicate] = reading
        if reading.id is not None and reading.id in data.excluded_reading_ids:
            if reading.role != ReadingRole.CUP:
                raise ValueError("only cup readings can be excluded")
            excluded_by_cell[key].add(reading.replicate)
    for key, replicates in excluded_by_cell.items():
        cells[key].excluded = frozenset(replicates)
    ordered = dict(sorted(cells.items(), key=lambda item: (str(item[0][0]), item[0][1].code)))
    limits = {(entry.sample, entry.analyte): entry.limits for entry in data.limits}
    return BatchState(batch=data.batch, cells=ordered, blanks=tuple(blanks), limits=limits)


def evaluate(data: EvaluationInput, ruleset: ValidatedRuleset) -> BatchEvaluation:
    if len(data.batch.samples) > ruleset.ruleset.max_batch_size:
        raise BatchRejected(
            f"batch {data.batch.code} has {len(data.batch.samples)} samples; "
            f"ruleset {ruleset.ruleset.name!r} allows {ruleset.ruleset.max_batch_size}"
        )
    with engine_arithmetic():
        state = _build_state(data)
        for resolved in ruleset.steps:
            resolved.step.run(state, resolved.params)
    outcomes = tuple(
        CellOutcome(
            sample=cell.sample,
            analyte=cell.analyte,
            selection=cell.selection,
            chosen_replicate=cell.chosen_replicate,
            raw=cell.value(),
            reported=None if cell.reported is None else Quantity(value=cell.reported, unit=cell.unit),
            qualifier=cell.qualifier,
            uncertainty=cell.uncertainty,
            recovery=cell.recovery,
            limits=cell.limits,
            flags=tuple(
                Flag(
                    code=f.code, severity=ruleset.ruleset.severity_of(f.code), step=f.step, message=f.message
                )
                for f in cell.flags
            ),
            trace=tuple(cell.trace),
        )
        for cell in state.cells.values()
    )
    return BatchEvaluation(ruleset_hash=ruleset.content_hash, cells=outcomes)


def replay(data: EvaluationInput, ruleset: ValidatedRuleset, stored: BatchEvaluation) -> list[str]:
    """Re-evaluate and compare with what was stored. Returns the differences; empty means verified."""
    differences = []
    if stored.ruleset_hash != ruleset.content_hash:
        differences.append("ruleset content hash differs from the one the results were made with")
    fresh = {(c.sample, c.analyte): c for c in evaluate(data, ruleset).cells}
    old = {(c.sample, c.analyte): c for c in stored.cells}
    for key in sorted(set(fresh) | set(old), key=lambda k: (str(k[0]), k[1].code)):
        if key not in fresh or key not in old:
            differences.append(f"{key[0]} {key[1].code}: present in only one evaluation")
        elif fresh[key] != old[key]:
            differences.append(f"{key[0]} {key[1].code}: trace or flags differ")
    return differences
