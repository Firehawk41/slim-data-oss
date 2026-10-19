"""Value objects: identities, quantities, float refusal."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from slim_data.domain.values import AnalyteRef, Quantity, SampleRef


def test_sample_ref_parses_and_prints_the_lab_format():
    ref = SampleRef.parse(" TR00042-03 ")
    assert ref == SampleRef(tr_number="TR00042", position=3)
    assert str(ref) == "TR00042-03"


@given(number=st.integers(min_value=1, max_value=9_999_999), position=st.integers(min_value=1, max_value=999))
def test_sample_ref_round_trips(number, position):
    ref = SampleRef(tr_number=f"TR{number:05d}", position=position)
    assert SampleRef.parse(str(ref)) == ref


@pytest.mark.parametrize("text", ["TR42-01", "TR00042", "TR00042-", "XX00042-01", "TR00042-0a", ""])
def test_sample_ref_rejects_malformed_ids(text):
    with pytest.raises(ValueError):
        SampleRef.parse(text)


def test_sample_ref_validates_fields():
    with pytest.raises(ValidationError):
        SampleRef(tr_number="TR42", position=1)
    with pytest.raises(ValidationError):
        SampleRef(tr_number="TR00042", position=0)


def test_analyte_ref():
    assert str(AnalyteRef(analysis_id=3, code="Cl-")) == "Cl- (analysis 3)"
    with pytest.raises(ValidationError):
        AnalyteRef(analysis_id=3, code=" Fe")
    with pytest.raises(ValidationError):
        AnalyteRef(analysis_id=0, code="Fe")


def test_quantity_keeps_its_digits_and_refuses_floats():
    q = Quantity(value="12.40", unit="ppb")
    assert q.value == Decimal("12.40") and str(q.value) == "12.40"
    assert str(q) == "12.40 ppb"
    assert Quantity(value=7, unit="ppb").value == Decimal(7)
    with pytest.raises(ValidationError, match="float"):
        Quantity(value=12.4, unit="ppb")


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_quantity_must_be_finite(value):
    with pytest.raises(ValidationError, match="finite number"):
        Quantity(value=Decimal(value), unit="ppb")


def test_domain_objects_are_immutable():
    ref = SampleRef(tr_number="TR00042", position=1)
    with pytest.raises(ValidationError):
        ref.position = 2
