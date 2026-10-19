"""Builders for engine tests: a batch, readings for one cell, and the demo ruleset."""

from decimal import Decimal
from itertools import count

from slim_data.domain.enums import ReadingRole
from slim_data.domain.observations import PrepBatch, Reading, SpikeAddition
from slim_data.engine.model import EvaluationInput
from slim_data.engine.registry import default_registry
from slim_data.engine.ruleset import Ruleset, validate_ruleset
from slim_data.rulesets import ruleset_text
from tests.factories import analyte, qty, sample

_ids = count(1)
DEMO = Ruleset.model_validate_json(ruleset_text("demo_metals"))


def demo(**changes):
    """The demo ruleset, validated; `changes` replace whole step params by step name."""
    ruleset = DEMO
    if changes:
        steps = tuple(
            c.model_copy(update={"params": changes[c.step.split("@")[0]]})
            if c.step.split("@")[0] in changes
            else c
            for c in ruleset.steps
        )
        ruleset = ruleset.model_copy(update={"steps": steps})
    return validate_ruleset(ruleset, default_registry())


def reading(pos, code, role, replicate, value, mdl="0.05", dilution="1", unit="ppb"):
    return Reading(
        id=next(_ids),
        sample=sample(pos),
        analyte=analyte(code),
        role=role,
        replicate=replicate,
        value=qty(value, unit),
        mdl=qty(mdl, unit) if mdl is not None else None,
        dilution=Decimal(dilution),
    )


def cups(pos, code, v1, v2, **kw):
    return [
        reading(pos, code, ReadingRole.CUP, 1, v1, **kw),
        reading(pos, code, ReadingRole.CUP, 2, v2, **kw),
    ]


def spikes(pos, code, *values, **kw):
    return [reading(pos, code, ReadingRole.SPIKE, i, v, **kw) for i, v in enumerate(values, start=1)]


def batch(positions=(1,), spike_amounts=(), prep_method="microwave HNO3"):
    return PrepBatch(
        code="PB-0001",
        analysis_id=1,
        prep_method=prep_method,
        samples=tuple(sample(p) for p in positions),
        spikes=tuple(
            SpikeAddition(sample=sample(p), analyte=analyte(c), added=qty(a)) for p, c, a in spike_amounts
        ),
    )


def evaluation_input(readings, positions=(1,), spike_amounts=(), excluded=(), prep_method="microwave HNO3"):
    return EvaluationInput(
        batch=batch(positions, spike_amounts, prep_method),
        readings=tuple(readings),
        excluded_reading_ids=frozenset(excluded),
    )


def codes(outcome):
    return [f.code for f in outcome.flags]
