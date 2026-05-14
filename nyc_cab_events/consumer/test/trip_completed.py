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
        "localhost:9092", "nyc-cab-events-test", TRIP_COMPLETED_TOPIC, 5, 3,
    )
    assert config.bootstrap_servers == "localhost:9092"
    assert config.group_id == "nyc-cab-events-test"
    assert config.topic == TRIP_COMPLETED_TOPIC
    assert config.poll_timeout_seconds == 5
    assert config.max_idle_polls == 3


def test_config_defaults() -> None:
    """Direct construction with defaults targets the v1 topic with sane idle bounds."""
    config = TripCompletedConsumerConfig(
        bootstrap_servers="localhost:9092",
        group_id="g",
    )
    assert config.topic == TRIP_COMPLETED_TOPIC
    assert config.poll_timeout_seconds == 5
    assert config.max_idle_polls == 3


# --- TripCompletedConsumerConfig: type rejections ---------------------------


def test_config_rejects_non_string_bootstrap() -> None:
    """``bootstrap_servers`` must be a string."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerConfig.create_validated(0, "g", "t", 5, 3)
    names = [v[0] for v in info.value.violations]
    assert "bootstrap_servers" in names


def test_config_rejects_non_string_group_id() -> None:
    """``group_id`` must be a string."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerConfig.create_validated("localhost:9092", 0, "t", 5, 3)
    names = [v[0] for v in info.value.violations]
    assert "group_id" in names


def test_config_rejects_bool_poll_timeout() -> None:
    """``poll_timeout_seconds`` rejects bool despite int compatibility."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerConfig.create_validated("localhost:9092", "g", "t", True, 3)
    assert ("poll_timeout_seconds", True) in info.value.violations


def test_config_rejects_bool_max_idle_polls() -> None:
    """``max_idle_polls`` rejects bool despite int compatibility."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerConfig.create_validated("localhost:9092", "g", "t", 5, False)
    assert ("max_idle_polls", False) in info.value.violations


# --- TripCompletedConsumerConfig: structural rejections ---------------------


def test_config_rejects_blank_bootstrap_servers() -> None:
    """Blank bootstrap_servers violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerConfig.create_validated("   ", "g", "t", 5, 3)
    assert ("bootstrap_servers", "   ") in info.value.violations


def test_config_rejects_blank_group_id() -> None:
    """Blank group_id violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerConfig.create_validated("localhost:9092", "", "t", 5, 3)
    assert ("group_id", "") in info.value.violations


def test_config_rejects_blank_topic() -> None:
    """Blank topic violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerConfig.create_validated("localhost:9092", "g", "", 5, 3)
    assert ("topic", "") in info.value.violations


def test_config_rejects_zero_poll_timeout() -> None:
    """``poll_timeout_seconds`` must be positive."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerConfig.create_validated("localhost:9092", "g", "t", 0, 3)
    assert ("poll_timeout_seconds", 0) in info.value.violations


def test_config_rejects_negative_max_idle_polls() -> None:
    """``max_idle_polls`` must be positive."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerConfig.create_validated("localhost:9092", "g", "t", 5, -1)
    assert ("max_idle_polls", -1) in info.value.violations


def test_config_is_frozen() -> None:
    """``TripCompletedConsumerConfig`` rejects attribute mutation."""
    config = TripCompletedConsumerConfig.create_validated("localhost:9092", "g", "t", 5, 3)
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.group_id = "other"  # type: ignore[misc]


# --- TripCompletedConsumerResult: happy paths -------------------------------


def test_result_happy_path() -> None:
    """A well-formed result constructs cleanly."""
    result = TripCompletedConsumerResult.create_validated(1000, 24)
    assert result.events_consumed == 1000
    assert result.hourly_buckets_written == 24


def test_result_zero_run() -> None:
    """A run that consumed no events is valid (drained-already case)."""
    result = TripCompletedConsumerResult.create_validated(0, 0)
    assert result.events_consumed == 0


# --- TripCompletedConsumerResult: rejections --------------------------------


def test_result_rejects_bool_events_consumed() -> None:
    """``events_consumed`` rejects bool despite int compatibility."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerResult.create_validated(True, 0)
    assert ("events_consumed", True) in info.value.violations


def test_result_rejects_negative_events_consumed() -> None:
    """Negative ``events_consumed`` violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerResult.create_validated(-1, 0)
    assert ("events_consumed", -1) in info.value.violations


def test_result_rejects_buckets_exceeding_events() -> None:
    """``hourly_buckets_written`` cannot exceed ``events_consumed``."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerResult.create_validated(5, 6)
    names = [v[0] for v in info.value.violations]
    assert "hourly_buckets_written" in names


def test_result_is_frozen() -> None:
    """``TripCompletedConsumerResult`` rejects attribute mutation."""
    result = TripCompletedConsumerResult.create_validated(0, 0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.events_consumed = 1  # type: ignore[misc]


# --- Stub coverage (decision 25) --------------------------------------------


def test_consume_and_aggregate_is_not_implemented() -> None:
    """``consume_and_aggregate`` raises ``NotImplementedError`` until implemented."""
    consumer_config = TripCompletedConsumerConfig.create_validated(
        "localhost:9092", "g", TRIP_COMPLETED_TOPIC, 5, 3,
    )
    with pytest.raises(NotImplementedError):
        consume_and_aggregate(consumer_config=consumer_config, sink_config=object())
