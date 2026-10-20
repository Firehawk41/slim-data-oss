"""Who may do what (D-10, D-50, D-57): data, not code.

A policy answers two questions for an action on a result with a given verdict:
  * which role is the minimum (roles are tiered: a higher role can do what a lower one can);
  * which identity sources are accepted (a production lab would refuse ASSERTED for releases).
Anything a policy doesn't grant is denied. Lab-real versus demo is a different policy, never a
code branch (D-05).
"""

from typing import Self

from pydantic import Field, model_validator

from slim_data.domain.base import Frozen
from slim_data.domain.enums import Action, IdentitySource, Role, Verdict


class RoleRule(Frozen):
    action: Action
    verdict: Verdict | None = None  # None = any verdict; a verdict-specific rule wins
    min_role: Role


class IdentityRule(Frozen):
    action: Action
    sources: frozenset[IdentitySource] = Field(min_length=1)


class Policy(Frozen):
    name: str = Field(min_length=1, max_length=60)
    role_rules: tuple[RoleRule, ...]
    identity_rules: tuple[IdentityRule, ...]
    # Minutes a chemist working alone must wait before a solo review or release of their own
    # results (D-68); None = solo sign-offs not allowed: strict four-eyes.
    solo_review_minutes: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _no_duplicates(self) -> Self:
        role_keys = [(r.action, r.verdict) for r in self.role_rules]
        if len(set(role_keys)) != len(role_keys):
            raise ValueError("two role rules for the same action and verdict")
        identity_keys = [r.action for r in self.identity_rules]
        if len(set(identity_keys)) != len(identity_keys):
            raise ValueError("two identity rules for the same action")
        return self

    def min_role(self, action: Action, verdict: Verdict) -> Role | None:
        """The minimum role for `action` on a result with `verdict`; None means nobody may."""
        general = None
        for rule in self.role_rules:
            if rule.action == action and rule.verdict == verdict:
                return rule.min_role
            if rule.action == action and rule.verdict is None:
                general = rule.min_role
        return general

    def accepted_sources(self, action: Action) -> frozenset[IdentitySource]:
        for rule in self.identity_rules:
            if rule.action == action:
                return rule.sources
        return frozenset()


_ANY_SOURCE = frozenset(IdentitySource)
_AUTHENTICATED = frozenset({IdentitySource.AUTHENTICATED})
_SIGN_OFF_ACTIONS = (Action.REVIEW, Action.ACCEPT, Action.RECORD_EXCEPTION, Action.RELEASE)

# The tiered roles of D-57.
_TIERED_ROLES = (
    RoleRule(action=Action.REVIEW, min_role=Role.REVIEWER),
    RoleRule(action=Action.ACCEPT, verdict=Verdict.NEEDS_SUPERVISOR, min_role=Role.SUPERVISOR),
    RoleRule(action=Action.REQUEST_RERUN, min_role=Role.REVIEWER),
    RoleRule(action=Action.REQUEST_RERUN, verdict=Verdict.NEEDS_SUPERVISOR, min_role=Role.SUPERVISOR),
    RoleRule(action=Action.RECORD_EXCEPTION, verdict=Verdict.RERUN, min_role=Role.SUPERVISOR),
    RoleRule(action=Action.RELEASE, min_role=Role.REVIEWER),
    RoleRule(action=Action.REJECT, min_role=Role.REVIEWER),
    RoleRule(action=Action.CORRECT, min_role=Role.ANALYST),
)

DEMO_POLICY = Policy(
    name="demo",
    role_rules=_TIERED_ROLES,
    identity_rules=tuple(IdentityRule(action=action, sources=_ANY_SOURCE) for action in Action),
    solo_review_minutes=15,
)
"""The public demo: tiered roles; demo personas (ASSERTED) may do everything their role allows."""

PRODUCTION_EXAMPLE_POLICY = Policy(
    name="production-example",
    role_rules=_TIERED_ROLES,
    identity_rules=tuple(
        IdentityRule(action=action, sources=_AUTHENTICATED if action in _SIGN_OFF_ACTIONS else _ANY_SOURCE)
        for action in Action
    ),
)
"""What a real lab would run: sign-offs (review, accept, exception, release) need a real login."""
