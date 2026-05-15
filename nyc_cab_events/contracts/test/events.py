# pylint: disable=redefined-outer-name
"""Tests for :mod:`nyc_cab_events.contracts.events`."""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from nyc_cab.exceptions import InvalidRequestError
from nyc_cab_events.contracts.events import (
    EVENT_ID_HASH_FIELDS,
    EVENT_ID_HASH_TRUNCATION,
    SCHEMA_VERSION,
    TRIP_COMPLETED_QUARANTINE_TOPIC,
    TRIP_COMPLETED_TOPIC,
    EventRejectionReason,
    InvalidEventPayloadError,
    TripCompleted,
    derive_event_id,
    event_key,
    from_json,
    quarantine_topic_for,
    to_json,
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
        "abc123def456",     # event_id
        SCHEMA_VERSION,     # schema_version
        "yellow",           # cab_type
        2023,               # year
        1,                  # month
        14,                 # hour
        3.7,                # trip_distance
        18.50,              # fare_amount
        2,                  # passenger_count
        produced_at,        # produced_at
    )


@pytest.fixture
def silver_row() -> dict[str, Any]:
    """A Silver-shaped source row sufficient for :func:`derive_event_id`."""
    return {
        "cab_type": "yellow",
        "year": 2023,
        "month": 1,
        "VendorID": 1,
        "tpep_pickup_datetime": datetime(2023, 1, 15, 14, 30, 0),
        "tpep_dropoff_datetime": datetime(2023, 1, 15, 14, 55, 0),
        "PULocationID": 161,
        "DOLocationID": 236,
        "fare_amount": 18.50,
        "total_amount": 22.50,
    }


# --- Module-level constants -------------------------------------------------


def test_trip_completed_topic_constant() -> None:
    """``TRIP_COMPLETED_TOPIC`` is the canonical primary topic name."""
    assert TRIP_COMPLETED_TOPIC == "trip.completed.v1"


def test_trip_completed_quarantine_topic_constant() -> None:
    """``TRIP_COMPLETED_QUARANTINE_TOPIC`` is the canonical quarantine topic name."""
    assert TRIP_COMPLETED_QUARANTINE_TOPIC == "trip.completed.v1.invalid"


def test_schema_version_constant() -> None:
    """``SCHEMA_VERSION`` is the current wire-format major version."""
    assert SCHEMA_VERSION == "1"


def test_event_id_hash_fields_is_ordered_tuple() -> None:
    """The hash field order is part of the contract."""
    assert isinstance(EVENT_ID_HASH_FIELDS, tuple)
    assert len(EVENT_ID_HASH_FIELDS) == 10


def test_event_id_hash_truncation_constant() -> None:
    """16 hex characters = 64 bits of collision resistance."""
    assert EVENT_ID_HASH_TRUNCATION == 16


# --- Happy paths ------------------------------------------------------------


def test_create_validated_happy_path(valid_args: tuple) -> None:
    """A well-formed event constructs cleanly."""
    event = TripCompleted.create_validated(*valid_args)
    assert event.event_id == "abc123def456"
    assert event.schema_version == SCHEMA_VERSION
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
    args[8] = 0  # passenger_count is now index 8 (schema_version pushed it down)
    event = TripCompleted.create_validated(*args)
    assert event.passenger_count == 0


def test_hour_zero_accepted(valid_args: tuple) -> None:
    """``hour`` zero is the midnight bucket."""
    args = list(valid_args)
    args[5] = 0
    event = TripCompleted.create_validated(*args)
    assert event.hour == 0


def test_hour_twenty_three_accepted(valid_args: tuple) -> None:
    """``hour`` 23 is the last valid bucket."""
    args = list(valid_args)
    args[5] = 23
    event = TripCompleted.create_validated(*args)
    assert event.hour == 23


def test_zero_distance_accepted(valid_args: tuple) -> None:
    """``trip_distance`` zero is allowed (short hops round to zero in TLC data)."""
    args = list(valid_args)
    args[6] = 0.0
    event = TripCompleted.create_validated(*args)
    assert event.trip_distance == 0.0


def test_zero_fare_accepted(valid_args: tuple) -> None:
    """``fare_amount`` zero is allowed (voided fares appear with zero amounts)."""
    args = list(valid_args)
    args[7] = 0.0
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


def test_rejects_non_string_schema_version(valid_args: tuple) -> None:
    """``schema_version`` must be a string (no int 1 or float 1.0)."""
    args = list(valid_args)
    args[1] = 1
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    names = [v[0] for v in info.value.violations]
    assert "schema_version" in names


def test_rejects_non_string_cab_type(valid_args: tuple) -> None:
    """``cab_type`` must be a string."""
    args = list(valid_args)
    args[2] = 0
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    names = [v[0] for v in info.value.violations]
    assert "cab_type" in names


def test_rejects_bool_year(valid_args: tuple) -> None:
    """``year`` rejects bool despite int compatibility."""
    args = list(valid_args)
    args[3] = True
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    assert ("year", True) in info.value.violations


def test_rejects_bool_month(valid_args: tuple) -> None:
    """``month`` rejects bool despite int compatibility."""
    args = list(valid_args)
    args[4] = True
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    assert ("month", True) in info.value.violations


def test_rejects_bool_hour(valid_args: tuple) -> None:
    """``hour`` rejects bool despite int compatibility."""
    args = list(valid_args)
    args[5] = False
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    assert ("hour", False) in info.value.violations


def test_rejects_int_trip_distance(valid_args: tuple) -> None:
    """``trip_distance`` must be a float (not an int)."""
    args = list(valid_args)
    args[6] = 3
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    names = [v[0] for v in info.value.violations]
    assert "trip_distance" in names


def test_rejects_int_fare_amount(valid_args: tuple) -> None:
    """``fare_amount`` must be a float (not an int)."""
    args = list(valid_args)
    args[7] = 10
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    names = [v[0] for v in info.value.violations]
    assert "fare_amount" in names


def test_rejects_bool_passenger_count(valid_args: tuple) -> None:
    """``passenger_count`` rejects bool despite int compatibility."""
    args = list(valid_args)
    args[8] = True
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    assert ("passenger_count", True) in info.value.violations


def test_rejects_non_datetime_produced_at(valid_args: tuple) -> None:
    """``produced_at`` must be a ``datetime`` instance."""
    args = list(valid_args)
    args[9] = "2025-01-15T12:00:00Z"
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


def test_rejects_wrong_schema_version(valid_args: tuple) -> None:
    """``schema_version`` must equal :data:`SCHEMA_VERSION` exactly."""
    args = list(valid_args)
    args[1] = "2"
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    assert ("schema_version", "2") in info.value.violations


def test_rejects_semver_schema_version(valid_args: tuple) -> None:
    """A semver-shaped schema_version (e.g. ``1.0``) is rejected — strict equality."""
    args = list(valid_args)
    args[1] = "1.0"
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    assert ("schema_version", "1.0") in info.value.violations


def test_rejects_blank_cab_type(valid_args: tuple) -> None:
    """A whitespace-only cab_type violates the structural rule."""
    args = list(valid_args)
    args[2] = ""
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    assert ("cab_type", "") in info.value.violations


def test_rejects_year_below_range(valid_args: tuple) -> None:
    """Year 1899 is below the 1900-2100 range."""
    args = list(valid_args)
    args[3] = 1899
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    assert ("year", 1899) in info.value.violations


def test_rejects_month_zero(valid_args: tuple) -> None:
    """Month 0 is out of the 1-12 range."""
    args = list(valid_args)
    args[4] = 0
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    assert ("month", 0) in info.value.violations


def test_rejects_month_thirteen(valid_args: tuple) -> None:
    """Month 13 is out of the 1-12 range."""
    args = list(valid_args)
    args[4] = 13
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    assert ("month", 13) in info.value.violations


def test_rejects_negative_hour(valid_args: tuple) -> None:
    """Hour -1 is below the 0-23 range."""
    args = list(valid_args)
    args[5] = -1
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    assert ("hour", -1) in info.value.violations


def test_rejects_hour_twenty_four(valid_args: tuple) -> None:
    """Hour 24 is above the 0-23 range."""
    args = list(valid_args)
    args[5] = 24
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    assert ("hour", 24) in info.value.violations


def test_rejects_negative_trip_distance(valid_args: tuple) -> None:
    """Negative trip_distance violates the structural rule."""
    args = list(valid_args)
    args[6] = -0.5
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    assert ("trip_distance", -0.5) in info.value.violations


def test_rejects_negative_fare_amount(valid_args: tuple) -> None:
    """Negative fare_amount violates the structural rule."""
    args = list(valid_args)
    args[7] = -1.0
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    assert ("fare_amount", -1.0) in info.value.violations


def test_rejects_passenger_count_above_nine(valid_args: tuple) -> None:
    """passenger_count 10 is above the 0-9 range (matches Silver constraint)."""
    args = list(valid_args)
    args[8] = 10
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    assert ("passenger_count", 10) in info.value.violations


def test_rejects_negative_passenger_count(valid_args: tuple) -> None:
    """Negative passenger_count violates the structural rule."""
    args = list(valid_args)
    args[8] = -1
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    assert ("passenger_count", -1) in info.value.violations


def test_rejects_naive_produced_at(valid_args: tuple) -> None:
    """``produced_at`` must be timezone-aware (no naive datetimes on the wire)."""
    args = list(valid_args)
    args[9] = datetime(2025, 1, 15, 12, 0, 0)
    with pytest.raises(InvalidRequestError) as info:
        TripCompleted.create_validated(*args)
    names = [v[0] for v in info.value.violations]
    assert "produced_at" in names


def test_accepts_non_utc_tzaware_produced_at(valid_args: tuple) -> None:
    """Any tz-aware datetime is accepted; UTC normalization is the producer's job."""
    args = list(valid_args)
    args[9] = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone(timedelta(hours=-5)))
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
        event_id="x", schema_version=SCHEMA_VERSION, cab_type="yellow",
        year=2023, month=1, hour=24, trip_distance=1.0, fare_amount=1.0,
        passenger_count=1, produced_at=produced_at,
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


# --- derive_event_id --------------------------------------------------------


def test_derive_event_id_returns_truncated_hex(silver_row: dict[str, Any]) -> None:
    """The id has the documented hex-character length."""
    eid = derive_event_id(silver_row)
    assert len(eid) == EVENT_ID_HASH_TRUNCATION
    assert all(c in "0123456789abcdef" for c in eid)


def test_derive_event_id_deterministic(silver_row: dict[str, Any]) -> None:
    """Same input → same id every call."""
    assert derive_event_id(silver_row) == derive_event_id(silver_row)


def test_derive_event_id_deterministic_across_dict_orderings(silver_row: dict[str, Any]) -> None:
    """Determinism does not depend on dict insertion order."""
    reordered = {k: silver_row[k] for k in reversed(list(silver_row.keys()))}
    assert derive_event_id(silver_row) == derive_event_id(reordered)


def test_derive_event_id_silently_ignores_extras(silver_row: dict[str, Any]) -> None:
    """Extra keys in the source dict do not affect the hash."""
    base = derive_event_id(silver_row)
    with_extras = derive_event_id({**silver_row, "extraneous": "noise", "RatecodeID": 1})
    assert base == with_extras


@pytest.mark.parametrize("field", EVENT_ID_HASH_FIELDS)
def test_derive_event_id_changes_when_each_hash_field_changes(
    silver_row: dict[str, Any], field: str,
) -> None:
    """Mutating any single hash-input field changes the id.

    Catches the failure mode where a field is documented as part of the
    hash but accidentally omitted from the implementation.
    """
    base = derive_event_id(silver_row)
    mutated = dict(silver_row)
    original = mutated[field]
    if isinstance(original, datetime):
        mutated[field] = original.replace(second=original.second + 1)
    elif isinstance(original, (int, float)):
        mutated[field] = original + 1
    else:
        mutated[field] = str(original) + "_mutated"
    assert derive_event_id(mutated) != base


def test_derive_event_id_raises_on_missing_field(silver_row: dict[str, Any]) -> None:
    """A missing required field raises :class:`KeyError`."""
    incomplete = {k: v for k, v in silver_row.items() if k != "VendorID"}
    with pytest.raises(KeyError) as info:
        derive_event_id(incomplete)
    assert "VendorID" in str(info.value)


def test_derive_event_id_handles_none_field(silver_row: dict[str, Any]) -> None:
    """``None`` is canonicalized to empty string and yields a valid id."""
    nulled = {**silver_row, "DOLocationID": None}
    eid = derive_event_id(nulled)
    assert len(eid) == EVENT_ID_HASH_TRUNCATION
    # Distinguishable from the all-present case.
    assert eid != derive_event_id(silver_row)


# --- event_key --------------------------------------------------------------


def test_event_key_format(valid_args: tuple) -> None:
    """The key has hour grain in ``cab_type/YYYY/MM/HH`` form."""
    event = TripCompleted.create_validated(*valid_args)
    assert event_key(event) == "yellow/2023/01/14"


def test_event_key_zero_pads_month(valid_args: tuple) -> None:
    """Single-digit months are zero-padded to two digits."""
    args = list(valid_args)
    args[4] = 9  # September
    event = TripCompleted.create_validated(*args)
    assert event_key(event) == "yellow/2023/09/14"


def test_event_key_zero_pads_hour(valid_args: tuple) -> None:
    """Single-digit hours are zero-padded to two digits."""
    args = list(valid_args)
    args[5] = 3
    event = TripCompleted.create_validated(*args)
    assert event_key(event).endswith("/03")


def test_event_key_midnight_hour(valid_args: tuple) -> None:
    """Hour 0 renders as ``00``."""
    args = list(valid_args)
    args[5] = 0
    event = TripCompleted.create_validated(*args)
    assert event_key(event).endswith("/00")


def test_event_key_stable_across_events_in_same_hour(valid_args: tuple) -> None:
    """Two events in the same hour bucket share a routing key."""
    e1 = TripCompleted.create_validated(*valid_args)
    args = list(valid_args)
    args[0] = "different_event_id"  # different event_id, same hour bucket
    e2 = TripCompleted.create_validated(*args)
    assert event_key(e1) == event_key(e2)


def test_event_key_differs_for_different_hours(valid_args: tuple) -> None:
    """Different hour buckets produce different keys."""
    e1 = TripCompleted.create_validated(*valid_args)
    args = list(valid_args)
    args[5] = (valid_args[5] + 1) % 24
    e2 = TripCompleted.create_validated(*args)
    assert event_key(e1) != event_key(e2)


# --- JSON serialization -----------------------------------------------------


def test_to_json_produces_valid_json(valid_args: tuple) -> None:
    """The output parses as JSON."""
    event = TripCompleted.create_validated(*valid_args)
    parsed = json.loads(to_json(event))
    assert isinstance(parsed, dict)


def test_to_json_contains_all_fields(valid_args: tuple) -> None:
    """Every contract field appears in the JSON payload."""
    event = TripCompleted.create_validated(*valid_args)
    parsed = json.loads(to_json(event))
    expected_keys = {
        "event_id", "schema_version", "cab_type", "year", "month", "hour",
        "trip_distance", "fare_amount", "passenger_count", "produced_at",
    }
    assert set(parsed.keys()) == expected_keys


def test_to_json_isoformats_produced_at(valid_args: tuple) -> None:
    """``produced_at`` is serialized as an ISO 8601 string with timezone."""
    event = TripCompleted.create_validated(*valid_args)
    parsed = json.loads(to_json(event))
    assert parsed["produced_at"] == event.produced_at.isoformat()
    # Sanity: contains timezone marker.
    assert "+" in parsed["produced_at"] or parsed["produced_at"].endswith("Z")


# --- JSON deserialization: happy path ---------------------------------------


def test_round_trip_preserves_all_fields(valid_args: tuple) -> None:
    """``from_json(to_json(event)) == event`` for every field."""
    event = TripCompleted.create_validated(*valid_args)
    assert from_json(to_json(event)) == event


def test_from_json_accepts_bytes(valid_args: tuple) -> None:
    """``from_json`` accepts UTF-8 bytes (as delivered by Kafka)."""
    event = TripCompleted.create_validated(*valid_args)
    payload = to_json(event).encode("utf-8")
    assert from_json(payload) == event


def test_from_json_accepts_non_utc_tzaware(valid_args: tuple) -> None:
    """Non-UTC tz-aware datetimes round-trip cleanly."""
    args = list(valid_args)
    args[9] = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone(timedelta(hours=-5)))
    event = TripCompleted.create_validated(*args)
    assert from_json(to_json(event)) == event


# --- JSON deserialization: malformed input ----------------------------------


def test_from_json_rejects_non_utf8_bytes() -> None:
    """Bytes that are not valid UTF-8 are rejected with the payload exception."""
    with pytest.raises(InvalidEventPayloadError, match="UTF-8"):
        from_json(b"\xff\xfe\xfd")


def test_from_json_rejects_non_json() -> None:
    """A non-JSON payload is rejected."""
    with pytest.raises(InvalidEventPayloadError, match="not valid JSON"):
        from_json("this is not json")


def test_from_json_rejects_non_object_root() -> None:
    """A JSON payload whose root is not an object is rejected."""
    with pytest.raises(InvalidEventPayloadError, match="root must be a JSON object"):
        from_json("[1, 2, 3]")


def test_from_json_rejects_scalar_root() -> None:
    """A scalar JSON payload is rejected."""
    with pytest.raises(InvalidEventPayloadError, match="root must be a JSON object"):
        from_json("42")


# --- JSON deserialization: structural payload errors ------------------------


@pytest.fixture
def valid_payload(valid_args: tuple) -> str:
    """A serialized form of a valid event, ready to mutate per test."""
    return to_json(TripCompleted.create_validated(*valid_args))


def test_from_json_rejects_missing_field(valid_payload: str) -> None:
    """A payload missing a required field is rejected."""
    data = json.loads(valid_payload)
    del data["fare_amount"]
    with pytest.raises(InvalidEventPayloadError, match="missing required fields"):
        from_json(json.dumps(data))


def test_from_json_rejects_extra_field(valid_payload: str) -> None:
    """A payload with an unexpected field is rejected — strict wire format."""
    data = json.loads(valid_payload)
    data["future_field"] = "not yet"
    with pytest.raises(InvalidEventPayloadError, match="unexpected fields"):
        from_json(json.dumps(data))


def test_from_json_rejects_wrong_schema_version(valid_payload: str) -> None:
    """A payload with a non-current ``schema_version`` is rejected with a clear message."""
    data = json.loads(valid_payload)
    data["schema_version"] = "2"
    with pytest.raises(InvalidEventPayloadError, match="unsupported schema_version"):
        from_json(json.dumps(data))


def test_from_json_rejects_naive_produced_at(valid_payload: str) -> None:
    """A naive datetime string (no timezone) is rejected."""
    data = json.loads(valid_payload)
    data["produced_at"] = "2025-01-15T12:00:00"  # no tz
    with pytest.raises(InvalidEventPayloadError, match="timezone-aware"):
        from_json(json.dumps(data))


def test_from_json_rejects_malformed_produced_at(valid_payload: str) -> None:
    """A non-ISO-8601 ``produced_at`` string is rejected."""
    data = json.loads(valid_payload)
    data["produced_at"] = "not a datetime"
    with pytest.raises(InvalidEventPayloadError, match="ISO 8601"):
        from_json(json.dumps(data))


def test_from_json_rejects_non_string_produced_at(valid_payload: str) -> None:
    """A non-string ``produced_at`` value is rejected."""
    data = json.loads(valid_payload)
    data["produced_at"] = 12345
    with pytest.raises(InvalidEventPayloadError, match="ISO 8601"):
        from_json(json.dumps(data))


# --- JSON deserialization: contract violations ------------------------------


def test_from_json_rejects_payload_that_fails_structural_check(valid_payload: str) -> None:
    """Payloads parseable as JSON but failing structural rules are quarantined."""
    data = json.loads(valid_payload)
    data["hour"] = 24  # out of [0, 23]
    with pytest.raises(InvalidEventPayloadError, match="fails contract validation"):
        from_json(json.dumps(data))


def test_from_json_rejects_payload_with_wrong_field_type(valid_payload: str) -> None:
    """Payloads with a wrong-typed field are caught by the wrapped type check."""
    data = json.loads(valid_payload)
    data["trip_distance"] = "three"
    with pytest.raises(InvalidEventPayloadError, match="fails contract validation"):
        from_json(json.dumps(data))


# --- Forward-compatibility self-check ---------------------------------------


def test_event_id_for_canonical_silver_row_is_stable(silver_row: dict[str, Any]) -> None:
    """Pin the canonical fixture's event_id to detect inadvertent hash changes.

    If this test starts failing without a corresponding wire-format major
    version bump, the hash inputs or formatter changed unexpectedly. The
    value below is the SHA-256-truncated-to-16 of the documented
    ``:``-joined formatter output for the ``silver_row`` fixture.
    """
    assert derive_event_id(silver_row) == "6dba7a395d25df4c"
