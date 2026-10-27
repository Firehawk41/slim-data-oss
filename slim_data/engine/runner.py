"""Runs a validated ruleset over one prep batch, and replays stored traces (D-16, D-17).

Pure: no I/O, no clock, one fixed decimal context. The same input and ruleset always produce the
same flags and the same trace, which is what `replay` checks.
"""

from collections import defaultdict

from slim_data.domain.enums import ReadingRole
from slim_data.domain.observations import Reading
from slim_data.domain.results import Flag
from slim_data.domain.values import Quantity
from slim_data.engine.model import BatchEvaluation, BatchState, Cell, CellOutcome, EvaluationInput
from slim_data.engine.numbers import engine_arithmetic
from slim_data.engine.ruleset import ValidatedRuleset


class BatchRejected(ValueError):
    """The batch can't be evaluated under this ruleset at all (e.g. over the size limit, D-07)."""


def _current(data: EvaluationInput) -> tuple[list[tuple[Reading, bool]], list[int]]:
    """Each measurement's attempt that counts (D-66): the newest one not excluded, else the newest
    (marked excluded). Returns (reading, excluded) pairs and the ids of the attempts set aside."""
    attempts = defaultdict(list)
    for reading in data.readings:
        attempts[(reading.sample, reading.analyte, reading.role, reading.replicate)].append(reading)
    chosen, superseded = [], []
    for key, readings in attempts.items():
        readings.sort(key=lambda r: r.attempt)
        numbers = [r.attempt for r in readings]
        if len(set(numbers)) != len(numbers):
            raise ValueError(
                f"two {key[2].name} readings with replicate {key[3]}, same attempt, for {key[:2]}"
            )
        good = [r for r in readings if r.id is None or r.id not in data.excluded_reading_ids]
        current = good[-1] if good else readings[-1]
        chosen.append((current, not good))
        superseded.extend(r.id for r in readings if r is not current and r.id is not None)
    return chosen, superseded


def _build_state(data: EvaluationInput) -> BatchState:
    cells: dict = {}
    blanks, qc = [], []
    excluded_by_cell = defaultdict(set)
    chosen, superseded = _current(data)
    for reading, excluded in chosen:
        if reading.role == ReadingRole.BLANK:
            if not excluded:  # an excluded blank is dropped
                blanks.append(reading)
            continue
        if reading.role.is_qc:
            if not excluded:  # as a blank: an excluded check standard or CRM is dropped
                qc.append(reading)
            continue
        key = (reading.sample, reading.analyte)
        cell = cells.setdefault(key, Cell(sample=reading.sample, analyte=reading.analyte))
        if reading.role == ReadingRole.SPIKE:
            if excluded:  # an excluded spike is dropped: as if it wasn't measured
                superseded.append(reading.id)
            else:
                cell.spikes[reading.replicate] = reading
            continue
        cell.cups[reading.replicate] = reading
        if excluded:
            excluded_by_cell[key].add(reading.replicate)
    for key, replicates in excluded_by_cell.items():
        cells[key].excluded = frozenset(replicates)
    by_id = {r.id: r for r in data.readings}
    for reading_id in sorted(superseded):
        cell = cells.get((by_id[reading_id].sample, by_id[reading_id].analyte))
        if cell is not None:
            cell.superseded.append(reading_id)
    ordered = dict(sorted(cells.items(), key=lambda item: (str(item[0][0]), item[0][1].code)))
    limits = {(entry.sample, entry.analyte): entry.limits for entry in data.limits}
    calibrations = {(c.run_id, c.analyte): c for c in data.calibrations if c.run_id is not None}
    return BatchState(
        batch=data.batch,
        cells=ordered,
        blanks=tuple(blanks),
        qc=tuple(qc),
        limits=limits,
        calibrations=calibrations,
    )


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
            trace=(({"step": "attempts", "set_aside": cell.superseded},) if cell.superseded else ())
            + tuple(cell.trace),
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
