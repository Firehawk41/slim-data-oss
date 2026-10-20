"""Publishing rulesets and policies (D-14, D-15, D-30)."""

import pytest
from sqlalchemy import text, update

from slim_data.domain.policy import DEMO_POLICY, PRODUCTION_EXAMPLE_POLICY
from slim_data.engine.ruleset import RulesetError
from slim_data.persistence._rows import NotFound
from slim_data.persistence.rulesets import (
    current_policy,
    get_ruleset,
    publish_policy,
    publish_ruleset,
    ruleset_for,
)
from slim_data.persistence.tables import rulesets
from tests.engine.helpers import DEMO
from tests.factories import ANA, SUE
from tests.persistence.conftest import instant


def with_agreement(ruleset, **params):
    steps = tuple(
        s.model_copy(update={"params": params}) if s.step == "agreement@1" else s for s in ruleset.steps
    )
    return ruleset.model_copy(update={"steps": steps})


def test_publishing_numbers_and_hashes_a_ruleset_once(session):
    first = publish_ruleset(session, DEMO, SUE)
    again = publish_ruleset(session, DEMO, ANA)
    assert again.id == first.id and again.published_at == first.published_at
    assert get_ruleset(session, first.id).ruleset == DEMO
    with pytest.raises(NotFound):
        get_ruleset(session, 999)


def test_a_cliff_needs_a_stored_reason(session):
    cliff = with_agreement(DEMO, switch_multiple="5", rpd_limit="0.20", absolute_multiple="2")
    with pytest.raises(RulesetError, match="needs a reason"):
        publish_ruleset(session, cliff, SUE, cliff_reason="  ")
    published = publish_ruleset(session, cliff, SUE, cliff_reason="method validation report MV-12")
    assert published.cliff_reason == "method validation report MV-12"


def test_a_prep_specific_ruleset_wins_over_the_analysis_default_as_of_any_time(session):
    default = publish_ruleset(session, DEMO, SUE)
    before = instant(session)
    hot_block = publish_ruleset(session, DEMO.model_copy(update={"prep_method": "hot block HNO3"}), SUE)
    assert ruleset_for(session, 1, "hot block HNO3").id == hot_block.id
    assert ruleset_for(session, 1, "microwave HNO3").id == default.id
    assert ruleset_for(session, 1, "hot block HNO3", as_of=before).id == default.id
    assert ruleset_for(session, 2, "microwave HNO3") is None


def test_a_newer_default_replaces_the_older_one(session):
    publish_ruleset(session, DEMO, SUE)
    newer = publish_ruleset(session, DEMO.model_copy(update={"name": "demo-metals-v2"}), SUE)
    assert ruleset_for(session, 1, "microwave HNO3").id == newer.id


def test_a_tampered_body_is_refused_on_read(session):
    published = publish_ruleset(session, DEMO, SUE)
    # What a table owner could do by dropping the trigger; the content hash still catches it.
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("DROP TRIGGER slim_data_append_only ON rulesets"))
    else:
        session.execute(text('DROP TRIGGER "slim_data_no_update_rulesets"'))
    session.execute(
        update(rulesets).values(body=DEMO.model_copy(update={"name": "x"}).model_dump(mode="json"))
    )
    with pytest.raises(RulesetError, match="no longer matches its content hash"):
        get_ruleset(session, published.id)


def test_the_latest_policy_is_in_force(session):
    assert current_policy(session) is None
    demo = publish_policy(session, DEMO_POLICY, SUE)
    before = instant(session)
    production = publish_policy(session, PRODUCTION_EXAMPLE_POLICY, SUE)
    assert publish_policy(session, PRODUCTION_EXAMPLE_POLICY, SUE).id == production.id
    assert current_policy(session).policy == PRODUCTION_EXAMPLE_POLICY
    assert current_policy(session, as_of=before).id == demo.id
