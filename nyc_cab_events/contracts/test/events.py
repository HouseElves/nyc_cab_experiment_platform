# pylint: disable=redefined-outer-name
"""Tests for :mod:`nyc_cab_events.contracts.events`."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from nyc_cab.exceptions import InvalidRequestError
from nyc_cab_events.contracts.events import (
    TRIP_COMPLETED_QUARANTINE_TOPIC,
    TRIP_COMPLETED_TOPIC,
    EventRejectionReason,
    TripCompleted,
    quarantine_topic_for,
)

pytestmark = pytest.mark.unit


# --- Fixtures ---------------------------------------------------------------


@pytest.fixture
def produced_at() -> datetime:
    """A timezone-aware UTC instant suitable for ``produced_at``."""
    return datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def valid_args(produced_at: datetime) -> tuple:
    """Positional args that construct a valid ``TripCompleted`` instance."""
    return (
        "abc123",       # event_id
        "yellow",       # cab_type
        2023,           # year
        1,              # month
        14,             # hour
        3.7,            # trip_distance
        18.50,          # fare_amount
        2,              # passenger_count
        produced_at,    # produced_at
    )


# --- Module-level constants -------------------------------------------------


def test_trip_completed_topic_constant() -> None:
    """``TRIP_COMPLETED_TOPIC`` is the canonical primary topic name."""
    assert TRIP_COMPLETED_TOPIC == "trip.completed.v1"


def test_trip_completed_quarantine_topic_constant() -> None:
    """``TRIP_COMPLETED_QUARANTINE_TOPIC`` is the canonical quarantine topic name."""
    assert TRIP_COMPLETED_QUARANTINE_TOPIC == "trip.completed.v1.invalid"


# --- Happy paths ------------------------------------------------------------


def test_create_validated_happy_path(valid_args: tuple) -> None:
    """A well-formed event constructs cleanly."""
    event = TripCompleted.create_validated(*valid_args)
    assert event.event_id == "abc123"
    assert event.cab_type == "yellow"
    assert event.year == 2023
    assert event.month == 1
    assert event.hour == 14
    assert event.trip_distance == 3.7
    assert event.fare_amount == 18.50
    assert event.passenger_count == 2


def test_zero_passenger_count_accepted(valid_args: tuple) -> None:
    """``passenger_count`` zero is allowed (no-passenger trips do appear in TLC data)."""
    args = list(valid_args)
    args[7] = 0  # passenger_count
    event = TripCompleted.create_validated(*args)
    assert event.passenger_count == 0


def test_hour_zero_accepted(valid_args: tuple) -> None:
    """``hour`` zero is the midnight bucket."""
    args = list(valid_args)
    args[4] = 0
    event = TripCompleted.create_validated(*args)
    assert event.hour == 0


def test_hour_twenty_three_accepted(valid_args: tuple) -> None:
    """``hour`` 23 is the last valid bucket."""
    args = list(valid_args)
    args[4] = 23
    event = TripCompleted.create_validated(*args)
    assert event.hour == 23


def test_zero_distance_accepted(valid_args: tuple) -> None:
    """``trip_distance`` zero is allowed (short hops round to zero in TLC data)."""
    args = list(valid_args)
    args[5] = 0.0
    event = TripCompleted.create_validated(*args)
    assert event.trip_distance == 0.0


def test_zero_fare_accepted(valid_args: tuple) -> None:
    """``fare_amount`` zero is allowed (voided fares appear with zero amounts)."""
    args = list(valid_args)
    args[6] = 0.0
    event = TripCompleted.create_validated(*args)
    assert event.fare_amount == 0.0


# --- Type-check rejections --------------------------------------------------


def test_rejects_non_string_event_id(valid_args: tuple) -> None:
    """``event_id`` must be a string."""
    args = list(valid_args)
    args[0] = 12345
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    names = [v[0] for v in info.value.violations]
    assert "event_id" in names


def test_rejects_non_string_cab_type(valid_args: tuple) -> None:
    """``cab_type`` must be a string."""
    args = list(valid_args)
    args[1] = 0
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    names = [v[0] for v in info.value.violations]
    assert "cab_type" in names


def test_rejects_bool_year(valid_args: tuple) -> None:
    """``year`` rejects bool despite int compatibility."""
    args = list(valid_args)
    args[2] = True
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    assert ("year", True) in info.value.violations


def test_rejects_bool_month(valid_args: tuple) -> None:
    """``month`` rejects bool despite int compatibility."""
    args = list(valid_args)
    args[3] = True
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    assert ("month", True) in info.value.violations


def test_rejects_bool_hour(valid_args: tuple) -> None:
    """``hour`` rejects bool despite int compatibility."""
    args = list(valid_args)
    args[4] = False
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    assert ("hour", False) in info.value.violations


def test_rejects_int_trip_distance(valid_args: tuple) -> None:
    """``trip_distance`` must be a float (not an int)."""
    args = list(valid_args)
    args[5] = 3  # int, not float
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    names = [v[0] for v in info.value.violations]
    assert "trip_distance" in names


def test_rejects_int_fare_amount(valid_args: tuple) -> None:
    """``fare_amount`` must be a float (not an int)."""
    args = list(valid_args)
    args[6] = 10
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    names = [v[0] for v in info.value.violations]
    assert "fare_amount" in names


def test_rejects_bool_passenger_count(valid_args: tuple) -> None:
    """``passenger_count`` rejects bool despite int compatibility."""
    args = list(valid_args)
    args[7] = True
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    assert ("passenger_count", True) in info.value.violations


def test_rejects_non_datetime_produced_at(valid_args: tuple) -> None:
    """``produced_at`` must be a ``datetime`` instance."""
    args = list(valid_args)
    args[8] = "2025-01-15T12:00:00Z"  # string, not datetime
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    names = [v[0] for v in info.value.violations]
    assert "produced_at" in names


# --- Structural rejections --------------------------------------------------


def test_rejects_blank_event_id(valid_args: tuple) -> None:
    """A whitespace-only event_id violates the structural rule."""
    args = list(valid_args)
    args[0] = "   "
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    assert ("event_id", "   ") in info.value.violations


def test_rejects_blank_cab_type(valid_args: tuple) -> None:
    """A whitespace-only cab_type violates the structural rule."""
    args = list(valid_args)
    args[1] = ""
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    assert ("cab_type", "") in info.value.violations


def test_rejects_year_below_range(valid_args: tuple) -> None:
    """Year 1899 is below the 1900-2100 range."""
    args = list(valid_args)
    args[2] = 1899
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    assert ("year", 1899) in info.value.violations


def test_rejects_month_zero(valid_args: tuple) -> None:
    """Month 0 is out of the 1-12 range."""
    args = list(valid_args)
    args[3] = 0
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    assert ("month", 0) in info.value.violations


def test_rejects_month_thirteen(valid_args: tuple) -> None:
    """Month 13 is out of the 1-12 range."""
    args = list(valid_args)
    args[3] = 13
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    assert ("month", 13) in info.value.violations


def test_rejects_negative_hour(valid_args: tuple) -> None:
    """Hour -1 is below the 0-23 range."""
    args = list(valid_args)
    args[4] = -1
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    assert ("hour", -1) in info.value.violations


def test_rejects_hour_twenty_four(valid_args: tuple) -> None:
    """Hour 24 is above the 0-23 range."""
    args = list(valid_args)
    args[4] = 24
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    assert ("hour", 24) in info.value.violations


def test_rejects_negative_trip_distance(valid_args: tuple) -> None:
    """Negative trip_distance violates the structural rule."""
    args = list(valid_args)
    args[5] = -0.5
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    assert ("trip_distance", -0.5) in info.value.violations


def test_rejects_negative_fare_amount(valid_args: tuple) -> None:
    """Negative fare_amount violates the structural rule."""
    args = list(valid_args)
    args[6] = -1.0
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    assert ("fare_amount", -1.0) in info.value.violations


def test_rejects_passenger_count_above_nine(valid_args: tuple) -> None:
    """passenger_count 10 is above the 0-9 range (matches Silver constraint)."""
    args = list(valid_args)
    args[7] = 10
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    assert ("passenger_count", 10) in info.value.violations


def test_rejects_negative_passenger_count(valid_args: tuple) -> None:
    """Negative passenger_count violates the structural rule."""
    args = list(valid_args)
    args[7] = -1
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    assert ("passenger_count", -1) in info.value.violations


def test_rejects_naive_produced_at(valid_args: tuple) -> None:
    """``produced_at`` must be timezone-aware (no naive datetimes on the wire)."""
    args = list(valid_args)
    args[8] = datetime(2025, 1, 15, 12, 0, 0)  # naive
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    names = [v[0] for v in info.value.violations]
    assert "produced_at" in names


def test_accepts_non_utc_tzaware_produced_at(valid_args: tuple) -> None:
    """Any tz-aware datetime is accepted; UTC normalization is the producer's job."""
    args = list(valid_args)
    args[8] = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone(timedelta(hours=-5)))
    event = TripCompleted.create_validated(*args)
    assert event.produced_at.tzinfo is not None


# --- Frozenness -------------------------------------------------------------


def test_is_frozen(valid_args: tuple) -> None:
    """``TripCompleted`` rejects attribute mutation."""
    event = TripCompleted.create_validated(*valid_args)
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.fare_amount = 0.0  # type: ignore[misc]


# --- validity_check ---------------------------------------------------------


def test_validity_check_passing(valid_args: tuple) -> None:
    """``validity_check`` returns a passing tuple for a valid instance."""
    event = TripCompleted.create_validated(*valid_args)
    passed, name, _value = event.validity_check("event")
    assert passed is True
    assert name == "event"


def test_validity_check_failing(produced_at: datetime) -> None:
    """``validity_check`` returns a failing tuple for an invalid instance."""
    bad = TripCompleted(
        event_id="x", cab_type="yellow", year=2023, month=1, hour=24,  # bad hour
        trip_distance=1.0, fare_amount=1.0, passenger_count=1, produced_at=produced_at,
    )
    passed, _name, _value = bad.validity_check("event")
    assert passed is False


# --- Quarantine routing -----------------------------------------------------


def test_quarantine_topic_for_invalid_construction() -> None:
    """The INVALID_CONSTRUCTION reason routes to the v1 quarantine topic."""
    assert quarantine_topic_for(EventRejectionReason.INVALID_CONSTRUCTION) == TRIP_COMPLETED_QUARANTINE_TOPIC


def test_quarantine_topic_rejects_non_enum() -> None:
    """``quarantine_topic_for`` rejects values that are not ``EventRejectionReason``."""
    with pytest.raises(TypeError):
        quarantine_topic_for("invalid_construction")  # type: ignore[arg-type]


def test_event_rejection_reason_values() -> None:
    """The enum's wire-string values are stable identifiers."""
    assert EventRejectionReason.INVALID_CONSTRUCTION.value == "invalid_construction"
