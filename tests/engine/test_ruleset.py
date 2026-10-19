"""Rulesets are validated against the registry before they can run (D-13, D-14, D-30)."""

import pytest
from pydantic import ValidationError

from slim_data.domain.enums import Severity
from slim_data.engine.registry import default_registry
from slim_data.engine.ruleset import (
    Ruleset,
    RulesetError,
    SeverityRule,
    StepConfig,
    content_hash,
    validate_ruleset,
)
from tests.engine.helpers import DEMO, demo


def without(ruleset, code):
    return ruleset.model_copy(update={"severities": tuple(r for r in ruleset.severities if r.code != code)})


def test_demo_ruleset_is_valid_and_has_no_cliff():
    validated = demo()
    assert [r.step.key() for r in validated.steps] == [
        "cup_shape@1",
        "linear_range@1",
        "agreement@1",
        "cup_choice@1",
        "recovery@1",
        "recovery_pooling@1",
        "reported_value@1",
        "uncertainty@1",
        "method_blank@1",
        "spec_limits@1",
    ]
    assert validated.warnings == ()
    assert DEMO.severity_of("ABOVE_LINEAR_RANGE") == Severity.RERUN


def test_cliff_is_a_warning_that_publishing_must_answer():
    validated = demo(agreement={"switch_multiple": "5", "rpd_limit": "0.20", "absolute_multiple": "2"})
    assert len(validated.warnings) == 1 and "jumps at the switch point" in validated.warnings[0]


def test_content_hash_is_stable_and_sensitive():
    assert content_hash(DEMO) == content_hash(Ruleset.model_validate_json(DEMO.model_dump_json()))
    assert content_hash(DEMO) != content_hash(DEMO.model_copy(update={"max_batch_size": 21}))


@pytest.mark.parametrize(
    "ruleset, message",
    [
        (without(DEMO, "CUPS_DISAGREE"), "no severity for flag CUPS_DISAGREE"),
        (DEMO.model_copy(update={"steps": DEMO.steps[2:]}), "needs cup_shape to run before it"),
        (DEMO.model_copy(update={"steps": (StepConfig(step="cup_shape@9"),)}), "unknown step"),
        (
            DEMO.model_copy(
                update={
                    "steps": (
                        StepConfig(step="cup_shape@1"),
                        StepConfig(step="agreement@1", params={"rpd_limit": "-1"}),
                    )
                }
            ),
            "bad params for agreement@1",
        ),  # fmt: skip
    ],
)
def test_broken_rulesets_are_refused(ruleset, message):
    with pytest.raises(RulesetError, match=message):
        validate_ruleset(ruleset, default_registry())


def test_ruleset_shape_rules():
    with pytest.raises(ValidationError, match="appears twice"):
        DEMO.model_validate(
            {**DEMO.model_dump(), "steps": [{"step": "cup_shape@1"}, {"step": "cup_shape@1"}]}
        )
    rule = SeverityRule(code="X", severity="WARN")
    assert rule.severity == Severity.WARN
    with pytest.raises(ValidationError, match="two severities"):
        DEMO.model_validate({**DEMO.model_dump(), "severities": [rule.model_dump()] * 2})
    with pytest.raises(ValidationError):
        StepConfig(step="Agreement")
