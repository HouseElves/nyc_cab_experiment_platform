"""
Trip-completed event consumer.

Reads ``trip.completed.v1`` from Kafka, aggregates events by
``(cab_type, year, month, hour)``, and writes hourly counts to the Postgres
sink. See the package ``__init__`` for the full flow diagram and the
counter-invariant statement.

The implementation is layered:

- :class:`_Disposition` — closed enum classifying one polled message.
  ``IN_SLICE`` / ``OUT_OF_SLICE`` / ``INVALID`` partition ``events_read``
  per design log decision 45.
- :class:`_ConsumedMessage` — discriminator dataclass returned by
  :func:`_classify_message`. Carries the parsed event when the
  disposition is ``IN_SLICE``; ``event`` is ``None`` otherwise.
- :func:`_classify_message` — pure function. Catches
  :class:`InvalidEventPayloadError` from
  :func:`~nyc_cab_events.contracts.events.from_json` and the slice
  filter is in-line. The seen-set check and aggregator increment
  happen in the driver; they are stateful, so they stay there.
- :func:`_aggregator_to_buckets` — pure function. Converts the
  ``{(cab_type, year, month, hour): count}`` aggregator dict to a list
  of :class:`HourlyBucket` instances suitable for the sink.
- :func:`_make_kafka_consumer` — factory returning a configured
  ``confluent_kafka.Consumer``. Module-level so tests can monkeypatch
  it to inject a recording fake (parity with the producer's
  :func:`_make_kafka_producer`).
- :func:`consume_and_aggregate` — driver. Discovers partitions via
  :meth:`Consumer.list_topics`, assigns them at offset 0, captures
  end-of-partition watermarks, polls forward to those watermarks,
  classifies each message, deduplicates in-slice events on
  deterministic ``event_id``, accumulates hourly counts, upserts the
  complete bucket sequence, and returns a
  :class:`TripCompletedConsumerResult`.

Counter invariants
------------------

Six run-level counters partition the polled message stream. The
identities below are documented here, enforced as noted, and
exercised by unit tests in
:mod:`nyc_cab_events.consumer.test.trip_completed`:

- ``events_read == events_invalid + events_out_of_slice + events_in_slice``
  Partition identity over the three :class:`_Disposition` values.
  Not structurally enforceable on the result (``events_invalid`` and
  ``events_out_of_slice`` are not materialized on the dataclass);
  verified by tests via the INFO log line and via the observable
  arithmetic ``events_read - events_in_slice == events_invalid +
  events_out_of_slice``.
- ``events_in_slice == events_duplicate + events_unique``. Driver-
  local identity at the seen-set boundary.
- ``events_unique <= events_in_slice <= events_read`` and
  ``hourly_buckets_written <= events_unique``. Structurally enforced
  on :class:`TripCompletedConsumerResult` via
  :meth:`_Validated.create_validated`; a violating result cannot be
  constructed.
- ``events_unique == sum(bucket.event_count for bucket in buckets)``
  and ``hourly_buckets_written == len(buckets)``. Enforced by the
  driver at the result-construction boundary with explicit ``raise
  RuntimeError``; ``assert`` is not used because assertions are
  stripped under ``python -O`` and the checks are cheap. Failure
  here indicates a driver-loop implementation bug.

The contract is "read the complete slice or write nothing"
(decision 42). Kafka offsets are not committed back to the broker:
a crashed run is recovered by re-running the slice, not by resuming.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from typing import ClassVar

from confluent_kafka import Consumer, KafkaException, Message, TopicPartition

from nyc_cab._validation import _Validated, CheckSpec, CheckTuple
from nyc_cab_events.contracts.events import (
    InvalidEventPayloadError,
    TRIP_COMPLETED_TOPIC,
    TripCompleted,
    from_json,
)
from nyc_cab_events.sink.postgres import (
    HourlyBucket,
    PostgresSinkConfig,
    upsert_hourly_counts,
)


# pylint: disable=duplicate-code
# Decision 28 (in spirit): duplication between nyc_cab and nyc_cab_events is
# tolerated until a shared-vocabulary package is justified.


_LOGGER = logging.getLogger(__name__)


# --- Configuration ----------------------------------------------------------


@dataclass(frozen=True)
class TripCompletedConsumerConfig(_Validated):
    """Configure one run of the trip-completed event consumer.

    ``poll_timeout_seconds`` bounds how long each individual
    :meth:`Consumer.poll` call blocks waiting for new messages. The
    consumer's termination condition is the captured high-water-mark
    offsets per partition (design log decision 42), not an idle-timeout
    threshold — the prior ``max_idle_polls`` field has been removed.
    """

    bootstrap_servers: str
    group_id: str
    topic: str = TRIP_COMPLETED_TOPIC
    poll_timeout_seconds: int = 5

    _type_check_specs: ClassVar[tuple[CheckSpec, ...]] = (
        ("bootstrap_servers", str),
        ("group_id", str),
        ("topic", str),
        ("poll_timeout_seconds", int, bool),
    )

    def _structural_checks(self) -> tuple[CheckTuple, ...]:
        """Return structural validation rules for the consumer config."""
        return (
            (self.bootstrap_servers.strip() != "", "bootstrap_servers", self.bootstrap_servers),
            (self.group_id.strip() != "", "group_id", self.group_id),
            (self.topic.strip() != "", "topic", self.topic),
            (self.poll_timeout_seconds > 0, "poll_timeout_seconds", self.poll_timeout_seconds),
        )


# --- Result -----------------------------------------------------------------


@dataclass(frozen=True)
class TripCompletedConsumerResult(_Validated):
    """Describe the result of one bounded-full-slice-replay consumer run.

    The slice identity (``cab_type``, ``year``, ``month``) is part of the
    result, mirroring the producer-side ``TripCompletedProducerResult``.
    The replay-window counters report what was read, what was in-slice
    after filtering, and what was unique after deduplication on
    ``event_id``. ``hourly_buckets_written`` is the number of
    ``(cab_type, year, month, hour)`` aggregate rows handed to the sink.

    Two structural invariants hold:

    - ``events_in_slice <= events_read``: filtering monotonically reduces.
    - ``events_unique <= events_in_slice``: deduplication monotonically reduces.
    - ``hourly_buckets_written <= events_unique``: collapse by hour
      reduces (or stays equal when every event lands in its own hour).
    """

    cab_type: str
    year: int
    month: int
    events_read: int
    events_in_slice: int
    events_unique: int
    hourly_buckets_written: int

    _type_check_specs: ClassVar[tuple[CheckSpec, ...]] = (
        ("cab_type", str),
        ("year", int, bool),
        ("month", int, bool),
        ("events_read", int, bool),
        ("events_in_slice", int, bool),
        ("events_unique", int, bool),
        ("hourly_buckets_written", int, bool),
    )

    def _structural_checks(self) -> tuple[CheckTuple, ...]:
        """Return structural validation rules for the consumer result."""
        return (
            (self.cab_type.strip() != "", "cab_type", self.cab_type),
            (1900 <= self.year <= 2100, "year", self.year),
            (1 <= self.month <= 12, "month", self.month),
            (self.events_read >= 0, "events_read", self.events_read),
            (self.events_in_slice >= 0, "events_in_slice", self.events_in_slice),
            (self.events_unique >= 0, "events_unique", self.events_unique),
            (self.hourly_buckets_written >= 0, "hourly_buckets_written", self.hourly_buckets_written),
            (
                self.events_in_slice <= self.events_read,
                "events_in_slice",
                self.events_in_slice,
            ),
            (
                self.events_unique <= self.events_in_slice,
                "events_unique",
                self.events_unique,
            ),
            (
                self.hourly_buckets_written <= self.events_unique,
                "hourly_buckets_written",
                self.hourly_buckets_written,
            ),
        )


# --- Per-message classification ---------------------------------------------


class _Disposition(enum.Enum):
    """Classify one polled Kafka message.

    The three values partition ``events_read`` per design log decision 45.
    Adding a disposition is an additive change to the partition identity
    and must be accompanied by a new run-level counter and the
    corresponding test coverage.
    """

    IN_SLICE = "in_slice"
    OUT_OF_SLICE = "out_of_slice"
    INVALID = "invalid"


@dataclass(frozen=True)
class _ConsumedMessage:
    """One polled message's classification result.

    Internal type. ``event`` is populated only when ``disposition`` is
    :attr:`_Disposition.IN_SLICE`; the driver does not need the parsed
    event for the other dispositions and dropping it avoids carrying
    unused state through the run.
    """

    disposition: _Disposition
    event: TripCompleted | None


def _classify_message(
    msg: Message,
    cab_type: str,
    year: int,
    month: int,
) -> _ConsumedMessage:
    """Classify one polled Kafka message into a disposition.

    Pure function. Catches :class:`InvalidEventPayloadError` from
    :func:`~nyc_cab_events.contracts.events.from_json` and routes the
    message to :attr:`_Disposition.INVALID`. Events whose slice metadata
    does not match the requested ``(cab_type, year, month)`` go to
    :attr:`_Disposition.OUT_OF_SLICE`. Everything else is
    :attr:`_Disposition.IN_SLICE` with the parsed event attached.

    The driver does the seen-set check and aggregator increment
    downstream — duplicate-vs-unique is not knowable from the message
    alone, so it is not part of this helper's contract.

    A ``None`` ``msg.value()`` (broker tombstone or malformed envelope)
    is treated as ``INVALID``. The classification never raises; failure
    modes are returned as values, not as exceptions, per the rationale
    in design log decision 45.
    """
    payload = msg.value()
    if payload is None:
        return _ConsumedMessage(_Disposition.INVALID, None)

    try:
        event = from_json(payload)
    except InvalidEventPayloadError:
        return _ConsumedMessage(_Disposition.INVALID, None)

    if event.cab_type != cab_type or event.year != year or event.month != month:
        return _ConsumedMessage(_Disposition.OUT_OF_SLICE, None)

    return _ConsumedMessage(_Disposition.IN_SLICE, event)


# --- Aggregator → buckets ---------------------------------------------------


def _aggregator_to_buckets(
    aggregator: dict[tuple[str, int, int, int], int],
) -> list[HourlyBucket]:
    """Convert the in-memory aggregator to a list of :class:`HourlyBucket`.

    Pure function. The aggregator key is ``(cab_type, year, month,
    hour)`` and the value is the unique-event count for that bucket.
    Each entry constructs one :class:`HourlyBucket` via
    :meth:`HourlyBucket.create_validated`, so any out-of-band corruption
    of the aggregator (e.g. negative count, hour outside ``0..23``)
    raises :class:`~nyc_cab.exceptions.InvalidRequestError` at this
    boundary rather than silently propagating to the sink.

    The output order is the aggregator's iteration order, which on
    CPython 3.7+ is insertion order. Sink upserts are idempotent under
    permutation, so ordering is unobservable to the database row state
    but stable for tests that assert on the sequence.
    """
    return [
        HourlyBucket.create_validated(cab_type, year, month, hour, count)
        for (cab_type, year, month, hour), count in aggregator.items()
    ]


# --- Kafka consumer factory -------------------------------------------------


def _make_kafka_consumer(config: TripCompletedConsumerConfig) -> Consumer:
    """Build a configured ``confluent_kafka.Consumer``.

    Exposed as a module-level function so tests can monkeypatch it to
    return a recording fake (parity with
    :func:`~nyc_cab_events.producer.trip_completed._make_kafka_producer`).
    Production settings:

    - ``group.id`` from the consumer config. Required by librdkafka even
      when partitions are assigned explicitly (design log decision 44);
      the group is not used for partition assignment or offset
      committing, only to satisfy the client-library precondition.
    - ``enable.auto.commit=false``. Decision 42 forbids committed-offset
      resumption; auto-commit would write offsets back to the broker
      on a timer and break the "read the complete slice or write
      nothing" contract.
    - ``auto.offset.reset=earliest``. Bounded replay reads each
      partition from offset 0; explicit ``TopicPartition`` offsets
      override this, but the safe default matches the contract.
    """
    return Consumer({
        "bootstrap.servers": config.bootstrap_servers,
        "group.id": config.group_id,
        "enable.auto.commit": False,
        "auto.offset.reset": "earliest",
    })


# --- Driver helpers ---------------------------------------------------------


def _discover_partitions(consumer: Consumer, topic: str) -> list[int]:
    """Return the list of partition ids for ``topic`` via Kafka metadata.

    Uses :meth:`Consumer.list_topics` so the partition set comes from
    the broker rather than from a producer-side constant (design log
    decision 44). A missing topic at consumer-run time raises
    :class:`KafkaException` and propagates: a deployment-time problem,
    not a recoverable empty run.
    """
    metadata = consumer.list_topics(topic, timeout=10.0)
    topic_metadata = metadata.topics.get(topic)
    if topic_metadata is None or topic_metadata.error is not None:
        err = topic_metadata.error if topic_metadata is not None else "topic not found"
        raise KafkaException(f"list_topics({topic!r}) failed: {err}")
    return sorted(topic_metadata.partitions.keys())


def _capture_replay_window(
    consumer: Consumer,
    topic: str,
    partition_ids: list[int],
) -> dict[int, int]:
    """Capture end-of-partition watermarks; return ``{partition: high}``.

    Excludes empty partitions (``high == 0``) at capture time: a
    partition with no records is already at its end offset and would
    waste a poll iteration. The remaining partitions form the
    deterministic replay window per design log decision 42.
    """
    window: dict[int, int] = {}
    for partition_id in partition_ids:
        _, high = consumer.get_watermark_offsets(
            TopicPartition(topic, partition_id),
            timeout=10.0,
        )
        if high > 0:
            window[partition_id] = high
    return window


# --- Driver -----------------------------------------------------------------


def consume_and_aggregate(
    # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals,too-many-branches,too-many-statements
    # Five arguments are intrinsic to the bounded-full-slice-replay run:
    # consumer config, sink config, and the three slice-metadata fields
    # that constitute the run identity per design log decision 42. The
    # local-variable / branch / statement counts are driven by the six
    # counters in the partition identity (decision 45) plus the seen-set,
    # aggregator, replay-window, and per-partition consumed-offset
    # tracking; bundling them into a helper dataclass would obscure the
    # straight-line loop body without reducing real complexity.
    consumer_config: TripCompletedConsumerConfig,
    sink_config: PostgresSinkConfig,
    cab_type: str,
    year: int,
    month: int,
) -> TripCompletedConsumerResult:
    """Drain Kafka, aggregate, and upsert hourly counts to Postgres.

    Per design log decisions 42, 44, and 45, the flow is:

        1. Build a ``confluent_kafka.Consumer`` from ``consumer_config``.
        2. Discover the topic's partition set via
           :meth:`Consumer.list_topics` and assign all partitions at
           offset 0 via :meth:`Consumer.assign` (decision 44).
        3. Capture end-of-partition offsets via
           :meth:`Consumer.get_watermark_offsets` — the deterministic
           replay window. Empty partitions (high == 0) are excluded
           from the window at capture time.
        4. Poll forward until each partition reaches its captured end
           offset, then stop. ``consumer_config.poll_timeout_seconds``
           is the per-poll blocking time; it is not a termination
           criterion.
        5. Classify each message via :func:`_classify_message`,
           increment the appropriate disposition counter, and for
           in-slice events check the seen-set, increment the
           aggregator, and update the unique/duplicate counters.
        6. Convert the aggregator to :class:`HourlyBucket` instances
           via :func:`_aggregator_to_buckets`, verify driver-level
           integrity invariants, and call
           :func:`~nyc_cab_events.sink.postgres.upsert_hourly_counts`.
        7. Return a :class:`TripCompletedConsumerResult`. The six
           counters partition the polled stream per the module
           docstring's counter-invariant statement.

    Kafka offsets are not committed back to the broker. The contract is
    "read the complete slice or write nothing"; a crashed run is
    recovered by re-running the slice, not by resuming. The consumer is
    closed in a ``try/finally`` so partition assignments are released
    regardless of how the loop exits.
    """
    _LOGGER.info(
        "consumer.start: cab_type=%s year=%d month=%d topic=%s",
        cab_type, year, month, consumer_config.topic,
    )

    seen: set[str] = set()
    aggregator: dict[tuple[str, int, int, int], int] = {}

    events_read = 0
    events_invalid = 0
    events_out_of_slice = 0
    events_in_slice = 0
    events_duplicate = 0
    events_unique = 0

    consumer = _make_kafka_consumer(consumer_config)
    try:
        partition_ids = _discover_partitions(consumer, consumer_config.topic)
        consumer.assign([
            TopicPartition(consumer_config.topic, p, 0) for p in partition_ids
        ])
        replay_window = _capture_replay_window(consumer, consumer_config.topic, partition_ids)
        _LOGGER.info(
            "consumer.window: partitions=%d non_empty=%d",
            len(partition_ids), len(replay_window),
        )

        # Track the highest offset consumed per partition so we can
        # stop once every non-empty partition has reached its captured
        # end offset. Kafka offsets are monotonic within a partition,
        # so a poll-returned offset >= high - 1 means the next poll
        # would have nothing left to deliver from that partition.
        consumed_to: dict[int, int] = {p: -1 for p in replay_window}

        while any(consumed_to[p] < high - 1 for p, high in replay_window.items()):
            msg = consumer.poll(consumer_config.poll_timeout_seconds)
            if msg is None:
                # poll() can return None on timeout even when offsets
                # remain to consume — keep looping until the watermark
                # condition is satisfied. The bounded window is the
                # termination criterion (decision 42), not idle time.
                continue
            if msg.error() is not None:
                # Broker-side errors on a polled message are operational
                # failures (not contract failures) and propagate. The
                # contract is "read the complete slice or write nothing";
                # a broker error means we cannot guarantee we have read
                # the complete slice.
                raise KafkaException(msg.error())

            partition = msg.partition()
            offset = msg.offset()

            # Replay window guard (design log decision 42: "events
            # arriving mid-run fall into the next replay"). Reject any
            # polled message whose partition was not in the captured
            # window (initially empty partitions that received mid-run
            # writes) or whose offset is at or beyond the captured
            # high-water mark (mid-run writes to non-empty partitions).
            # Beyond-window messages must not increment any counter —
            # they belong to the next replay, not this one.
            if partition not in replay_window:
                continue
            if offset >= replay_window[partition]:
                continue

            events_read += 1
            consumed_to[partition] = max(consumed_to[partition], offset)

            consumed = _classify_message(msg, cab_type, year, month)
            if consumed.disposition is _Disposition.INVALID:
                events_invalid += 1
                continue
            if consumed.disposition is _Disposition.OUT_OF_SLICE:
                events_out_of_slice += 1
                continue

            # Disposition is IN_SLICE; event is non-None by
            # _classify_message's contract.
            events_in_slice += 1
            event = consumed.event
            if event is None:
                # Defensive: _classify_message guarantees event is
                # non-None for IN_SLICE. A None here means the helper's
                # contract has been violated and we cannot continue
                # safely.
                raise RuntimeError(
                    "consumer integrity: IN_SLICE disposition with None event"
                )
            if event.event_id in seen:
                events_duplicate += 1
                continue

            seen.add(event.event_id)
            events_unique += 1
            key = (event.cab_type, event.year, event.month, event.hour)
            aggregator[key] = aggregator.get(key, 0) + 1
    finally:
        consumer.close()

    buckets = _aggregator_to_buckets(aggregator)

    # Driver-level integrity invariants (design log decision 45):
    # prove the loop did not lie before publishing the result. Two
    # identities connect the run-level counters to the bucket sequence
    # handed to the sink; neither can be a structural check on
    # TripCompletedConsumerResult alone, so they live here, immediately
    # adjacent to create_validated, with explicit RuntimeError rather
    # than assert (which would be stripped under python -O).
    bucket_sum = sum(b.event_count for b in buckets)
    if bucket_sum != events_unique:
        raise RuntimeError(
            f"consumer integrity: bucket sum {bucket_sum} != "
            f"events_unique {events_unique}"
        )
    if len(buckets) != len(aggregator):
        raise RuntimeError(
            f"consumer integrity: len(buckets) {len(buckets)} != "
            f"len(aggregator) {len(aggregator)}"
        )
    hourly_buckets_written = len(buckets)

    rows_affected = upsert_hourly_counts(sink_config, buckets)
    _LOGGER.info(
        "consumer.done: read=%d invalid=%d out_of_slice=%d in_slice=%d "
        "duplicate=%d unique=%d buckets=%d rows_affected=%d",
        events_read, events_invalid, events_out_of_slice, events_in_slice,
        events_duplicate, events_unique, hourly_buckets_written, rows_affected,
    )

    return TripCompletedConsumerResult.create_validated(
        cab_type,
        year,
        month,
        events_read,
        events_in_slice,
        events_unique,
        hourly_buckets_written,
    )
