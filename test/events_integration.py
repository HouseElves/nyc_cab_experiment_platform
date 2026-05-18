# pylint: disable=redefined-outer-name,duplicate-code
# duplicate-code: the silver_row fixture is intentionally repeated across
# the events test files (decision 28: tolerate duplication until shared
# vocabulary is justified).
"""
End-to-end integration test for the trip-completed event bridge.

This test exercises the full loop in a single ``producer ->
consume_and_aggregate -> reconcile_against_silver`` flow:

    Silver accepted Parquet
        -> producer.trip_completed.produce_trip_completed_events
        -> Kafka topic trip.completed.v1
        -> consumer.trip_completed.consume_and_aggregate
        -> Postgres trip_completed_hourly
        -> sink.postgres.reconcile_against_silver

The expected end state is a :class:`ReconciliationResult` with
``is_reconciled is True`` for the slice, equal to the producer's
``events_emitted`` count. Phase A held four per-step tests here; the
consumer milestone (Phase B) collapses them into the one end-to-end
test below per the milestone's stated scope.

The test is marked ``kafka``, ``postgres``, and ``spark`` because it
needs all three services up: Kafka and Postgres via the project's
``docker-compose.yml``, and a local Spark session for the producer
driver.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from pathlib import Path

import pytest

from nyc_cab_events.consumer.trip_completed import (
    TripCompletedConsumerConfig,
    consume_and_aggregate,
)
from nyc_cab_events.producer.trip_completed import (
    TripCompletedProducerConfig,
    ensure_topics,
    produce_trip_completed_events,
)
from nyc_cab_events.sink.postgres import (
    TRIP_COMPLETED_HOURLY_TABLE,
    PostgresSinkConfig,
    _connect,
    ensure_table,
    reconcile_against_silver,
)


# All test functions in this module drive the full end-to-end loop
# and therefore need all three services up. Marking at the module
# level is safe because every test in the file is integration-tier.
pytestmark = [pytest.mark.kafka, pytest.mark.postgres, pytest.mark.spark]


# Environment-variable knobs for endpoints that vary per developer
# machine. Defaults match the project's docker-compose.yml so the
# standard "bring up compose, run pytest" workflow needs no
# environment setup; developers whose local Postgres or Kafka uses
# different credentials override the relevant variable.
_DEFAULT_KAFKA_BOOTSTRAP = "localhost:9092"
_DEFAULT_PG_HOST = "localhost"
_DEFAULT_PG_PORT = "5432"
_DEFAULT_PG_DATABASE = "nyc_cab_events"
_DEFAULT_PG_USER = "nyc_cab"
_DEFAULT_PG_PASSWORD = "nyc_cab"  # nosec B105 — docker-compose default; override via env


# --- Endpoint helpers -------------------------------------------------------


def _kafka_bootstrap() -> str:
    """Return the Kafka bootstrap-servers string for integration tests.

    Reads ``NYC_CAB_EVENTS_KAFKA_BOOTSTRAP`` if set, otherwise the
    docker-compose default. Lifted into a helper so producer and
    consumer fixtures cannot drift apart.
    """
    return os.environ.get("NYC_CAB_EVENTS_KAFKA_BOOTSTRAP", _DEFAULT_KAFKA_BOOTSTRAP)


def _pg_endpoint() -> tuple[str, int, str, str, str]:
    """Return Postgres endpoint tuple ``(host, port, database, user, password)``.

    Defaults match the project's docker-compose; each field is
    overridable via its ``NYC_CAB_EVENTS_PG_*`` environment variable.
    Lifted into a helper so the sink-config fixture and the
    pre-flight Postgres verifier read the same source of truth.
    """
    return (
        os.environ.get("NYC_CAB_EVENTS_PG_HOST", _DEFAULT_PG_HOST),
        int(os.environ.get("NYC_CAB_EVENTS_PG_PORT", _DEFAULT_PG_PORT)),
        os.environ.get("NYC_CAB_EVENTS_PG_DATABASE", _DEFAULT_PG_DATABASE),
        os.environ.get("NYC_CAB_EVENTS_PG_USER", _DEFAULT_PG_USER),
        os.environ.get("NYC_CAB_EVENTS_PG_PASSWORD", _DEFAULT_PG_PASSWORD),
    )


# --- Pre-flight diagnostics -------------------------------------------------


@pytest.fixture(scope="session")
def _verify_kafka_reachable() -> None:
    """Fail fast with a readable message if Kafka is unreachable.

    Probes the AdminClient's metadata endpoint with a short timeout.
    Collapses raw librdkafka stack traces (``_TIMED_OUT`` waiting for
    controller; bootstrap resolution failures; advertised-listener
    mismatches) into a setup-level message naming the bootstrap
    address and the override env var. Wired as an explicit dependency
    of :func:`producer_config` and :func:`consumer_config` rather than
    autouse so the probe fires only when a test in this module asks
    for a Kafka endpoint — never as a side effect of pytest
    collecting tests in other modules.
    """
    # pylint: disable=import-outside-toplevel,broad-except
    # Local import: AdminClient is only needed for the probe and the
    # producer imports it inside ensure_topics by the same convention.
    # Broad except: any failure to reach Kafka is a setup-level problem
    # that the developer wants surfaced in one readable line, not as
    # an uncaught librdkafka stack.
    from confluent_kafka.admin import AdminClient

    bootstrap = _kafka_bootstrap()
    try:
        AdminClient({"bootstrap.servers": bootstrap}).list_topics(timeout=3.0)
    except Exception as exc:
        pytest.fail(
            f"Kafka not reachable at {bootstrap}: {exc}. "
            f"Bring the broker up (docker compose up -d zookeeper kafka) "
            f"or set NYC_CAB_EVENTS_KAFKA_BOOTSTRAP to your local broker.",
            pytrace=False,
        )


@pytest.fixture(scope="session")
def _verify_postgres_reachable() -> None:
    """Fail fast with a readable message if Postgres is unreachable.

    Opens a connection with the same credentials the
    :func:`sink_config` fixture will use and closes it immediately.
    Collapses raw ``psycopg.OperationalError`` traces (auth failure,
    connection refused, connect timeout) into a setup-level message
    naming the endpoint, the connecting user, and the override env
    vars. Wired as an explicit dependency of :func:`sink_config`
    rather than autouse, for the same scope-discipline reason as
    :func:`_verify_kafka_reachable`.
    """
    # pylint: disable=import-outside-toplevel,broad-except
    # See _verify_kafka_reachable for rationale on the import locality
    # and the broad except.
    import psycopg

    host, port, database, user, password = _pg_endpoint()
    try:
        with psycopg.connect(
            host=host,
            port=port,
            dbname=database,
            user=user,
            password=password,
            connect_timeout=3,
        ):
            pass
    except Exception as exc:
        pytest.fail(
            f"Postgres not reachable at {host}:{port}/{database} as {user}: "
            f"{exc}. Start the database (see README) or override credentials "
            f"via NYC_CAB_EVENTS_PG_* env vars.",
            pytrace=False,
        )


# --- Fixtures ---------------------------------------------------------------


@pytest.fixture
def topic_suffix() -> str:
    """Unique suffix per test invocation for scratch Kafka topics.

    The integration test reads from offset 0 of the captured replay
    window per decision 42. Reusing a stable topic across runs lets
    prior events leak into the new run; dedup on ``event_id`` only
    helps if the prior runs emitted byte-identical events, and any
    same-slice event with a different ``event_id`` would poison the
    reconciliation. A fresh topic per run avoids that entire failure
    mode. Topics survive the test (no admin-side cleanup); local
    docker-compose teardown clears them, and CI brokers should be
    ephemeral.
    """
    return uuid.uuid4().hex[:8]


@pytest.fixture
def producer_config(
    _verify_kafka_reachable: None,
    topic_suffix: str,
) -> TripCompletedProducerConfig:
    """Producer config pointed at scratch Kafka topics for this run."""
    _ = _verify_kafka_reachable  # dependency-only: forces the probe to run
    return TripCompletedProducerConfig.create_validated(
        _kafka_bootstrap(),
        f"trip.completed.v1.integration.{topic_suffix}",
        f"trip.completed.v1.integration.{topic_suffix}.invalid",
    )


@pytest.fixture
def consumer_config(
    _verify_kafka_reachable: None,
    topic_suffix: str,
) -> TripCompletedConsumerConfig:
    """Consumer config pointed at the same scratch topic; scratch group too."""
    _ = _verify_kafka_reachable  # dependency-only: forces the probe to run
    return TripCompletedConsumerConfig.create_validated(
        _kafka_bootstrap(),
        f"nyc-cab-events-integration-{topic_suffix}",
        f"trip.completed.v1.integration.{topic_suffix}",
        5,
    )


@pytest.fixture
def sink_config(_verify_postgres_reachable: None) -> PostgresSinkConfig:
    """Sink config pointed at Postgres.

    Defaults match the project's docker-compose settings. Override
    individual fields via these environment variables when the local
    Postgres differs:

    - ``NYC_CAB_EVENTS_PG_HOST`` (default ``localhost``)
    - ``NYC_CAB_EVENTS_PG_PORT`` (default ``5432``)
    - ``NYC_CAB_EVENTS_PG_DATABASE`` (default ``nyc_cab_events``)
    - ``NYC_CAB_EVENTS_PG_USER`` (default ``nyc_cab``)
    - ``NYC_CAB_EVENTS_PG_PASSWORD`` (default ``nyc_cab``)
    """
    _ = _verify_postgres_reachable  # dependency-only: forces the probe to run
    return PostgresSinkConfig.create_validated(*_pg_endpoint())


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


# --- End-to-end test --------------------------------------------------------


def test_producer_through_reconcile(
    # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    # Six fixtures correspond to the six subsystems the test ties
    # together (producer, consumer, sink, table setup, scratch path,
    # shared Spark session); the local-variable count tracks the same
    # per-subsystem state.
    producer_config: TripCompletedProducerConfig,
    consumer_config: TripCompletedConsumerConfig,
    sink_config: PostgresSinkConfig,
    empty_aggregate_table,
    tmp_path: Path,
    spark,
) -> None:
    """Producer -> Kafka -> consumer -> Postgres -> reconcile loops cleanly.

    Builds a synthetic Silver partition with three rows (two valid +
    one contract-violating), runs the producer to emit events to a
    live Kafka broker, drains the broker via
    :func:`consume_and_aggregate` into Postgres, and reconciles the
    resulting hourly counts against the producer's
    ``events_emitted`` count.

    The expected reconciliation: ``silver_accepted_count`` is the
    producer's ``events_emitted`` (the count that actually made it
    through the contract), the Postgres sum matches, and
    ``is_reconciled`` is :data:`True`.

    Borrows the session-scoped ``spark`` fixture from
    :mod:`test.conftest` rather than building a local
    ``SparkSession`` here. ``SparkSession.builder.getOrCreate()``
    returns the existing process-wide singleton when one is active,
    and calling ``spark.stop()`` on the borrowed session would kill
    the JVM context that other integration tests (e.g. the silver_*
    suites) depend on. One owner for the Spark lifecycle — the
    fixture creates it, the fixture stops it.
    """
    _ = empty_aggregate_table  # fixture's setup/teardown is the contract

    ensure_topics(producer_config)
    ensure_table(sink_config)

    clean_row = {
        "VendorID": 1,
        "tpep_pickup_datetime": datetime(2023, 1, 15, 14, 30, 0),
        "tpep_dropoff_datetime": datetime(2023, 1, 15, 14, 55, 0),
        "PULocationID": 161,
        "DOLocationID": 236,
        "passenger_count": 2,
        "trip_distance": 3.5,
        "fare_amount": 18.0,
        "total_amount": 22.0,
    }
    other_row = {
        **clean_row,
        "tpep_pickup_datetime": datetime(2023, 1, 15, 17, 0, 0),
        "tpep_dropoff_datetime": datetime(2023, 1, 15, 17, 30, 0),
        "fare_amount": 22.0,
        "total_amount": 28.0,
    }
    bad_row = {**clean_row, "fare_amount": -1.0}
    partition_path = tmp_path / "silver"
    partition_path.mkdir(parents=True, exist_ok=True)
    spark.createDataFrame([clean_row, other_row, bad_row]).write.mode(
        "overwrite"
    ).parquet(str(partition_path))

    producer_result = produce_trip_completed_events(
        spark, partition_path, producer_config, "yellow", 2023, 1,
    )

    assert producer_result.silver_read_count == 3
    assert producer_result.events_emitted == 2
    assert producer_result.events_quarantined == 1
    assert (
        producer_result.silver_read_count
        == producer_result.events_emitted + producer_result.events_quarantined
    )

    consumer_result = consume_and_aggregate(
        consumer_config, sink_config, "yellow", 2023, 1,
    )
    assert consumer_result.events_in_slice == producer_result.events_emitted
    assert consumer_result.events_unique == producer_result.events_emitted
    assert consumer_result.hourly_buckets_written == 2  # two distinct pickup hours

    reconciliation = reconcile_against_silver(
        sink_config, "yellow", 2023, 1, producer_result.events_emitted,
    )
    assert reconciliation.is_reconciled is True
    assert reconciliation.postgres_event_count == producer_result.events_emitted
