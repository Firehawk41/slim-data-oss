"""The demo rulesets other than metals (D-78): each validates, and the assay passes only on RSD < 1 %."""

from decimal import Decimal

import pytest

from slim_data.domain.enums import Verdict
from slim_data.engine.registry import default_registry
from slim_data.engine.ruleset import Ruleset, validate_ruleset
from slim_data.engine.runner import evaluate
from slim_data.rulesets import ruleset_text
from tests.engine.helpers import cups, evaluation_input


def rules(name):
    return validate_ruleset(Ruleset.model_validate_json(ruleset_text(name)), default_registry())


@pytest.mark.parametrize("name", ["demo_metals", "demo_anions", "demo_assay"])
def test_every_demo_ruleset_validates_without_warnings(name):
    assert rules(name).warnings == ()


def rsd(a: str, b: str) -> Decimal:
    """Sample RSD of two values: |a − b| / (√2 · mean)."""
    x, y = Decimal(a), Decimal(b)
    return abs(x - y) / (Decimal(2).sqrt() * (x + y) / 2)


@pytest.mark.parametrize(
    ("first", "second", "verdict"),
    [
        ("69.52", "69.95", Verdict.REPORTABLE),  # RSD 0.44 %
        ("69.00", "69.97", Verdict.REPORTABLE),  # RSD 0.99 %
        ("69.00", "70.00", Verdict.RERUN),  # RSD 1.02 %
        ("68.00", "71.00", Verdict.RERUN),  # RSD 3.0 %
    ],
)
def test_the_assay_passes_only_when_the_two_titrations_rsd_is_under_1_percent(first, second, verdict):
    data = evaluation_input(cups(1, "Assay", first, second, mdl=None, unit="%"))
    cell = evaluate(data, rules("demo_assay")).cells[0]
    assert (rsd(first, second) < Decimal("0.01")) == (verdict == Verdict.REPORTABLE)
    assert cell.verdict == verdict
    assert {f.code for f in cell.flags} >= {"MDL_MISSING"}  # a titration has no MDL: INFO only
