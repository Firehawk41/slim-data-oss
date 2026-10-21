"""Workflow rules (DESIGN.md §8): pure functions that check an action and return the event to record.

Each function raises a WorkflowError subclass when the action isn't allowed, and otherwise returns
the ResultEvent for the caller to store. Nothing here touches a database or a clock.

| Verdict          | Cleared by                              | Recorded as            |
|------------------|-----------------------------------------|------------------------|
| REPORTABLE       | nothing extra                           | -                      |
| NEEDS_SUPERVISOR | supervisor accepts (or sends to rerun)  | ACCEPTED (not an exception) |
| RERUN            | rerun, or supervisor exception          | EXCEPTION (D-09)       |

Release needs status REVIEWED and a cleared verdict. Four-eyes: nobody reviews, accepts, records an
exception for or releases a result on readings they entered, with one exception (D-68): when the
policy allows it, a chemist working alone may review and release their own results as a **solo**
sign-off, with a reason, once the policy's delay has passed since the result last changed (fresh
eyes on their own work). It is labelled as solo forever.
"""

from datetime import datetime, timedelta

from slim_data.domain.actor import Actor
from slim_data.domain.enums import Action, EventKind, ResultStatus, Verdict
from slim_data.domain.policy import Policy
from slim_data.domain.results import Result, ResultEvent


class WorkflowError(Exception):
    """An action that the workflow rules don't allow."""


class InvalidTransition(WorkflowError):
    """The result isn't in a state where this action makes sense."""


class NotPermitted(WorkflowError):
    """The actor's role or identity source isn't enough under the policy."""


class FourEyesViolation(WorkflowError):
    """The actor entered the readings behind this result."""


class UnknownCategory(WorkflowError):
    """The reason category isn't one the policy lists for this action (D-72)."""


def _check_category(policy: Policy, action: Action, reason_category: str) -> None:
    allowed = policy.categories(action)
    if reason_category not in allowed:
        raise UnknownCategory(
            f"{reason_category!r} is not a reason category for {action.name} under policy {policy.name!r}"
            + (f"; choose one of: {', '.join(allowed)}" if allowed else "; it lists none")
        )


_FOUR_EYES = {Action.REVIEW, Action.ACCEPT, Action.RECORD_EXCEPTION, Action.RELEASE}
_SOLO_ALLOWED = {Action.REVIEW, Action.RELEASE}
_SIGN_OFFS = {EventKind.REVIEWED, EventKind.RELEASED}
_OPEN = {ResultStatus.DRAFT, ResultStatus.REVIEWED}


def _authorize(
    result: Result,
    actor: Actor,
    action: Action,
    policy: Policy,
    solo: bool = False,
    now: datetime | None = None,
) -> bool:
    """Raises if the action isn't allowed; returns whether it is a solo sign-off (D-68)."""
    verdict = result.current.verdict
    min_role = policy.min_role(action, verdict)
    if min_role is None:
        raise NotPermitted(f"policy {policy.name!r} allows nobody to {action.name} a {verdict.name} result")
    if not actor.has_role_at_least(min_role):
        raise NotPermitted(f"{action.name} on a {verdict.name} result needs {min_role.name} or above")
    if actor.identity_source not in policy.accepted_sources(action):
        raise NotPermitted(
            f"policy {policy.name!r} doesn't accept {actor.identity_source.name} identities for {action.name}"
        )
    if action not in _FOUR_EYES or actor.id not in result.current.analyst_ids:
        return False
    if not solo or action not in _SOLO_ALLOWED:
        raise FourEyesViolation(f"{actor.display} entered readings behind this result; someone else must")
    _check_solo(result, actor, policy, now)
    return True


def _check_solo(result: Result, actor: Actor, policy: Policy, now: datetime | None) -> None:
    if policy.solo_review_minutes is None:
        raise NotPermitted(f"policy {policy.name!r} doesn't allow solo review")
    if now is None:
        raise ValueError("a solo sign-off needs the current time (the database clock)")
    # The last change: the version itself, or a decision the actor took on it since (not a sign-off).
    changes = [result.current.created_at] + [
        e.at
        for e in result.events_for(result.current.version)
        if e.actor.id == actor.id and e.kind not in _SIGN_OFFS
    ]
    known = [at for at in changes if at is not None]
    if not known:
        raise InvalidTransition(
            "can't tell when this result last changed, so the solo delay can't be checked"
        )
    wait = timedelta(minutes=policy.solo_review_minutes) - (now - max(known))
    if wait > timedelta(0):
        left = -(-wait.total_seconds() // 60)  # whole minutes, rounded up
        raise InvalidTransition(
            f"solo review needs {policy.solo_review_minutes} min after the last change; {left:.0f} min to go"
        )


def _require_open(result: Result, action: Action) -> None:
    if result.status not in _OPEN:
        raise InvalidTransition(f"can't {action.name} a {result.status.name} result")
    if result.rerun_requested:
        raise InvalidTransition(f"a rerun was requested; can't {action.name} until a new version exists")
    if result.correction_started:
        raise InvalidTransition(f"a correction was started; can't {action.name} until the new version exists")


def _event(
    result: Result,
    kind: EventKind,
    actor: Actor,
    note: str = "",
    reason_category: str = "",
    solo: bool = False,
) -> ResultEvent:
    return ResultEvent(
        kind=kind,
        version=result.current.version,
        actor=actor,
        note=note,
        reason_category=reason_category,
        solo=solo,
    )


def review(
    result: Result,
    actor: Actor,
    policy: Policy,
    note: str = "",
    solo: bool = False,
    now: datetime | None = None,
) -> ResultEvent:
    _require_open(result, Action.REVIEW)
    if result.status != ResultStatus.DRAFT:
        raise InvalidTransition("only a draft can be reviewed")
    is_solo = _authorize(result, actor, Action.REVIEW, policy, solo, now)
    return _event(result, EventKind.REVIEWED, actor, note, solo=is_solo)


def accept(result: Result, actor: Actor, policy: Policy, reason_category: str, note: str = "") -> ResultEvent:
    _require_open(result, Action.ACCEPT)
    if result.current.verdict != Verdict.NEEDS_SUPERVISOR:
        raise InvalidTransition("only a NEEDS_SUPERVISOR result is accepted")
    if result.is_cleared:
        raise InvalidTransition("already accepted")
    _check_category(policy, Action.ACCEPT, reason_category)
    _authorize(result, actor, Action.ACCEPT, policy)
    return _event(result, EventKind.ACCEPTED, actor, note, reason_category)


def request_rerun(result: Result, actor: Actor, policy: Policy, note: str = "") -> ResultEvent:
    _require_open(result, Action.REQUEST_RERUN)
    _authorize(result, actor, Action.REQUEST_RERUN, policy)
    return _event(result, EventKind.RERUN_REQUESTED, actor, note)


def record_exception(
    result: Result, actor: Actor, policy: Policy, reason_category: str, note: str
) -> ResultEvent:
    _require_open(result, Action.RECORD_EXCEPTION)
    if result.current.verdict != Verdict.RERUN:
        raise InvalidTransition("an exception releases a RERUN result without rerunning; this isn't one")
    if result.is_cleared:
        raise InvalidTransition("an exception is already recorded")
    _check_category(policy, Action.RECORD_EXCEPTION, reason_category)
    _authorize(result, actor, Action.RECORD_EXCEPTION, policy)
    return _event(result, EventKind.EXCEPTION, actor, note, reason_category)


def release(
    result: Result,
    actor: Actor,
    policy: Policy,
    note: str = "",
    solo: bool = False,
    now: datetime | None = None,
) -> ResultEvent:
    _require_open(result, Action.RELEASE)
    if result.status != ResultStatus.REVIEWED:
        raise InvalidTransition("only a reviewed result can be released")
    if not result.is_cleared:
        verdict = result.current.verdict
        needed = "an acceptance" if verdict == Verdict.NEEDS_SUPERVISOR else "a rerun or an exception"
        raise InvalidTransition(f"a {verdict.name} result needs {needed} before release")
    is_solo = _authorize(result, actor, Action.RELEASE, policy, solo, now)
    return _event(result, EventKind.RELEASED, actor, note, solo=is_solo)


def reject(result: Result, actor: Actor, policy: Policy, note: str) -> ResultEvent:
    _require_open(result, Action.REJECT)
    _authorize(result, actor, Action.REJECT, policy)
    return _event(result, EventKind.REJECTED, actor, note)


def start_correction(result: Result, actor: Actor, policy: Policy, reason: str) -> ResultEvent:
    """Any non-rejected result can be corrected (D-20). The caller then stores a new version with
    `reason`, which goes through review and release again."""
    if result.status == ResultStatus.REJECTED:
        raise InvalidTransition("a rejected result can't be corrected")
    if result.correction_started:
        raise InvalidTransition("a correction is already under way")
    _authorize(result, actor, Action.CORRECT, policy)
    return _event(result, EventKind.CORRECTION_STARTED, actor, reason)
