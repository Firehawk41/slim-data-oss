"""Workflow rules (DESIGN.md §8): pure functions that check an action and return the event to record.

Each function raises a WorkflowError subclass when the action isn't allowed, and otherwise returns
the ResultEvent for the caller to store. Nothing here touches a database or a clock.

| Verdict          | Cleared by                              | Recorded as            |
|------------------|-----------------------------------------|------------------------|
| REPORTABLE       | nothing extra                           | -                      |
| NEEDS_SUPERVISOR | supervisor accepts (or sends to rerun)  | ACCEPTED (not an exception) |
| RERUN            | rerun, or supervisor exception          | EXCEPTION (D-09)       |

Release needs status REVIEWED and a cleared verdict. Four-eyes: nobody reviews, accepts, records an
exception for or releases a result on readings they entered.
"""

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


_FOUR_EYES = {Action.REVIEW, Action.ACCEPT, Action.RECORD_EXCEPTION, Action.RELEASE}
_OPEN = {ResultStatus.DRAFT, ResultStatus.REVIEWED}


def _authorize(result: Result, actor: Actor, action: Action, policy: Policy) -> None:
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
    if action in _FOUR_EYES and actor.id in result.current.analyst_ids:
        raise FourEyesViolation(f"{actor.display} entered readings behind this result; someone else must")


def _require_open(result: Result, action: Action) -> None:
    if result.status not in _OPEN:
        raise InvalidTransition(f"can't {action.name} a {result.status.name} result")
    if result.rerun_requested:
        raise InvalidTransition(f"a rerun was requested; can't {action.name} until a new version exists")
    if result.correction_started:
        raise InvalidTransition(f"a correction was started; can't {action.name} until the new version exists")


def _event(
    result: Result, kind: EventKind, actor: Actor, note: str = "", reason_category: str = ""
) -> ResultEvent:
    return ResultEvent(
        kind=kind,
        version=result.current.version,
        actor=actor,
        note=note,
        reason_category=reason_category,
    )


def review(result: Result, actor: Actor, policy: Policy, note: str = "") -> ResultEvent:
    _require_open(result, Action.REVIEW)
    if result.status != ResultStatus.DRAFT:
        raise InvalidTransition("only a draft can be reviewed")
    _authorize(result, actor, Action.REVIEW, policy)
    return _event(result, EventKind.REVIEWED, actor, note)


def accept(result: Result, actor: Actor, policy: Policy, reason_category: str, note: str = "") -> ResultEvent:
    _require_open(result, Action.ACCEPT)
    if result.current.verdict != Verdict.NEEDS_SUPERVISOR:
        raise InvalidTransition("only a NEEDS_SUPERVISOR result is accepted")
    if result.is_cleared:
        raise InvalidTransition("already accepted")
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
    _authorize(result, actor, Action.RECORD_EXCEPTION, policy)
    return _event(result, EventKind.EXCEPTION, actor, note, reason_category)


def release(result: Result, actor: Actor, policy: Policy, note: str = "") -> ResultEvent:
    _require_open(result, Action.RELEASE)
    if result.status != ResultStatus.REVIEWED:
        raise InvalidTransition("only a reviewed result can be released")
    if not result.is_cleared:
        verdict = result.current.verdict
        needed = "an acceptance" if verdict == Verdict.NEEDS_SUPERVISOR else "a rerun or an exception"
        raise InvalidTransition(f"a {verdict.name} result needs {needed} before release")
    _authorize(result, actor, Action.RELEASE, policy)
    return _event(result, EventKind.RELEASED, actor, note)


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
