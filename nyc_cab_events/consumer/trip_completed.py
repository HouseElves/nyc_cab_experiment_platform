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
to module level when it is filled in.
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

    ``max_idle_polls`` and ``poll_timeout_seconds`` together bound how long
    a batch consumer waits for new messages before declaring the queue
    drained and proceeding to the sink write. They have no effect on a
    long-running consumer; they exist to make the batch pattern deterministic
    in tests and reconciliation runs.
    """

    bootstrap_servers: str
    group_id: str
    topic: str = TRIP_COMPLETED_TOPIC
    poll_timeout_seconds: int = 5
    max_idle_polls: int = 3

    _type_check_specs: ClassVar[tuple[CheckSpec, ...]] = (
        ("bootstrap_servers", str),
        ("group_id", str),
        ("topic", str),
        ("poll_timeout_seconds", int, bool),
        ("max_idle_polls", int, bool),
    )

    def _structural_checks(self) -> tuple[CheckTuple, ...]:
        """Return structural validation rules for the consumer config."""
        return (
            (self.bootstrap_servers.strip() != "", "bootstrap_servers", self.bootstrap_servers),
            (self.group_id.strip() != "", "group_id", self.group_id),
            (self.topic.strip() != "", "topic", self.topic),
            (self.poll_timeout_seconds > 0, "poll_timeout_seconds", self.poll_timeout_seconds),
            (self.max_idle_polls > 0, "max_idle_polls", self.max_idle_polls),
        )


# --- Result -----------------------------------------------------------------


@dataclass(frozen=True)
class TripCompletedConsumerResult(_Validated):
    """Describe the result of one consumer run.

    ``hourly_buckets_written`` is at most ``events_consumed`` (collapsing
    happens by hour) and is at least one when any events are consumed. The
    invariant ``hourly_buckets_written <= events_consumed`` is enforced
    structurally.
    """

    events_consumed: int
    hourly_buckets_written: int

    _type_check_specs: ClassVar[tuple[CheckSpec, ...]] = (
        ("events_consumed", int, bool),
        ("hourly_buckets_written", int, bool),
    )

    def _structural_checks(self) -> tuple[CheckTuple, ...]:
        """Return structural validation rules for the consumer result."""
        return (
            (self.events_consumed >= 0, "events_consumed", self.events_consumed),
            (self.hourly_buckets_written >= 0, "hourly_buckets_written", self.hourly_buckets_written),
            (
                self.hourly_buckets_written <= self.events_consumed,
                "hourly_buckets_written",
                self.hourly_buckets_written,
            ),
        )


# --- Stub -------------------------------------------------------------------


def consume_and_aggregate(
    consumer_config: TripCompletedConsumerConfig,
    sink_config: object,
) -> TripCompletedConsumerResult:
    """Drain Kafka, aggregate, and upsert hourly counts to Postgres.

    The flow is:

        1. Build a ``confluent_kafka.Consumer`` from ``consumer_config``.
        2. Subscribe to ``consumer_config.topic``.
        3. Poll until ``max_idle_polls`` consecutive empty polls occur.
        4. For each message: deserialize a
           :class:`~nyc_cab_events.contracts.events.TripCompleted` event and
           increment the in-memory aggregator at
           ``(cab_type, year, month, hour)``.
        5. Call :func:`~nyc_cab_events.sink.postgres.upsert_hourly_counts`
           with the aggregator buckets.
        6. Commit Kafka offsets.
        7. Return a :class:`TripCompletedConsumerResult`.

    The ``sink_config`` argument is typed as :class:`object` only until the
    Postgres sink config dataclass moves from an in-function import to a
    module-level import alongside this stub being filled in.
    """
    # pylint: disable=unused-argument
    raise NotImplementedError(
        "consume_and_aggregate is a scaffolding stub; see module docstring "
        "and design log decision 25."
    )
