"""Built-in steps. Every released version stays here for good (D-17); see tests/engine/step_manifest.txt."""

from slim_data.engine.steps.agreement import Agreement
from slim_data.engine.steps.calibration import CalibrationCheck
from slim_data.engine.steps.cup_choice import CupChoice, CupChoice2
from slim_data.engine.steps.cup_shape import CupShape
from slim_data.engine.steps.linear_range import LinearRangeCheck
from slim_data.engine.steps.mdl_source import MdlSource
from slim_data.engine.steps.method_blank import MethodBlank
from slim_data.engine.steps.precision import Precision
from slim_data.engine.steps.qc_check import QcCheck
from slim_data.engine.steps.recovery import Recovery
from slim_data.engine.steps.recovery_pooling import RecoveryPooling
from slim_data.engine.steps.reported_value import ReportedValue
from slim_data.engine.steps.spec_limits import SpecLimits
from slim_data.engine.steps.uncertainty import Uncertainty

ALL_STEPS = (
    CupShape,
    LinearRangeCheck,
    Agreement,
    CupChoice,
    Recovery,
    RecoveryPooling,
    ReportedValue,
    Uncertainty,
    MethodBlank,
    QcCheck,
    SpecLimits,
    Precision,
    CalibrationCheck,
    MdlSource,
    CupChoice2,
)
