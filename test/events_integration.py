# pylint: disable=redefined-outer-name,duplicate-code
# duplicate-code: the silver_row fixture is intentionally repeated across
# the events test files (decision 28: tolerate duplication until shared
# vocabulary is justified).
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
from nyc_cab_events.producer.trip_completed import TripCompletedProducerConfig
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


def test_producer_step_emits_events_to_kafka(
    producer_config: TripCompletedProducerConfig, tmp_path,
) -> None:
    """The producer step writes events to a live Kafka broker.

    Requires docker-compose up. Builds a synthetic Silver partition with
    two clean rows and one bad row, runs the producer, and confirms the
    returned result satisfies the reconciliation invariant.
    """
    # pylint: disable=import-outside-toplevel
    from datetime import datetime as _dt
    from pyspark.sql import SparkSession
    from nyc_cab_events.producer.trip_completed import produce_trip_completed_events

    spark = (
        SparkSession.builder
        .appName("nyc_cab_events_integration_producer")
        .master("local[1]")
        .config("spark.sql.shuffle.partitions", "1")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )
    try:
        clean_row = {
            "VendorID": 1,
            "tpep_pickup_datetime": _dt(2023, 1, 15, 14, 30, 0),
            "tpep_dropoff_datetime": _dt(2023, 1, 15, 14, 55, 0),
            "PULocationID": 161,
            "DOLocationID": 236,
            "passenger_count": 2,
            "trip_distance": 3.5,
            "fare_amount": 18.0,
            "total_amount": 22.0,
        }
        bad_row = {**clean_row, "fare_amount": -1.0}
        partition_path = tmp_path / "silver"
        partition_path.mkdir(parents=True, exist_ok=True)
        spark.createDataFrame([clean_row, clean_row, bad_row]).write.mode("overwrite").parquet(str(partition_path))

        result = produce_trip_completed_events(
            spark, partition_path, producer_config, "yellow", 2023, 1,
        )
        assert result.silver_read_count == 3
        assert result.events_emitted == 2
        assert result.events_quarantined == 1
        assert result.silver_read_count == result.events_emitted + result.events_quarantined
    finally:
        spark.stop()


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
