"""Rulesets are data (D-13, D-14): which steps run, in what order, with which parameters, and how
severe each flag is (D-56).

A ruleset is authored as a file and later published into the database as a numbered, content-hashed,
immutable version (M3). Here it is validated against the step registry, so a ruleset that names a
missing step, misses a severity or runs a step before what it needs is refused before it can run.
"""

import hashlib
import json
from typing import Annotated, Any, Self

from pydantic import BaseModel, BeforeValidator, Field, ValidationError, model_validator

from slim_data.domain.base import Frozen
from slim_data.domain.enums import Severity


class RulesetError(ValueError):
    """The ruleset can't run as written."""


class StepConfig(Frozen):
    step: str = Field(pattern=r"^[a-z_]+@[1-9][0-9]*$")  # e.g. "agreement@1"
    params: dict[str, Any] = Field(default_factory=dict)


def _severity_by_name(value: Any) -> Any:
    # Ruleset files say "RERUN", not 4: they're written and reviewed by people.
    return Severity[value] if isinstance(value, str) else value


class SeverityRule(Frozen):
    code: str = Field(min_length=1, max_length=60)
    severity: Annotated[Severity, BeforeValidator(_severity_by_name)]


class Ruleset(Frozen):
    name: str = Field(min_length=1, max_length=100)
    analysis_id: int = Field(ge=1)
    prep_method: str | None = None  # None = the analysis-wide default (D-15)
    max_batch_size: int = Field(ge=1)  # D-07
    steps: tuple[StepConfig, ...] = Field(min_length=1)
    severities: tuple[SeverityRule, ...]

    @model_validator(mode="after")
    def _unique(self) -> Self:
        names = [config.step.split("@")[0] for config in self.steps]
        if len(set(names)) != len(names):
            raise ValueError("a step appears twice")
        codes = [rule.code for rule in self.severities]
        if len(set(codes)) != len(codes):
            raise ValueError("a flag code has two severities")
        return self

    def severity_of(self, code: str) -> Severity:
        for rule in self.severities:
            if rule.code == code:
                return rule.severity
        raise RulesetError(f"no severity for flag {code}")


def content_hash(ruleset: Ruleset) -> str:
    """sha256 of the canonical JSON: the identity a published ruleset is stored and verified by."""
    canonical = json.dumps(ruleset.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class ResolvedStep(Frozen):
    model_config = Frozen.model_config | {"arbitrary_types_allowed": True}

    step: Any  # a Step instance
    params: BaseModel


class ValidatedRuleset(Frozen):
    """A ruleset whose steps resolved and whose params parsed; `warnings` need a reason to publish."""

    model_config = Frozen.model_config | {"arbitrary_types_allowed": True}

    ruleset: Ruleset
    content_hash: str
    steps: tuple[ResolvedStep, ...]
    warnings: tuple[str, ...]


def validate_ruleset(ruleset: Ruleset, registry) -> ValidatedRuleset:
    resolved = []
    seen: set[str] = set()
    for config in ruleset.steps:
        step = registry.get(config.step)
        missing = [name for name in step.requires if name not in seen]
        if missing:
            raise RulesetError(f"{config.step} needs {', '.join(missing)} to run before it")
        try:
            params = step.Params.model_validate(config.params)
        except ValidationError as error:
            raise RulesetError(f"bad params for {config.step}: {error}") from error
        for code in sorted(step.flag_codes):
            severity = ruleset.severity_of(code)
            if code in step.info_only and severity != Severity.INFO:
                raise RulesetError(f"{code} from {config.step} must be INFO: it never affects the verdict")
        resolved.append(ResolvedStep(step=step, params=params))
        seen.add(step.name)
    warnings = tuple(w for r in resolved for w in r.step.warnings(r.params))
    return ValidatedRuleset(
        ruleset=ruleset, content_hash=content_hash(ruleset), steps=tuple(resolved), warnings=warnings
    )
