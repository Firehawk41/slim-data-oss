"""Enums, stored as ints (portable schema). Never renumber a member: the numbers are in the database."""

from enum import IntEnum


class ReadingRole(IntEnum):
    """What a reading measures (D-06)."""

    CUP = 1  # a sample replicate
    SPIKE = 2  # a sample aliquot with a known amount added
    BLANK = 3  # the prep batch's method blank
    CHECK = 4  # a check standard (CCV): a solution of known concentration (D-87)
    CRM = 5  # a certified reference material, prepped like a sample (D-87)

    @property
    def of_batch(self) -> bool:
        """Belongs to the prep batch, not to a sample: no SampleRef (D-64, D-87)."""
        return self in BATCH_ROLES

    @property
    def is_qc(self) -> bool:
        """Measured against an expected value (D-87)."""
        return self in QC_ROLES


QC_ROLES = frozenset({ReadingRole.CHECK, ReadingRole.CRM})
BATCH_ROLES = frozenset({ReadingRole.BLANK}) | QC_ROLES


class SourceKind(IntEnum):
    """How a run's readings came in (D-88): the lab's policy says which it accepts."""

    INSTRUMENT = 1  # the instrument software's own export, read by its reader (e.g. qtegra)
    CSV = 2  # a file in slim-data's generic layout, made by hand or by a script
    PASTE = 3  # a table pasted in, as from Excel


class ExclusionKind(IntEnum):
    """An analyst takes a reading out, or puts it back (D-65). The newest one for a reading decides."""

    EXCLUDED = 1
    RESTORED = 2


class IdentitySource(IntEnum):
    """How the caller knows who the actor is (D-18, D-50)."""

    AUTHENTICATED = 1  # a real login
    ASSERTED = 2  # claimed, e.g. a demo persona


class Role(IntEnum):
    """Staff roles, ordered: a higher role can do what a lower one can (tiered policy, D-10, D-57)."""

    ANALYST = 1
    REVIEWER = 2
    SUPERVISOR = 3


class Severity(IntEnum):
    """Severity of an engine flag (D-56). The verdict is the worst one."""

    INFO = 1
    WARN = 2
    NEEDS_SUPERVISOR = 3
    RERUN = 4


class Verdict(IntEnum):
    """What the engine decided about a result (D-08). Separate from the workflow status."""

    REPORTABLE = 1
    NEEDS_SUPERVISOR = 2
    RERUN = 3


class Qualifier(IntEnum):
    """How the reported value reads (D-54)."""

    DETECTED = 1  # a plain number
    BELOW_MDL = 2  # "< effective MDL"
    ESTIMATED = 3  # between MDL and QL
    NOT_REPORTABLE = 4  # the engine found nothing to report (e.g. no cup could be chosen); no value


class ResultStatus(IntEnum):
    """Where people have got to with a result version (D-08)."""

    DRAFT = 1
    REVIEWED = 2
    RELEASED = 3
    REJECTED = 4


class Action(IntEnum):
    """Things people do to a result. Policies grant them by role and identity source."""

    REVIEW = 1
    ACCEPT = 2  # supervisor accepts a NEEDS_SUPERVISOR judgment call (D-57)
    REQUEST_RERUN = 3
    RECORD_EXCEPTION = 4  # release a RERUN result without rerunning (D-09)
    RELEASE = 5
    REJECT = 6
    CORRECT = 7  # start a correction: a new version follows (D-20)


class EventKind(IntEnum):
    """What a result event records. One kind per Action."""

    REVIEWED = 1
    ACCEPTED = 2
    RERUN_REQUESTED = 3
    EXCEPTION = 4
    RELEASED = 5
    REJECTED = 6
    CORRECTION_STARTED = 7
