"""
Trip-completed event producer.

Reads a Silver accepted Parquet partition and emits one
:class:`~nyc_cab_events.contracts.events.TripCompleted` event per accepted row
to Kafka. See the package ``__init__`` for the full flow diagram.

The implementation is layered:

- :func:`_build_event_from_silver_row` — pure function. Builds one
  ``TripCompleted`` from one Silver row dict plus slice metadata plus a
  ``produced_at`` instant. Raises :class:`KeyError` on missing source fields
  and :class:`~nyc_cab.exceptions.InvalidRequestError` on contract
  violations.
- :func:`_route_silver_row` — pure function. Wraps
  ``_build_event_from_silver_row`` and returns a
  :class:`_RoutedMessage` describing where the row goes (primary topic
  or quarantine), with the serialized bytes and Kafka headers prepared.
- :func:`_make_kafka_producer` — factory returning a configured
  ``confluent_kafka.Producer``. Module-level so tests can monkeypatch it
  to inject a recording fake.
- :func:`produce_trip_completed_events` — driver. Reads the Parquet
  partition with Spark, streams rows via :meth:`DataFrame.toLocalIterator`
  to bound driver memory, calls ``_route_silver_row`` per row, emits via
  the producer, and returns a :class:`TripCompletedProducerResult` whose
  reconciliation invariant proves no rows were lost.

The driver uses ``toLocalIterator`` rather than ``foreachPartition``
because the per-row work is small (one hash, one dataclass construction,
one JSON dump) and the production NYC monthly volume (~3M rows) fits in a
single-threaded driver stream without memory pressure. ``foreachPartition``
is the right refactor when the per-event work becomes heavier or when
parallelism needs to be horizontal across executors.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar, Mapping

from confluent_kafka import KafkaException, Producer
from pyspark.sql import SparkSession

from nyc_cab._validation import _Validated, CheckSpec, CheckTuple
from nyc_cab.exceptions import InvalidRequestError
from nyc_cab_events.contracts.events import (
    SCHEMA_VERSION,
    TRIP_COMPLETED_QUARANTINE_TOPIC,
    TRIP_COMPLETED_TOPIC,
    EventRejectionReason,
    TripCompleted,
    derive_event_id,
    event_key,
    quarantine_topic_for,
    to_json,
)


# pylint: disable=duplicate-code
# Decision 28 (in spirit): duplication between nyc_cab and nyc_cab_events is
# tolerated until a shared-vocabulary package is justified.


_LOGGER = logging.getLogger(__name__)


# --- Configuration ----------------------------------------------------------


@dataclass(frozen=True)
class TripCompletedProducerConfig(_Validated):
    """Configure one run of the trip-completed event producer.

    The two topic fields default to the v1 contract constants; tests and
    integration harnesses can override them to point at scratch topics.
    """

    bootstrap_servers: str
    topic: str = TRIP_COMPLETED_TOPIC
    quarantine_topic: str = TRIP_COMPLETED_QUARANTINE_TOPIC

    _type_check_specs: ClassVar[tuple[CheckSpec, ...]] = (
        ("bootstrap_servers", str),
        ("topic", str),
        ("quarantine_topic", str),
    )

    def _structural_checks(self) -> tuple[CheckTuple, ...]:
        """Return structural validation rules for the producer config."""
        return (
            (self.bootstrap_servers.strip() != "", "bootstrap_servers", self.bootstrap_servers),
            (self.topic.strip() != "", "topic", self.topic),
            (self.quarantine_topic.strip() != "", "quarantine_topic", self.quarantine_topic),
            (self.topic != self.quarantine_topic, "quarantine_topic", self.quarantine_topic),
        )


# --- Result -----------------------------------------------------------------


@dataclass(frozen=True)
class TripCompletedProducerResult(_Validated):
    """Describe the result of one producer run.

    The reconciliation invariant
    ``silver_read_count == events_emitted + events_quarantined`` is enforced
    structurally; a result that violates it cannot be constructed via
    :meth:`create_validated`. This mirrors the discipline in
    :class:`~nyc_cab.transform.silver_entrypoint.SilverTransformResult`.
    """

    cab_type: str
    year: int
    month: int
    silver_partition_path: Path
    silver_read_count: int
    events_emitted: int
    events_quarantined: int

    _type_check_specs: ClassVar[tuple[CheckSpec, ...]] = (
        ("cab_type", str),
        ("year", int, bool),
        ("month", int, bool),
        ("silver_partition_path", Path),
        ("silver_read_count", int, bool),
        ("events_emitted", int, bool),
        ("events_quarantined", int, bool),
    )

    def _structural_checks(self) -> tuple[CheckTuple, ...]:
        """Return structural validation rules including the reconciliation invariant."""
        return (
            (self.cab_type.strip() != "", "cab_type", self.cab_type),
            (1900 <= self.year <= 2100, "year", self.year),
            (1 <= self.month <= 12, "month", self.month),
            (
                not self.silver_partition_path.exists() or self.silver_partition_path.is_dir(),
                "silver_partition_path",
                self.silver_partition_path,
            ),
            (self.silver_read_count >= 0, "silver_read_count", self.silver_read_count),
            (self.events_emitted >= 0, "events_emitted", self.events_emitted),
            (self.events_quarantined >= 0, "events_quarantined", self.events_quarantined),
            (
                self.silver_read_count == self.events_emitted + self.events_quarantined,
                "reconciliation",
                f"silver_read={self.silver_read_count} != "
                f"emitted={self.events_emitted} + quarantined={self.events_quarantined}",
            ),
        )


# --- Routed-message envelope ------------------------------------------------


@dataclass(frozen=True)
class _RoutedMessage:
    """One row's routing decision plus the wire-ready payload.

    Internal type. The driver receives a :class:`_RoutedMessage` per source
    row and dispatches to ``producer.produce`` accordingly. Splitting the
    decision out as data (rather than executing the produce call inside the
    routing function) keeps the per-row logic pure and testable.
    """

    topic: str
    key: str
    value: bytes
    headers: tuple[tuple[str, bytes], ...]
    """Headers as a tuple of (key, utf-8 bytes value), the confluent_kafka
    on-wire shape. Production-relevant headers include the rejection
    reason for quarantine routing (design log decision 40)."""

    is_quarantine: bool


# --- Pure per-row transformation --------------------------------------------


def _hour_of(pickup_datetime: datetime) -> int:
    """Return the local-time hour-of-day for the trip pickup.

    Silver carries pickup datetimes as naive ``timestamp_ntz`` values in
    NYC local time (the TLC source's reporting convention). Extracting
    ``.hour`` directly yields the NYC-local hour bucket, which is the
    grain the aggregate analysis cares about ("trips during the 8 AM
    rush"), not UTC.
    """
    return pickup_datetime.hour


def _build_event_from_silver_row(
    row: Mapping[str, Any],
    cab_type: str,
    year: int,
    month: int,
    produced_at: datetime,
) -> TripCompleted:
    """Construct a :class:`TripCompleted` from one Silver accepted row.

    Pure function. Raises :class:`KeyError` if required source fields are
    missing from the row; raises
    :class:`~nyc_cab.exceptions.InvalidRequestError` if the constructed
    event fails contract validation. The driver catches both to route the
    offending row to the quarantine topic.
    """
    # Augment with slice metadata so derive_event_id sees the full hash
    # input set. Silver partition columns are not present in the row dict
    # when Spark reads the leaf partition directly; the slice metadata
    # comes from the producer-run arguments.
    augmented = {
        **row,
        "cab_type": cab_type,
        "year": year,
        "month": month,
    }
    event_id_value = derive_event_id(augmented)

    return TripCompleted.create_validated(
        event_id_value,
        SCHEMA_VERSION,
        cab_type,
        year,
        month,
        _hour_of(row["tpep_pickup_datetime"]),
        float(row["trip_distance"]),
        float(row["fare_amount"]),
        int(row["passenger_count"]),
        produced_at,
    )


def _quarantine_headers(reason: EventRejectionReason, violations: str) -> tuple[tuple[str, bytes], ...]:
    """Return Kafka headers describing why a row was quarantined.

    Headers carry the rejection metadata; the payload body is the raw
    source row JSON. This split keeps the primary-topic and quarantine
    wire formats parallel (both are JSON objects) while making the reason
    machine-readable without parsing the body (design log decision 40).
    """
    return (
        ("rejection_reason", reason.value.encode("utf-8")),
        ("quarantined_at", datetime.now(timezone.utc).isoformat().encode("utf-8")),
        ("violations", violations.encode("utf-8")),
    )


def _serialize_quarantine_row(row: Mapping[str, Any], cab_type: str, year: int, month: int) -> bytes:
    """Serialize a quarantined source row to JSON-bytes.

    Uses ``str`` as the JSON default so datetimes and any other
    non-JSON-native values render as their string form. The quarantine
    topic is a diagnostic surface; faithful representation matters more
    than round-trip-safety.
    """
    # pylint: disable=import-outside-toplevel
    # Local import keeps the json dependency adjacent to its sole user
    # here, avoiding a module-level import for a single helper. The
    # contract module imports json at module level for the primary
    # serialization path.
    import json
    payload = {
        "cab_type": cab_type,
        "year": year,
        "month": month,
        **row,
    }
    return json.dumps(payload, default=str).encode("utf-8")


def _route_silver_row(
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    # The six arguments are all genuinely required per-row inputs: the row
    # itself, the three slice-metadata fields, the producer config, and the
    # produced_at instant. Bundling slice metadata into a dataclass is the
    # natural refactor when monthly-slice becomes formal shared vocabulary
    # (decision 28).
    row: Mapping[str, Any],
    cab_type: str,
    year: int,
    month: int,
    producer_config: TripCompletedProducerConfig,
    produced_at: datetime,
) -> _RoutedMessage:
    """Route one Silver row to either the primary topic or the quarantine topic.

    Pure function. The driver calls this once per row and produces the
    returned :class:`_RoutedMessage` to Kafka. Routing failures
    (``KeyError`` for missing source fields,
    :class:`InvalidRequestError` for contract violations) collapse to the
    same quarantine path; the headers distinguish the reason from a clean
    structural rejection.
    """
    try:
        event = _build_event_from_silver_row(row, cab_type, year, month, produced_at)
    except (KeyError, InvalidRequestError) as exc:
        violations_text = str(exc) if isinstance(exc, KeyError) else str(exc.violations)
        return _RoutedMessage(
            topic=quarantine_topic_for(EventRejectionReason.INVALID_CONSTRUCTION),
            key=f"{cab_type}/{year:04d}/{month:02d}",
            value=_serialize_quarantine_row(row, cab_type, year, month),
            headers=_quarantine_headers(EventRejectionReason.INVALID_CONSTRUCTION, violations_text),
            is_quarantine=True,
        )

    return _RoutedMessage(
        topic=producer_config.topic,
        key=event_key(event),
        value=to_json(event).encode("utf-8"),
        headers=(("schema_version", SCHEMA_VERSION.encode("utf-8")),),
        is_quarantine=False,
    )


# --- Kafka producer factory -------------------------------------------------


def _make_kafka_producer(config: TripCompletedProducerConfig) -> Producer:
    """Build a configured ``confluent_kafka.Producer``.

    Exposed as a module-level function so tests can monkeypatch it to
    return a recording fake. Production settings:

    - ``acks=all`` for full-quorum durability.
    - ``enable.idempotence=true`` so retries within librdkafka do not
      cause duplicate broker-side records.
    - ``linger.ms=10`` for light batching without meaningful latency
      penalty on a single-threaded driver.
    """
    return Producer({
        "bootstrap.servers": config.bootstrap_servers,
        "acks": "all",
        "enable.idempotence": True,
        "linger.ms": 10,
    })


# --- Driver -----------------------------------------------------------------


def produce_trip_completed_events(
    # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    # Six arguments and a moderately high local-variable count are
    # intrinsic to the driver: it ties together Spark, Kafka, the slice
    # metadata, and the per-row routing. The natural refactor is to bundle
    # slice metadata when decision 28's shared-vocabulary trigger fires.
    spark: SparkSession,
    silver_partition_path: Path,
    producer_config: TripCompletedProducerConfig,
    cab_type: str,
    year: int,
    month: int,
) -> TripCompletedProducerResult:
    """Read a Silver accepted partition and emit one event per row to Kafka.

    The slice metadata (``cab_type``, ``year``, ``month``) is passed
    explicitly rather than parsed from the partition path: the path is the
    storage location, the slice metadata is the run identity, and the two
    happen to be derivable from each other only because the Silver writer
    uses Hive partitioning. Decoupling them here keeps the function
    callable against any path layout the caller chooses to materialize.

    The driver streams rows via :meth:`DataFrame.toLocalIterator` so driver
    memory remains bounded regardless of partition size. Per-row work
    builds a routed message via :func:`_route_silver_row` and produces it
    to the configured Kafka producer; the producer is flushed before the
    function returns. Production failures (Kafka unreachable, broker
    rejection) propagate as :class:`KafkaException`.
    """
    _LOGGER.info(
        "producer.start: cab_type=%s year=%d month=%d partition=%s",
        cab_type, year, month, silver_partition_path,
    )

    df = spark.read.parquet(str(silver_partition_path))
    silver_read_count = df.count()
    _LOGGER.info("producer.read: silver_read_count=%d", silver_read_count)

    producer = _make_kafka_producer(producer_config)

    delivery_failures: list[str] = []

    def _delivery_callback(err: Any, _msg: Any) -> None:
        """Record async delivery failures so the driver can fail loudly."""
        if err is not None:
            delivery_failures.append(str(err))

    events_emitted = 0
    events_quarantined = 0

    for row in df.toLocalIterator():
        produced_at = datetime.now(timezone.utc)
        routed = _route_silver_row(
            row.asDict(recursive=False),
            cab_type, year, month,
            producer_config,
            produced_at,
        )
        producer.produce(
            topic=routed.topic,
            key=routed.key,
            value=routed.value,
            headers=list(routed.headers),
            on_delivery=_delivery_callback,
        )
        if routed.is_quarantine:
            events_quarantined += 1
            _LOGGER.info(
                "producer.quarantine: topic=%s key=%s reason=invalid_construction",
                routed.topic, routed.key,
            )
        else:
            events_emitted += 1
        # Drain delivery callbacks periodically so the in-memory queue
        # cannot grow unbounded on a slow broker.
        producer.poll(0)

    remaining = producer.flush(timeout=30)
    if remaining > 0:
        raise KafkaException(
            f"producer flush left {remaining} messages undelivered after timeout"
        )
    if delivery_failures:
        raise KafkaException(
            f"producer encountered {len(delivery_failures)} delivery failure(s); "
            f"first: {delivery_failures[0]}"
        )

    _LOGGER.info(
        "producer.done: emitted=%d quarantined=%d", events_emitted, events_quarantined,
    )

    return TripCompletedProducerResult.create_validated(
        cab_type,
        year,
        month,
        silver_partition_path,
        silver_read_count,
        events_emitted,
        events_quarantined,
    )
