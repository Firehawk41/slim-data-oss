"""The registry of step versions (D-17): add a version, never replace or remove one."""

from slim_data.engine.ruleset import RulesetError
from slim_data.engine.step import Step


class StepRegistry:
    def __init__(self) -> None:
        self._steps: dict[str, Step] = {}

    def register(self, step: Step) -> None:
        key = step.key()
        if key in self._steps:
            raise ValueError(f"{key} is already registered; a changed step is a new version")
        self._steps[key] = step

    def get(self, key: str) -> Step:
        try:
            return self._steps[key]
        except KeyError:
            raise RulesetError(f"unknown step {key}") from None

    def keys(self) -> list[str]:
        return sorted(self._steps)


def default_registry() -> StepRegistry:
    from slim_data.engine.steps import ALL_STEPS

    registry = StepRegistry()
    for step_class in ALL_STEPS:
        registry.register(step_class())
    return registry
