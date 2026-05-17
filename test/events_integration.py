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

At present three of the four steps are real (ensure_table, producer,
reconcile) and the consumer step remains a ``NotImplementedError`` stub
guarded by a ``pytest.raises`` test per design log decision 25. When
the consumer milestone lands, these per-step tests collapse into one
end-to-end ``test_producer_through_reconcile`` test that drives the
full loop.
"""

from __future__ import annotations

import pytest

from nyc_cab_events.consumer.trip_completed import (
    TripCompletedConsumerConfig,
    consume_and_aggregate,
)
from nyc_cab_events.producer.trip_completed import TripCompletedProducerConfig
from nyc_cab_events.sink.postgres import (
    TRIP_COMPLETED_HOURLY_TABLE,
    PostgresSinkConfig,
    _connect,
    ensure_table,
    reconcile_against_silver,
)


# Three of four steps are real (ensure_table, producer, reconcile) and
# require their respective services up; the consumer step remains a
# NotImplementedError stub per design log decision 25. All four tests
# are marked kafka and postgres so the module's marker contract is
# stable when Phase B collapses these into one end-to-end test.
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
    )


@pytest.fixture
def sink_config() -> PostgresSinkConfig:
    """Sink config pointed at the docker-compose Postgres instance."""
    return PostgresSinkConfig.create_validated(
        "localhost", 5432, "nyc_cab_events", "nyc_cab", "nyc_cab",
    )


@pytest.fixture
def empty_aggregate_table(sink_config: PostgresSinkConfig):
    """Yield with ``trip_completed_hourly`` present and empty.

    Ensures the table exists, truncates it before the test, and truncates
    again after. Required by tests that assert on the table's contents
    (e.g. an empty-slice reconciliation result), since other tests in
    this module or others may have left rows behind. Mirrors the
    ``clean_table`` fixture in :mod:`nyc_cab_events.sink.test.postgres`;
    duplication is tolerated under decision 28 until a shared-vocabulary
    conftest is justified.
    """
    ensure_table(sink_config)
    with _connect(sink_config) as conn:
        with conn.cursor() as cur:
            cur.execute(f"TRUNCATE TABLE {TRIP_COMPLETED_HOURLY_TABLE}")
        conn.commit()
    yield
    with _connect(sink_config) as conn:
        with conn.cursor() as cur:
            cur.execute(f"TRUNCATE TABLE {TRIP_COMPLETED_HOURLY_TABLE}")
        conn.commit()


# --- Per-step integration coverage ------------------------------------------


def test_ensure_table_step_is_idempotent(sink_config: PostgresSinkConfig) -> None:
    """The first step of the integration loop creates the aggregate table idempotently.

    Requires docker-compose up. Two calls back-to-back should both
    succeed; the second is a no-op via ``IF NOT EXISTS``.
    """
    ensure_table(sink_config)
    ensure_table(sink_config)


def test_producer_step_emits_events_to_kafka(
    producer_config: TripCompletedProducerConfig, tmp_path,
) -> None:
    """The producer step writes events to a live Kafka broker.

    Requires docker-compose up. Calls :func:`ensure_topics` first because
    the docker-compose Kafka broker has auto-topic-creation disabled
    (intentionally — production brokers typically do too); without this
    setup step the produce calls would fail against a freshly-started
    broker. Then builds a synthetic Silver partition with two clean rows
    and one bad row, runs the producer, and confirms the returned result
    satisfies the reconciliation invariant.
    """
    # pylint: disable=import-outside-toplevel
    from datetime import datetime as _dt
    from pyspark.sql import SparkSession
    from nyc_cab_events.producer.trip_completed import (
        ensure_topics,
        produce_trip_completed_events,
    )

    ensure_topics(producer_config)

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
        consume_and_aggregate(
            consumer_config=consumer_config,
            sink_config=sink_config,
            cab_type="yellow",
            year=2023,
            month=1,
        )


def test_reconcile_step_returns_validated_result(
    sink_config: PostgresSinkConfig, empty_aggregate_table,
) -> None:
    """The reconciliation step queries the sink and returns a validated result.

    Requires docker-compose up. The ``empty_aggregate_table`` fixture
    truncates ``trip_completed_hourly`` before the test runs so the
    slice is guaranteed empty regardless of other tests in this module
    or earlier runs. Reconciles the (yellow, 2023, 1) slice against a
    Silver count of zero; both sides agree, so the result is reconciled.
    The full-loop variant of this assertion lands when the consumer
    milestone collapses these per-step tests into one end-to-end test.
    """
    _ = empty_aggregate_table  # fixture's setup/teardown is the contract
    result = reconcile_against_silver(sink_config, "yellow", 2023, 1, 0)
    assert result.is_reconciled is True
    assert result.postgres_event_count == 0
