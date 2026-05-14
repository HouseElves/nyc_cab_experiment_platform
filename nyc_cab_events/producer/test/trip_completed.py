# pylint: disable=redefined-outer-name
"""Tests for :mod:`nyc_cab_events.producer.trip_completed`."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from nyc_cab.exceptions import InvalidRequestError
from nyc_cab_events.contracts.events import (
    TRIP_COMPLETED_QUARANTINE_TOPIC,
    TRIP_COMPLETED_TOPIC,
)
from nyc_cab_events.producer.trip_completed import (
    TripCompletedProducerConfig,
    TripCompletedProducerResult,
    derive_event_id,
    produce_trip_completed_events,
)

pytestmark = pytest.mark.unit


# --- TripCompletedProducerConfig: happy paths -------------------------------


def test_config_happy_path() -> None:
    """A well-formed config constructs cleanly."""
    config = TripCompletedProducerConfig.create_validated(
        "localhost:9092",
        TRIP_COMPLETED_TOPIC,
        TRIP_COMPLETED_QUARANTINE_TOPIC,
    )
    assert config.bootstrap_servers == "localhost:9092"
    assert config.topic == TRIP_COMPLETED_TOPIC
    assert config.quarantine_topic == TRIP_COMPLETED_QUARANTINE_TOPIC


def test_config_defaults_to_v1_topics() -> None:
    """Direct construction with defaults uses the v1 contract topic names."""
    config = TripCompletedProducerConfig(bootstrap_servers="localhost:9092")
    assert config.topic == TRIP_COMPLETED_TOPIC
    assert config.quarantine_topic == TRIP_COMPLETED_QUARANTINE_TOPIC


# --- TripCompletedProducerConfig: type rejections ---------------------------


def test_config_rejects_non_string_bootstrap() -> None:
    """``bootstrap_servers`` must be a string."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedProducerConfig.create_validated(9092, "t", "q")
    names = [v[0] for v in info.value.violations]
    assert "bootstrap_servers" in names


def test_config_rejects_non_string_topic() -> None:
    """``topic`` must be a string."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedProducerConfig.create_validated("localhost:9092", 0, "q")
    names = [v[0] for v in info.value.violations]
    assert "topic" in names


def test_config_rejects_non_string_quarantine_topic() -> None:
    """``quarantine_topic`` must be a string."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedProducerConfig.create_validated("localhost:9092", "t", None)
    names = [v[0] for v in info.value.violations]
    assert "quarantine_topic" in names


# --- TripCompletedProducerConfig: structural rejections ---------------------


def test_config_rejects_blank_bootstrap_servers() -> None:
    """Blank bootstrap_servers violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedProducerConfig.create_validated("   ", "t", "q")
    assert ("bootstrap_servers", "   ") in info.value.violations


def test_config_rejects_blank_topic() -> None:
    """Blank topic violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedProducerConfig.create_validated("localhost:9092", "", "q")
    assert ("topic", "") in info.value.violations


def test_config_rejects_blank_quarantine_topic() -> None:
    """Blank quarantine_topic violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedProducerConfig.create_validated("localhost:9092", "t", "")
    assert ("quarantine_topic", "") in info.value.violations


def test_config_rejects_topic_equals_quarantine_topic() -> None:
    """The two topic fields must differ — otherwise quarantine routing collapses."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedProducerConfig.create_validated("localhost:9092", "same", "same")
    names = [v[0] for v in info.value.violations]
    assert "quarantine_topic" in names


def test_config_is_frozen() -> None:
    """``TripCompletedProducerConfig`` rejects attribute mutation."""
    config = TripCompletedProducerConfig.create_validated("localhost:9092", "t", "q")
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.topic = "other"  # type: ignore[misc]


# --- TripCompletedProducerResult: happy paths -------------------------------


@pytest.fixture
def silver_path(tmp_path: Path) -> Path:
    """A directory path that survives the silver_partition_path structural check."""
    partition = tmp_path / "cab_type=yellow" / "year=2023" / "month=1"
    partition.mkdir(parents=True)
    return partition


def test_result_happy_path(silver_path: Path) -> None:
    """A well-formed result satisfies the reconciliation invariant."""
    result = TripCompletedProducerResult.create_validated(
        "yellow", 2023, 1, silver_path, 1000, 950, 50,
    )
    assert result.silver_read_count == 1000
    assert result.events_emitted == 950
    assert result.events_quarantined == 50


def test_result_happy_path_zero_quarantine(silver_path: Path) -> None:
    """A run with no quarantined events is valid."""
    result = TripCompletedProducerResult.create_validated(
        "yellow", 2023, 1, silver_path, 1000, 1000, 0,
    )
    assert result.events_quarantined == 0


def test_result_happy_path_empty_partition(silver_path: Path) -> None:
    """A zero-row partition produces a zero-event result."""
    result = TripCompletedProducerResult.create_validated(
        "yellow", 2023, 1, silver_path, 0, 0, 0,
    )
    assert result.silver_read_count == 0


# --- TripCompletedProducerResult: type rejections ---------------------------


def test_result_rejects_bool_year(silver_path: Path) -> None:
    """``year`` rejects bool despite int compatibility."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedProducerResult.create_validated(
            "yellow", True, 1, silver_path, 1, 1, 0,
        )
    assert ("year", True) in info.value.violations


def test_result_rejects_bool_month(silver_path: Path) -> None:
    """``month`` rejects bool despite int compatibility."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedProducerResult.create_validated(
            "yellow", 2023, True, silver_path, 1, 1, 0,
        )
    assert ("month", True) in info.value.violations


def test_result_rejects_bool_silver_read_count(silver_path: Path) -> None:
    """``silver_read_count`` rejects bool despite int compatibility."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedProducerResult.create_validated(
            "yellow", 2023, 1, silver_path, True, 1, 0,
        )
    assert ("silver_read_count", True) in info.value.violations


def test_result_rejects_non_path_silver_partition_path() -> None:
    """``silver_partition_path`` must be a :class:`Path` instance."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedProducerResult.create_validated(
            "yellow", 2023, 1, "/tmp/x", 0, 0, 0,
        )
    names = [v[0] for v in info.value.violations]
    assert "silver_partition_path" in names


# --- TripCompletedProducerResult: structural rejections ---------------------


def test_result_rejects_negative_emitted(silver_path: Path) -> None:
    """Negative ``events_emitted`` violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedProducerResult.create_validated(
            "yellow", 2023, 1, silver_path, 0, -1, 1,
        )
    assert ("events_emitted", -1) in info.value.violations


def test_result_rejects_reconciliation_mismatch(silver_path: Path) -> None:
    """The reconciliation invariant is enforced at construction time."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedProducerResult.create_validated(
            "yellow", 2023, 1, silver_path, 100, 50, 49,  # 50 + 49 != 100
        )
    names = [v[0] for v in info.value.violations]
    assert "reconciliation" in names


def test_result_rejects_silver_partition_path_pointing_at_file(tmp_path: Path) -> None:
    """``silver_partition_path`` must not refer to a regular file."""
    file_path = tmp_path / "not_a_dir"
    file_path.write_text("placeholder")
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedProducerResult.create_validated(
            "yellow", 2023, 1, file_path, 0, 0, 0,
        )
    names = [v[0] for v in info.value.violations]
    assert "silver_partition_path" in names


def test_result_accepts_nonexistent_silver_partition_path(tmp_path: Path) -> None:
    """A not-yet-materialized partition path is allowed (mirrors SilverTransformResult)."""
    missing = tmp_path / "does" / "not" / "exist"
    result = TripCompletedProducerResult.create_validated(
        "yellow", 2023, 1, missing, 0, 0, 0,
    )
    assert result.silver_partition_path == missing


def test_result_is_frozen(silver_path: Path) -> None:
    """``TripCompletedProducerResult`` rejects attribute mutation."""
    result = TripCompletedProducerResult.create_validated(
        "yellow", 2023, 1, silver_path, 0, 0, 0,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.events_emitted = 1  # type: ignore[misc]


# --- Stub coverage (decision 25) --------------------------------------------


def test_derive_event_id_is_not_implemented() -> None:
    """``derive_event_id`` raises ``NotImplementedError`` until implemented."""
    with pytest.raises(NotImplementedError):
        derive_event_id(object())


def test_produce_trip_completed_events_is_not_implemented(silver_path: Path) -> None:
    """``produce_trip_completed_events`` raises ``NotImplementedError`` until implemented."""
    config = TripCompletedProducerConfig.create_validated(
        "localhost:9092", TRIP_COMPLETED_TOPIC, TRIP_COMPLETED_QUARANTINE_TOPIC,
    )
    with pytest.raises(NotImplementedError):
        produce_trip_completed_events(spark=None, silver_partition_path=silver_path, producer_config=config)
