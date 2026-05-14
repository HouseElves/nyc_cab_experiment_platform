# pylint: disable=redefined-outer-name
"""
End-to-end integration test for the trip-completed event bridge.

This test exercises the full loop:

    Silver accepted Parquet
        → producer.trip_completed.produce_trip_completed_events
        → Kafka topic trip.completed.v1
        → consumer.trip_completed.consume_and_aggregate
        → Postgres trip_completed_hourly
        → sink.postgres.reconcile_against_silver

The expected end state is a :class:`ReconciliationResult` with
``is_reconciled is True`` for the slice. The test is marked ``kafka`` and
``postgres`` because it needs both services up (via the project's
``docker-compose.yml``).

At scaffolding stage every step is a ``NotImplementedError`` stub. This
file's *current* role is to fail loudly the moment any of those stubs is
silently replaced without a real test taking its place (design log
decision 25). When the stubs land, this test is rewritten to drive the
real loop and re-marked appropriately.
"""

from __future__ import annotations

import pytest

from nyc_cab_events.consumer.trip_completed import (
    TripCompletedConsumerConfig,
    consume_and_aggregate,
)
from nyc_cab_events.producer.trip_completed import (
    TripCompletedProducerConfig,
    produce_trip_completed_events,
)
from nyc_cab_events.sink.postgres import (
    PostgresSinkConfig,
    ensure_table,
    reconcile_against_silver,
)


# Markers reflect what the *implemented* test will need. The current bodies
# are unit-level stub assertions; the markers are forward-looking so the
# integration marker contract is stable when the stubs are filled in.
pytestmark = [pytest.mark.kafka, pytest.mark.postgres]


# --- Fixtures ---------------------------------------------------------------


@pytest.fixture
def producer_config() -> TripCompletedProducerConfig:
    """Producer config pointed at the docker-compose Kafka broker."""
    return TripCompletedProducerConfig.create_validated(
        "localhost:9092",
        "trip.completed.v1",
        "trip.completed.v1.invalid",
    )


@pytest.fixture
def consumer_config() -> TripCompletedConsumerConfig:
    """Consumer config pointed at the docker-compose Kafka broker."""
    return TripCompletedConsumerConfig.create_validated(
        "localhost:9092",
        "nyc-cab-events-integration",
        "trip.completed.v1",
        5,
        3,
    )


@pytest.fixture
def sink_config() -> PostgresSinkConfig:
    """Sink config pointed at the docker-compose Postgres instance."""
    return PostgresSinkConfig.create_validated(
        "localhost", 5432, "nyc_cab_events", "nyc_cab", "nyc_cab",
    )


# --- Stub coverage (decision 25) --------------------------------------------


def test_ensure_table_step_is_not_implemented(sink_config: PostgresSinkConfig) -> None:
    """The first step of the integration loop (table creation) is a stub."""
    with pytest.raises(NotImplementedError):
        ensure_table(sink_config)


def test_producer_step_is_not_implemented(producer_config: TripCompletedProducerConfig, tmp_path) -> None:
    """The producer step of the integration loop is a stub."""
    silver_partition_path = tmp_path / "cab_type=yellow" / "year=2023" / "month=1"
    silver_partition_path.mkdir(parents=True)
    with pytest.raises(NotImplementedError):
        produce_trip_completed_events(
            spark=None,
            silver_partition_path=silver_partition_path,
            producer_config=producer_config,
        )


def test_consumer_step_is_not_implemented(
    consumer_config: TripCompletedConsumerConfig, sink_config: PostgresSinkConfig,
) -> None:
    """The consumer-aggregate-sink step of the integration loop is a stub."""
    with pytest.raises(NotImplementedError):
        consume_and_aggregate(consumer_config=consumer_config, sink_config=sink_config)


def test_reconcile_step_is_not_implemented(sink_config: PostgresSinkConfig) -> None:
    """The reconciliation step of the integration loop is a stub."""
    with pytest.raises(NotImplementedError):
        reconcile_against_silver(sink_config, "yellow", 2023, 1, 0)
