"""Workflow rules (D-57, D-09, D-20): who may do what, when, with four-eyes throughout."""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from slim_data.domain import workflow
from slim_data.domain.enums import EventKind, IdentitySource, ResultStatus, Role, Severity
from slim_data.domain.policy import DEMO_POLICY, PRODUCTION_EXAMPLE_POLICY
from slim_data.domain.results import Result, ResultEvent
from slim_data.domain.workflow import FourEyesViolation, InvalidTransition, NotPermitted, UnknownCategory
from tests.factories import ANA, NOW, ROB, SUE, actor, flag, result, version

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
    r = apply(r, workflow.accept(r, SUE, P, reason_category="MDL taken from the method validation"))
    r = apply(r, workflow.release(r, ROB, P))
    assert r.status == ResultStatus.RELEASED
    assert not r.released_with_exception


def test_rerun_released_only_through_a_supervisor_exception():
    r = reviewed(result(RERUN))
    with pytest.raises(InvalidTransition, match="needs a rerun or an exception"):
        workflow.release(r, ROB, P)
    r = apply(
        r,
        workflow.record_exception(r, SUE, P, "Insufficient sample for a rerun", "customer accepts qualifier"),
    )
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
    args = {
        "accept": ("MDL taken from the method validation",),
        "record_exception": ("Insufficient sample for a rerun", "agreed"),
    }.get(call, ())
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
        ("accept", result(NEEDS), ROB, ("MDL taken from the method validation",)),
        ("record_exception", result(RERUN), ROB, ("Insufficient sample for a rerun", "agreed")),
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
        workflow.accept(result(), SUE, P, "MDL taken from the method validation")
    with pytest.raises(InvalidTransition, match="isn't one"):
        workflow.record_exception(result(NEEDS), SUE, P, "Released without rerun", "y")


def test_clearing_twice_is_refused():
    accepted = apply(
        result(NEEDS), workflow.accept(result(NEEDS), SUE, P, "MDL taken from the method validation")
    )
    with pytest.raises(InvalidTransition, match="already accepted"):
        workflow.accept(accepted, SUE, P, "MDL taken from the method validation")
    excepted = apply(
        result(RERUN), workflow.record_exception(result(RERUN), SUE, P, "Released without rerun", "y")
    )
    with pytest.raises(InvalidTransition, match="already recorded"):
        workflow.record_exception(excepted, SUE, P, "Released without rerun", "y")


def test_categories_come_from_the_policy():
    with pytest.raises(UnknownCategory, match="choose one of: MDL taken"):
        workflow.accept(result(NEEDS), SUE, P, "matrix, probably")
    with pytest.raises(UnknownCategory, match="Released without rerun"):
        workflow.record_exception(result(RERUN), SUE, P, "MDL taken from the method validation", "y")
    none_listed = P.model_copy(update={"reason_categories": ()})
    with pytest.raises(UnknownCategory, match="lists none"):
        workflow.accept(result(NEEDS), SUE, none_listed, "MDL taken from the method validation")


def test_a_rerun_request_blocks_sign_offs_until_a_new_version():
    r = apply(result(NEEDS), workflow.request_rerun(result(NEEDS), SUE, P, note="re-prep"))
    assert r.rerun_requested
    with pytest.raises(InvalidTransition, match="rerun was requested"):
        workflow.accept(r, SUE, P, "MDL taken from the method validation")
    fresh = r.model_copy(update={"versions": r.versions + (version(2),)})
    assert not fresh.rerun_requested
    assert workflow.review(fresh, ROB, P).version == 2


def test_rejection_is_final():
    r = apply(result(), workflow.reject(result(), ROB, P, note="sample mix-up"))
    assert r.status == ResultStatus.REJECTED
    with pytest.raises(InvalidTransition, match="rejected"):
        workflow.start_correction(r, ANA, P, reason="try again")


# --- solo sign-off: one chemist on site (D-68) ----------------------------------------------------------

ROBS_OWN = result(version(1, analysts=("rob",), created_at=NOW))
WHY = "only chemist on site"


def test_a_solo_review_and_release_after_the_delay_are_labelled():
    later = NOW + timedelta(minutes=15)
    event = workflow.review(ROBS_OWN, ROB, P, WHY, solo=True, now=later)
    assert event.solo and event.kind == EventKind.REVIEWED
    released = workflow.release(apply(ROBS_OWN, event), ROB, P, WHY, solo=True, now=later)
    assert released.solo  # no second wait: the review was the fresh look


def test_solo_waits_for_the_delay_after_the_last_change():
    with pytest.raises(InvalidTransition, match="15 min after the last change; 5 min to go"):
        workflow.review(ROBS_OWN, ROB, P, WHY, solo=True, now=NOW + timedelta(minutes=10, seconds=1))
    # A decision of his own on the result restarts the clock.
    own_call = ResultEvent(
        kind=EventKind.RERUN_REQUESTED, version=1, actor=ROB, at=NOW + timedelta(minutes=20)
    )
    with pytest.raises(InvalidTransition, match="min to go"):
        workflow._check_solo(apply(ROBS_OWN, own_call), ROB, P, NOW + timedelta(minutes=25))


def test_solo_needs_the_policy_a_reason_and_a_clock():
    with pytest.raises(NotPermitted, match="doesn't allow solo review"):
        workflow.review(ROBS_OWN, ROB, PRODUCTION_EXAMPLE_POLICY, WHY, solo=True, now=NOW)
    with pytest.raises(ValidationError, match="needs a reason"):
        workflow.review(ROBS_OWN, ROB, P, "", solo=True, now=NOW + timedelta(hours=1))
    with pytest.raises(ValueError, match="current time"):
        workflow.review(ROBS_OWN, ROB, P, WHY, solo=True)
    undated = result(version(1, analysts=("rob",)))
    with pytest.raises(InvalidTransition, match="can't tell when"):
        workflow.review(undated, ROB, P, WHY, solo=True, now=NOW)


def test_solo_never_covers_supervisor_decisions_and_is_only_recorded_when_needed():
    own_needs = result(version(1, flag(Severity.NEEDS_SUPERVISOR), analysts=("sue",), created_at=NOW))
    with pytest.raises(FourEyesViolation):
        workflow.accept(own_needs, SUE, P, "MDL taken from the method validation")
    assert not workflow.review(result(), ROB, P, WHY, solo=True, now=NOW).solo  # not his readings
    with pytest.raises(FourEyesViolation):
        workflow.review(ROBS_OWN, ROB, P)  # strict four-eyes unless solo is asked for
    with pytest.raises(ValidationError, match="only a review or a release"):
        ResultEvent(kind=EventKind.ACCEPTED, version=1, actor=SUE, reason_category="x", note="y", solo=True)


# --- flag-specific rules (D-97) ------------------------------------------------------------------------

CHECK_ONLY = version(1, flag(Severity.RERUN, "CHECK_FAILED"), flag(Severity.WARN), analysts=("ann",))
CHECK_AND_MORE = version(1, flag(Severity.RERUN, "CHECK_FAILED"), flag(Severity.RERUN), analysts=("ann",))
FAILED_CHECK = "Check standard failure assessed: element not affected"


def test_an_analyst_may_overrule_a_failed_check_standard_with_a_reason():
    event = workflow.record_exception(result(CHECK_ONLY), ANA, P, FAILED_CHECK, "Cu in bracket 2 read 98 %")
    assert event.kind == EventKind.EXCEPTION and event.actor == ANA and event.note.startswith("Cu")


def test_another_flag_as_bad_needs_the_general_rule():
    with pytest.raises(NotPermitted, match="needs SUPERVISOR"):
        workflow.record_exception(result(CHECK_AND_MORE), ANA, P, FAILED_CHECK, "x")


def test_four_eyes_still_keeps_the_analyst_off_their_own_run():
    own = version(1, flag(Severity.RERUN, "CHECK_FAILED"))  # entered by ana
    with pytest.raises(FourEyesViolation):
        workflow.record_exception(result(own), ANA, P, FAILED_CHECK, "x")
