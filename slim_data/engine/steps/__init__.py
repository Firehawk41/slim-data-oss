"""Built-in steps. Every released version stays here for good (D-17); see tests/engine/step_manifest.txt."""

from slim_data.engine.steps.agreement import Agreement
from slim_data.engine.steps.cup_choice import CupChoice
from slim_data.engine.steps.cup_shape import CupShape
from slim_data.engine.steps.linear_range import LinearRangeCheck

ALL_STEPS = (CupShape, LinearRangeCheck, Agreement, CupChoice)
