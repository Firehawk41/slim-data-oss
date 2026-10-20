"""Workflow rules (D-57, D-09, D-20): who may do what, when, with four-eyes throughout."""

import pytest

from slim_data.domain import workflow
from slim_data.domain.enums import EventKind, IdentitySource, ResultStatus, Role, Severity
from slim_data.domain.policy import DEMO_POLICY, PRODUCTION_EXAMPLE_POLICY
from slim_data.domain.results import Result
from slim_data.domain.workflow import FourEyesViolation, InvalidTransition, NotPermitted
from tests.factories import ANA, ROB, SUE, actor, flag, result, version

P = DEMO_POLICY
NEEDS = version(1, flag(Severity.NEEDS_SUPERVISOR))
RERUN = version(1, flag(Severity.RERUN))


def apply(r: Result, *events) -> Result:
    return r.model_copy(update={"events": r.events + events})


def reviewed(r: Result | None = None) -> Result:
    r = r or result()
    return apply(r, workflow.review(r, ROB, P))


# --- the happy paths -------------------------------------------------------------------------------


def test_reportable_review_then_release():
    r = reviewed()
    assert r.status == ResultStatus.REVIEWED
    r = apply(r, workflow.release(r, ROB, P, note="batch PB-0001"))
    assert r.status == ResultStatus.RELEASED
    assert r.events[-1].note == "batch PB-0001"


def test_needs_supervisor_is_accepted_then_released_without_an_exception():
    r = reviewed(result(NEEDS))
    with pytest.raises(InvalidTransition, match="needs an acceptance"):
        workflow.release(r, ROB, P)
    r = apply(r, workflow.accept(r, SUE, P, reason_category="spike not evaluable"))
    r = apply(r, workflow.release(r, ROB, P))
    assert r.status == ResultStatus.RELEASED
    assert not r.released_with_exception


def test_rerun_released_only_through_a_supervisor_exception():
    r = reviewed(result(RERUN))
    with pytest.raises(InvalidTransition, match="needs a rerun or an exception"):
        workflow.release(r, ROB, P)
    r = apply(r, workflow.record_exception(r, SUE, P, "insufficient sample", "customer accepts qualifier"))
    r = apply(r, workflow.release(r, SUE, P))
    assert r.status == ResultStatus.RELEASED
    assert r.released_with_exception


def test_correction_after_release_starts_a_new_cycle():
    r = apply(reviewed(), workflow.release(reviewed(), ROB, P))
    r = apply(r, workflow.start_correction(r, ANA, P, reason="dilution was 100, not 10"))
    with pytest.raises(InvalidTransition, match="already under way"):
        workflow.start_correction(r, ANA, P, reason="again")
    r = r.model_copy(update={"versions": r.versions + (version(2),)})
    assert r.status == ResultStatus.DRAFT and r.is_amended
    r = apply(r, workflow.review(r, ROB, P))
    r = apply(r, workflow.release(r, ROB, P))
    assert r.status == ResultStatus.RELEASED


def test_a_started_correction_blocks_sign_offs_until_the_new_version():
    r = apply(reviewed(), workflow.start_correction(reviewed(), ANA, P, reason="wrong dilution"))
    with pytest.raises(InvalidTransition, match="correction was started"):
        workflow.release(r, ROB, P)


# --- four-eyes -------------------------------------------------------------------------------------


@pytest.mark.parametrize("call", ["review", "accept", "record_exception", "release"])
def test_nobody_signs_off_their_own_readings(call):
    supervisor_analyst = actor("ana", Role.SUPERVISOR)
    r = {
        "review": result(),
        "accept": result(NEEDS),
        "record_exception": result(RERUN),
        "release": reviewed(),
    }[call]
    args = {"accept": ("matrix",), "record_exception": ("no sample", "agreed")}.get(call, ())
    with pytest.raises(FourEyesViolation):
        getattr(workflow, call)(r, supervisor_analyst, P, *args)


def test_analyst_may_correct_their_own_result():
    released = apply(reviewed(), workflow.release(reviewed(), ROB, P))
    assert workflow.start_correction(released, ANA, P, reason="typo").kind == EventKind.CORRECTION_STARTED


# --- roles and identity sources --------------------------------------------------------------------


@pytest.mark.parametrize(
    "call, r, who, args",
    [
        ("review", result(), actor("al", Role.ANALYST), ()),
        ("accept", result(NEEDS), ROB, ("matrix",)),
        ("record_exception", result(RERUN), ROB, ("no sample", "agreed")),
        ("request_rerun", result(NEEDS), ROB, ()),
        ("reject", result(), actor("al", Role.ANALYST), ("bad prep",)),
    ],
)
def test_roles_are_tiered(call, r, who, args):
    with pytest.raises(NotPermitted, match="needs"):
        getattr(workflow, call)(r, who, P, *args)


def test_a_higher_role_can_do_what_a_lower_one_can():
    assert workflow.review(result(), SUE, P).kind == EventKind.REVIEWED


def test_production_policy_refuses_personas_for_sign_offs():
    persona = actor("rob-demo", Role.REVIEWER, source=IdentitySource.ASSERTED)
    assert workflow.review(result(), persona, P).actor == persona
    with pytest.raises(NotPermitted, match="ASSERTED"):
        workflow.review(result(), persona, PRODUCTION_EXAMPLE_POLICY)


def test_actions_a_policy_does_not_grant_are_denied():
    from slim_data.domain.policy import Policy

    with pytest.raises(NotPermitted, match="allows nobody"):
        workflow.review(result(), SUE, Policy(name="locked", role_rules=(), identity_rules=()))


# --- state rules -----------------------------------------------------------------------------------


def test_state_rules():
    released = apply(reviewed(), workflow.release(reviewed(), ROB, P))
    with pytest.raises(InvalidTransition, match="RELEASED"):
        workflow.review(released, ROB, P)
    with pytest.raises(InvalidTransition, match="only a draft"):
        workflow.review(reviewed(), SUE, P)
    with pytest.raises(InvalidTransition, match="only a reviewed"):
        workflow.release(result(), ROB, P)
    with pytest.raises(InvalidTransition, match="only a NEEDS_SUPERVISOR"):
        workflow.accept(result(), SUE, P, "matrix")
    with pytest.raises(InvalidTransition, match="isn't one"):
        workflow.record_exception(result(NEEDS), SUE, P, "x", "y")


def test_clearing_twice_is_refused():
    accepted = apply(result(NEEDS), workflow.accept(result(NEEDS), SUE, P, "matrix"))
    with pytest.raises(InvalidTransition, match="already accepted"):
        workflow.accept(accepted, SUE, P, "matrix")
    excepted = apply(result(RERUN), workflow.record_exception(result(RERUN), SUE, P, "x", "y"))
    with pytest.raises(InvalidTransition, match="already recorded"):
        workflow.record_exception(excepted, SUE, P, "x", "y")


def test_a_rerun_request_blocks_sign_offs_until_a_new_version():
    r = apply(result(NEEDS), workflow.request_rerun(result(NEEDS), SUE, P, note="re-prep"))
    assert r.rerun_requested
    with pytest.raises(InvalidTransition, match="rerun was requested"):
        workflow.accept(r, SUE, P, "matrix")
    fresh = r.model_copy(update={"versions": r.versions + (version(2),)})
    assert not fresh.rerun_requested
    assert workflow.review(fresh, ROB, P).version == 2


def test_rejection_is_final():
    r = apply(result(), workflow.reject(result(), ROB, P, note="sample mix-up"))
    assert r.status == ResultStatus.REJECTED
    with pytest.raises(InvalidTransition, match="rejected"):
        workflow.start_correction(r, ANA, P, reason="try again")
