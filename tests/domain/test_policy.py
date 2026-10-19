"""Policies are data: tiered roles and accepted identity sources (D-10, D-50, D-57)."""

import pytest
from pydantic import ValidationError

from slim_data.domain.enums import Action, IdentitySource, Role, Verdict
from slim_data.domain.policy import DEMO_POLICY, PRODUCTION_EXAMPLE_POLICY, IdentityRule, Policy, RoleRule


def test_verdict_specific_rule_wins_over_general():
    assert DEMO_POLICY.min_role(Action.REQUEST_RERUN, Verdict.REPORTABLE) == Role.REVIEWER
    assert DEMO_POLICY.min_role(Action.REQUEST_RERUN, Verdict.NEEDS_SUPERVISOR) == Role.SUPERVISOR


def test_anything_not_granted_is_denied():
    assert DEMO_POLICY.min_role(Action.ACCEPT, Verdict.REPORTABLE) is None
    assert DEMO_POLICY.min_role(Action.RECORD_EXCEPTION, Verdict.NEEDS_SUPERVISOR) is None
    empty = Policy(name="empty", role_rules=(), identity_rules=())
    assert empty.accepted_sources(Action.RELEASE) == frozenset()


def test_demo_accepts_personas_and_production_requires_logins_for_sign_offs():
    assert IdentitySource.ASSERTED in DEMO_POLICY.accepted_sources(Action.RELEASE)
    assert PRODUCTION_EXAMPLE_POLICY.accepted_sources(Action.RELEASE) == {IdentitySource.AUTHENTICATED}
    assert IdentitySource.ASSERTED in PRODUCTION_EXAMPLE_POLICY.accepted_sources(Action.CORRECT)


def test_a_flat_lab_is_just_different_data():
    flat = Policy(
        name="flat",
        role_rules=tuple(RoleRule(action=a, min_role=Role.ANALYST) for a in Action),
        identity_rules=(IdentityRule(action=Action.RELEASE, sources=frozenset(IdentitySource)),),
    )
    assert flat.min_role(Action.RELEASE, Verdict.RERUN) == Role.ANALYST


def test_duplicate_rules_are_refused():
    with pytest.raises(ValidationError, match="two role rules"):
        Policy(name="dup", role_rules=(RoleRule(action=Action.REVIEW, min_role=Role.REVIEWER),) * 2,
               identity_rules=())  # fmt: skip
    rule = IdentityRule(action=Action.REVIEW, sources=frozenset(IdentitySource))
    with pytest.raises(ValidationError, match="two identity rules"):
        Policy(name="dup", role_rules=(), identity_rules=(rule, rule))
