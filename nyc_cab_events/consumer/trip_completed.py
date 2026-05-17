"""
Trip-completed event consumer.

Reads ``trip.completed.v1`` from Kafka, aggregates events by
``(cab_type, year, month, hour)``, and writes hourly counts to the Postgres
sink. See the package ``__init__`` for the full flow diagram.

This module currently provides:

- :class:`TripCompletedConsumerConfig` — fully implemented validated config
- :class:`TripCompletedConsumerResult` — fully implemented validated result
- :func:`consume_and_aggregate` — stub

The stub is guarded by :class:`NotImplementedError` and a corresponding
``pytest.raises`` test (design log decision 25). The heavy imports
(:mod:`confluent_kafka`, :mod:`psycopg`) live inside the stub and will move
to module level when it is filled in. The shape of the config and result
dataclasses below already reflects the decision-42 commitment to bounded
full-slice replay; Phase B fills in the function body against this shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from nyc_cab._validation import _Validated, CheckSpec, CheckTuple
from nyc_cab_events.contracts.events import TRIP_COMPLETED_TOPIC


# pylint: disable=duplicate-code
# Decision 28 (in spirit): duplication between nyc_cab and nyc_cab_events is
# tolerated until a shared-vocabulary package is justified.


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


# --- Stub -------------------------------------------------------------------


def consume_and_aggregate(
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    # Five arguments are intrinsic to the bounded-full-slice-replay run:
    # consumer config, sink config, and the three slice-metadata fields
    # that constitute the run identity per design log decision 42.
    consumer_config: TripCompletedConsumerConfig,
    sink_config: object,
    cab_type: str,
    year: int,
    month: int,
) -> TripCompletedConsumerResult:
    """Drain Kafka, aggregate, and upsert hourly counts to Postgres.

    Per design log decision 42 (bounded full-slice replay), the flow is:

        1. Build a ``confluent_kafka.Consumer`` from ``consumer_config``.
        2. Subscribe to ``consumer_config.topic``.
        3. Capture end-of-partition offsets via
           ``Consumer.get_watermark_offsets`` — the deterministic replay
           window.
        4. Poll forward until each partition reaches its captured end
           offset, then stop. ``consumer_config.poll_timeout_seconds``
           is the per-poll blocking time; it is not a termination
           criterion.
        5. For each message: deserialize a
           :class:`~nyc_cab_events.contracts.events.TripCompleted`
           event, filter to the requested ``(cab_type, year, month)``
           slice, deduplicate in memory on ``event_id``, and increment
           the in-memory aggregator at
           ``(cab_type, year, month, hour)``.
        6. Call :func:`~nyc_cab_events.sink.postgres.upsert_hourly_counts`
           with the aggregator buckets. Overwrite-on-conflict semantics
           make the run's count the single source of truth for the
           slice's rows.
        7. Return a :class:`TripCompletedConsumerResult`.

    Kafka offsets are not committed back to the broker. The contract is
    "read the complete slice or write nothing"; a crashed run is
    recovered by re-running the slice, not by resuming.

    The ``sink_config`` argument is typed as :class:`object` only until
    the Postgres sink config dataclass moves from an in-function import
    to a module-level import alongside this stub being filled in.
    """
    # pylint: disable=unused-argument
    raise NotImplementedError(
        "consume_and_aggregate is a scaffolding stub; see module docstring "
        "and design log decision 25."
    )
