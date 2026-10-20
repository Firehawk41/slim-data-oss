"""Publishing rulesets and policies (D-14, D-15, D-30): a file becomes a numbered, content-hashed,
immutable row. Publishing the same content again returns the existing number instead of a new one.
"""

from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from slim_data.domain.actor import Actor
from slim_data.domain.base import Frozen
from slim_data.domain.policy import Policy
from slim_data.engine.registry import StepRegistry, default_registry
from slim_data.engine.ruleset import Ruleset, RulesetError, ValidatedRuleset, content_hash, validate_ruleset
from slim_data.persistence._rows import NotFound, actor_json, as_of_filter
from slim_data.persistence.audit import set_change_context
from slim_data.persistence.tables import policies, rulesets


class PublishedRuleset(Frozen):
    model_config = Frozen.model_config | {"arbitrary_types_allowed": True}

    id: int
    validated: ValidatedRuleset
    cliff_reason: str | None
    published_at: datetime

    @property
    def ruleset(self) -> Ruleset:
        return self.validated.ruleset


class PublishedPolicy(Frozen):
    id: int
    policy: Policy
    published_at: datetime


def publish_ruleset(
    session: Session,
    ruleset: Ruleset,
    actor: Actor,
    cliff_reason: str | None = None,
    registry: StepRegistry | None = None,
) -> PublishedRuleset:
    validated = validate_ruleset(ruleset, registry or default_registry())
    reason = (cliff_reason or "").strip() or None
    if validated.warnings and reason is None:
        raise RulesetError("publishing needs a reason for: " + "; ".join(validated.warnings))
    existing = session.scalar(select(rulesets.c.id).where(rulesets.c.content_hash == validated.content_hash))
    if existing is None:
        set_change_context(session, actor, reason or f"publish ruleset {ruleset.name}")
        session.execute(
            rulesets.insert().values(
                name=ruleset.name,
                analysis_id=ruleset.analysis_id,
                prep_method=ruleset.prep_method,
                content_hash=validated.content_hash,
                body=ruleset.model_dump(mode="json"),
                cliff_reason=reason,
                published_by_id=actor.id,
                published_by=actor_json(actor),
            )
        )
    return get_ruleset(session, content_hash=validated.content_hash, registry=registry)


def get_ruleset(
    session: Session, id: int | None = None, content_hash: str | None = None, registry=None
) -> PublishedRuleset:
    condition = rulesets.c.id == id if id is not None else rulesets.c.content_hash == content_hash
    row = session.execute(select(rulesets).where(condition)).one_or_none()
    if row is None:
        raise NotFound(f"no published ruleset {id or content_hash}")
    return _published(row, registry)


def ruleset_for(
    session: Session, analysis_id: int, prep_method: str, as_of: datetime | None = None, registry=None
) -> PublishedRuleset | None:
    """The latest ruleset for (analysis, prep method), else the analysis default (D-15)."""
    rows = session.execute(
        select(rulesets)
        .where(
            rulesets.c.analysis_id == analysis_id,
            or_(rulesets.c.prep_method == prep_method, rulesets.c.prep_method.is_(None)),
            as_of_filter(rulesets.c.created_at, as_of),
        )
        .order_by(rulesets.c.prep_method.is_(None), rulesets.c.created_at.desc(), rulesets.c.id.desc())
        .limit(1)
    ).one_or_none()
    return None if rows is None else _published(rows, registry)


def _published(row, registry) -> PublishedRuleset:
    ruleset = Ruleset.model_validate(row.body)
    validated = validate_ruleset(ruleset, registry or default_registry())
    if validated.content_hash != row.content_hash:
        raise RulesetError(f"ruleset {row.id} no longer matches its content hash")
    return PublishedRuleset(
        id=row.id, validated=validated, cliff_reason=row.cliff_reason, published_at=row.created_at
    )


def publish_policy(session: Session, policy: Policy, actor: Actor) -> PublishedPolicy:
    digest = content_hash(policy)
    if session.scalar(select(policies.c.id).where(policies.c.content_hash == digest)) is None:
        set_change_context(session, actor, f"publish policy {policy.name}")
        session.execute(
            policies.insert().values(
                name=policy.name,
                content_hash=digest,
                body=policy.model_dump(mode="json"),
                published_by_id=actor.id,
                published_by=actor_json(actor),
            )
        )
    return _policy(session, policies.c.content_hash == digest)


def current_policy(session: Session, as_of: datetime | None = None) -> PublishedPolicy | None:
    """The latest published policy: one policy is in force at a time."""
    return _policy(session, as_of_filter(policies.c.created_at, as_of))


def _policy(session: Session, condition) -> PublishedPolicy | None:
    row = session.execute(
        select(policies).where(condition).order_by(policies.c.created_at.desc(), policies.c.id.desc())
    ).first()
    if row is None:
        return None
    return PublishedPolicy(id=row.id, policy=Policy.model_validate(row.body), published_at=row.created_at)
