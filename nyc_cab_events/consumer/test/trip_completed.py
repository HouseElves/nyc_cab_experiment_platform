# pylint: disable=redefined-outer-name
"""Tests for :mod:`nyc_cab_events.consumer.trip_completed`."""

from __future__ import annotations

import dataclasses

import pytest

from nyc_cab.exceptions import InvalidRequestError
from nyc_cab_events.contracts.events import TRIP_COMPLETED_TOPIC
from nyc_cab_events.consumer.trip_completed import (
    TripCompletedConsumerConfig,
    TripCompletedConsumerResult,
    consume_and_aggregate,
)

pytestmark = pytest.mark.unit


# --- TripCompletedConsumerConfig: happy paths -------------------------------


def test_config_happy_path() -> None:
    """A well-formed config constructs cleanly."""
    config = TripCompletedConsumerConfig.create_validated(
        "localhost:9092", "nyc-cab-events-test", TRIP_COMPLETED_TOPIC, 5,
    )
    assert config.bootstrap_servers == "localhost:9092"
    assert config.group_id == "nyc-cab-events-test"
    assert config.topic == TRIP_COMPLETED_TOPIC
    assert config.poll_timeout_seconds == 5


def test_config_defaults() -> None:
    """Direct construction with defaults targets the v1 topic with a sane poll timeout."""
    config = TripCompletedConsumerConfig(
        bootstrap_servers="localhost:9092",
        group_id="g",
    )
    assert config.topic == TRIP_COMPLETED_TOPIC
    assert config.poll_timeout_seconds == 5


# --- TripCompletedConsumerConfig: type rejections ---------------------------


def test_config_rejects_non_string_bootstrap() -> None:
    """``bootstrap_servers`` must be a string."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerConfig.create_validated(0, "g", "t", 5)
    names = [v[0] for v in info.value.violations]
    assert "bootstrap_servers" in names


def test_config_rejects_non_string_group_id() -> None:
    """``group_id`` must be a string."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerConfig.create_validated("localhost:9092", 0, "t", 5)
    names = [v[0] for v in info.value.violations]
    assert "group_id" in names


def test_config_rejects_bool_poll_timeout() -> None:
    """``poll_timeout_seconds`` rejects bool despite int compatibility."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerConfig.create_validated("localhost:9092", "g", "t", True)
    assert ("poll_timeout_seconds", True) in info.value.violations


# --- TripCompletedConsumerConfig: structural rejections ---------------------


def test_config_rejects_blank_bootstrap_servers() -> None:
    """Blank bootstrap_servers violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerConfig.create_validated("   ", "g", "t", 5)
    assert ("bootstrap_servers", "   ") in info.value.violations


def test_config_rejects_blank_group_id() -> None:
    """Blank group_id violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerConfig.create_validated("localhost:9092", "", "t", 5)
    assert ("group_id", "") in info.value.violations


def test_config_rejects_blank_topic() -> None:
    """Blank topic violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerConfig.create_validated("localhost:9092", "g", "", 5)
    assert ("topic", "") in info.value.violations


def test_config_rejects_zero_poll_timeout() -> None:
    """``poll_timeout_seconds`` must be positive."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerConfig.create_validated("localhost:9092", "g", "t", 0)
    assert ("poll_timeout_seconds", 0) in info.value.violations


def test_config_is_frozen() -> None:
    """``TripCompletedConsumerConfig`` rejects attribute mutation."""
    config = TripCompletedConsumerConfig.create_validated("localhost:9092", "g", "t", 5)
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.group_id = "other"  # type: ignore[misc]


# --- TripCompletedConsumerResult: happy paths -------------------------------


def test_result_happy_path() -> None:
    """A well-formed result constructs cleanly with the monotonic counters in order."""
    result = TripCompletedConsumerResult.create_validated(
        "yellow", 2023, 1, 1000, 950, 900, 24,
    )
    assert result.cab_type == "yellow"
    assert result.year == 2023
    assert result.month == 1
    assert result.events_read == 1000
    assert result.events_in_slice == 950
    assert result.events_unique == 900
    assert result.hourly_buckets_written == 24


def test_result_zero_run() -> None:
    """A run that consumed no in-slice events is valid (empty replay window)."""
    result = TripCompletedConsumerResult.create_validated(
        "yellow", 2023, 1, 0, 0, 0, 0,
    )
    assert result.events_read == 0


def test_result_allows_all_events_unique_and_one_per_bucket() -> None:
    """A run where every unique event maps to its own hour bucket is valid."""
    result = TripCompletedConsumerResult.create_validated(
        "yellow", 2023, 1, 24, 24, 24, 24,
    )
    assert result.hourly_buckets_written == result.events_unique


# --- TripCompletedConsumerResult: type rejections ---------------------------


def test_result_rejects_bool_year() -> None:
    """``year`` rejects bool despite int compatibility."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerResult.create_validated(
            "yellow", True, 1, 0, 0, 0, 0,
        )
    assert ("year", True) in info.value.violations


def test_result_rejects_bool_events_read() -> None:
    """``events_read`` rejects bool despite int compatibility."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerResult.create_validated(
            "yellow", 2023, 1, True, 0, 0, 0,
        )
    assert ("events_read", True) in info.value.violations


# --- TripCompletedConsumerResult: structural rejections ---------------------


def test_result_rejects_blank_cab_type() -> None:
    """Blank ``cab_type`` violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerResult.create_validated(
            "  ", 2023, 1, 0, 0, 0, 0,
        )
    names = [v[0] for v in info.value.violations]
    assert "cab_type" in names


def test_result_rejects_negative_events_read() -> None:
    """Negative ``events_read`` violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerResult.create_validated(
            "yellow", 2023, 1, -1, 0, 0, 0,
        )
    assert ("events_read", -1) in info.value.violations


def test_result_rejects_in_slice_exceeding_read() -> None:
    """``events_in_slice`` cannot exceed ``events_read`` — filtering is monotonic."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerResult.create_validated(
            "yellow", 2023, 1, 10, 11, 0, 0,
        )
    names = [v[0] for v in info.value.violations]
    assert "events_in_slice" in names


def test_result_rejects_unique_exceeding_in_slice() -> None:
    """``events_unique`` cannot exceed ``events_in_slice`` — dedup is monotonic."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerResult.create_validated(
            "yellow", 2023, 1, 100, 50, 51, 0,
        )
    names = [v[0] for v in info.value.violations]
    assert "events_unique" in names


def test_result_rejects_buckets_exceeding_unique() -> None:
    """``hourly_buckets_written`` cannot exceed ``events_unique``."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerResult.create_validated(
            "yellow", 2023, 1, 100, 100, 5, 6,
        )
    names = [v[0] for v in info.value.violations]
    assert "hourly_buckets_written" in names


def test_result_is_frozen() -> None:
    """``TripCompletedConsumerResult`` rejects attribute mutation."""
    result = TripCompletedConsumerResult.create_validated(
        "yellow", 2023, 1, 0, 0, 0, 0,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.events_read = 1  # type: ignore[misc]


# --- Stub coverage (decision 25) --------------------------------------------


def test_consume_and_aggregate_is_not_implemented() -> None:
    """``consume_and_aggregate`` raises ``NotImplementedError`` until Phase B."""
    consumer_config = TripCompletedConsumerConfig.create_validated(
        "localhost:9092", "g", TRIP_COMPLETED_TOPIC, 5,
    )
    with pytest.raises(NotImplementedError):
        consume_and_aggregate(
            consumer_config=consumer_config,
            sink_config=object(),
            cab_type="yellow",
            year=2023,
            month=1,
        )
