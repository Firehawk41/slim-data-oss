"""Prep batches, runs, readings and exclusions."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from slim_data.domain.enums import ReadingRole
from slim_data.domain.observations import PrepBatch, Reading, ReadingExclusion, Run, SourceFile, SpikeAddition
from tests.factories import ANA, NOW, ROB, analyte, qty, sample


def spike(position=1, code="Fe", added="10", analysis_id=1):
    return SpikeAddition(sample=sample(position), analyte=analyte(code, analysis_id), added=qty(added))


def batch(**overrides):
    fields = dict(code="PB-0001", analysis_id=1, prep_method="HNO3 digest", samples=(sample(1), sample(2)))
    fields.update(overrides)
    return PrepBatch(**fields)


def test_batch_finds_its_spike_amounts():
    b = batch(spikes=(spike(1, "Fe"), spike(2, "Fe", "20")))
    assert b.spike_added(sample(2), analyte("Fe")) == qty("20")
    assert b.spike_added(sample(1), analyte("Zn")) is None


@pytest.mark.parametrize(
    "overrides, message",
    [
        (dict(samples=()), "at least 1"),
        (dict(samples=(sample(1), sample(1))), "appears twice"),
        (dict(spikes=(spike(3),)), "not in the batch"),
        (dict(spikes=(spike(1, analysis_id=2),)), "not this batch's analysis"),
        (dict(spikes=(spike(1), spike(1, added="5"))), "two spike amounts"),
    ],
)
def test_batch_rejects_inconsistent_shapes(overrides, message):
    with pytest.raises(ValidationError, match=message):
        batch(**overrides)


def test_spike_amount_must_be_positive():
    with pytest.raises(ValidationError, match="positive"):
        spike(added="0")


def reading(**overrides):
    fields = dict(sample=sample(), analyte=analyte(), role=ReadingRole.CUP, replicate=1, value=qty("1.24"))
    fields.update(overrides)
    return Reading(**fields)


def test_a_blank_belongs_to_the_batch_not_a_sample():
    assert reading(role=ReadingRole.BLANK, sample=None).sample is None


def test_effective_mdl_is_mdl_times_dilution():
    assert reading(mdl=qty("0.05"), dilution=Decimal(10)).effective_mdl == qty("0.50")
    assert reading().effective_mdl is None
    assert reading().dilution == 1


@pytest.mark.parametrize(
    "overrides, message",
    [
        (dict(mdl=qty("0.05", "ppm")), "same unit"),
        (dict(mdl=qty("0")), "positive"),
        (dict(dilution=Decimal("0.5")), "at least 1"),
        (dict(replicate=0), "greater than or equal to 1"),
        (dict(dilution=1.5), "float"),
        (dict(role=ReadingRole.BLANK), "BLANK reading has no sample"),
        (dict(sample=None), "every other reading needs one"),
    ],
)
def test_reading_rejects_bad_values(overrides, message):
    with pytest.raises(ValidationError, match=message):
        reading(**overrides)


def test_run_and_exclusion():
    source = SourceFile(name="run43.csv", sha256="a" * 64, reader="generic_csv/1")
    run = Run(
        run_code="RUN-0043",
        instrument="ICP-MS 1",
        analyst=ANA,
        imported_by=ROB,
        analyzed_at=NOW,
        source=source,
    )
    assert run.source.reader == "generic_csv/1" and run.entered_by == {"ana", "rob"}
    with pytest.raises(ValidationError):
        SourceFile(name="run43.csv", sha256="not-a-hash", reader="generic_csv/1")
    with pytest.raises(ValidationError):
        Run(run_code="RUN-0043", instrument="ICP-MS 1", analyst=ANA, imported_by=ANA,
            analyzed_at=NOW.replace(tzinfo=None), source=source)  # fmt: skip
    assert ReadingExclusion(reading_id=7, reason="cup cracked", actor=ANA).at is None
    with pytest.raises(ValidationError):
        ReadingExclusion(reading_id=7, reason="", actor=ANA)
