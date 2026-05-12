"""Tests for :mod:`nyc_cab.transform.silver_request`."""


from __future__ import annotations

import dataclasses

import pytest

from nyc_cab.exceptions import InvalidRequestError
from nyc_cab.transform.silver_request import SilverTransformRequest


# --- Happy paths ------------------------------------------------------------


def test_create_validated_happy_path() -> None:
    """A well-formed request constructs cleanly."""
    request = SilverTransformRequest.create_validated("yellow", 2023, 1)
    assert request.cab_type == "yellow"
    assert request.year == 2023
    assert request.month == 1


def test_period_id_property() -> None:
    """The period_id property returns the canonical YYYY-MM format."""
    request = SilverTransformRequest.create_validated("yellow", 2023, 1)
    assert request.period_id == "2023-01"


def test_period_id_zero_pads_month() -> None:
    """Single-digit months are zero-padded."""
    request = SilverTransformRequest.create_validated("yellow", 2023, 7)
    assert request.period_id == "2023-07"


# --- Type-check rejections --------------------------------------------------


def test_rejects_non_string_cab_type() -> None:
    """``cab_type`` must be a string."""
    with pytest.raises(InvalidRequestError) as info:
        SilverTransformRequest.create_validated(123, 2023, 1)
    names = [v[0] for v in info.value.violations]
    assert "cab_type" in names


def test_rejects_bool_year() -> None:
    """``year`` rejects bool despite int compatibility."""
    with pytest.raises(InvalidRequestError) as info:
        SilverTransformRequest.create_validated("yellow", True, 1)
    assert ("year", True) in info.value.violations


def test_rejects_bool_month() -> None:
    """``month`` rejects bool despite int compatibility."""
    with pytest.raises(InvalidRequestError) as info:
        SilverTransformRequest.create_validated("yellow", 2023, True)
    assert ("month", True) in info.value.violations


# --- Structural rejections --------------------------------------------------


def test_rejects_blank_cab_type() -> None:
    """A whitespace-only cab_type violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        SilverTransformRequest.create_validated("   ", 2023, 1)
    assert ("cab_type", "   ") in info.value.violations


def test_rejects_month_zero() -> None:
    """Month 0 is out of the 1-12 range."""
    with pytest.raises(InvalidRequestError) as info:
        SilverTransformRequest.create_validated("yellow", 2023, 0)
    assert ("month", 0) in info.value.violations


def test_rejects_month_thirteen() -> None:
    """Month 13 is out of the 1-12 range."""
    with pytest.raises(InvalidRequestError) as info:
        SilverTransformRequest.create_validated("yellow", 2023, 13)
    assert ("month", 13) in info.value.violations


def test_rejects_year_below_range() -> None:
    """Year 1899 is below the 1900-2100 range."""
    with pytest.raises(InvalidRequestError) as info:
        SilverTransformRequest.create_validated("yellow", 1899, 1)
    assert ("year", 1899) in info.value.violations


# --- Frozenness -------------------------------------------------------------


def test_is_frozen() -> None:
    """``SilverTransformRequest`` rejects attribute mutation."""
    request = SilverTransformRequest.create_validated("yellow", 2023, 1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.month = 2  # type: ignore[misc]


# --- validity_check ---------------------------------------------------------


def test_validity_check_passing() -> None:
    """``validity_check`` returns a passing tuple for a valid instance."""
    request = SilverTransformRequest.create_validated("yellow", 2023, 1)
    passed, name, _value = request.validity_check("request")
    assert passed is True
    assert name == "request"


def test_validity_check_failing() -> None:
    """``validity_check`` returns a failing tuple for an invalid instance."""
    bad = SilverTransformRequest("yellow", 2023, 13)
    passed, _name, _value = bad.validity_check("request")
    assert passed is False
