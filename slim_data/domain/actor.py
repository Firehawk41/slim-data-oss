"""Who did something (D-18, D-50). slim-data never authenticates; the host passes an Actor in."""

from pydantic import Field

from slim_data.domain.base import Frozen
from slim_data.domain.enums import IdentitySource, Role


class Actor(Frozen):
    """`id` is what four-eyes compares; `identity_source` is stored on every event."""

    id: str = Field(min_length=1, max_length=100)
    display: str = Field(min_length=1, max_length=254)
    roles: frozenset[Role] = frozenset()
    identity_source: IdentitySource

    def has_role_at_least(self, minimum: Role) -> bool:
        return any(role >= minimum for role in self.roles)
