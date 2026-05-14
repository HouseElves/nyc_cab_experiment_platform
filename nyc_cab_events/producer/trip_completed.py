"""
Trip-completed event producer.

Reads a Silver accepted Parquet partition and emits one
:class:`~nyc_cab_events.contracts.events.TripCompleted` event per accepted row
to Kafka. See the package ``__init__`` for the full flow diagram.

This module currently provides:

- :class:`TripCompletedProducerConfig` — fully implemented validated config
- :class:`TripCompletedProducerResult` — fully implemented validated result,
  including the reconciliation invariant
  ``silver_read_count == events_emitted + events_quarantined``
- :func:`derive_event_id` — stub
- :func:`produce_trip_completed_events` — stub

The stubs are guarded by :class:`NotImplementedError` and corresponding
``pytest.raises`` tests (design log decision 25). The heavy imports
(:mod:`pyspark`, :mod:`confluent_kafka`) live inside the stubs and will move
to module level when the stubs are filled in.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from nyc_cab._validation import _Validated, CheckSpec, CheckTuple
from nyc_cab_events.contracts.events import (
    TRIP_COMPLETED_QUARANTINE_TOPIC,
    TRIP_COMPLETED_TOPIC,
)


# pylint: disable=duplicate-code
# Decision 28 (in spirit): duplication between nyc_cab and nyc_cab_events is
# tolerated until a shared-vocabulary package is justified.


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


# --- Stubs ------------------------------------------------------------------


def derive_event_id(row: Any) -> str:
    """Derive a deterministic ``event_id`` for one Silver accepted row.

    The id is a SHA-256 hex digest truncated to 16 hex characters (64 bits)
    over the tuple
    ``(cab_type, year, month, VendorID, tpep_pickup_datetime,
    tpep_dropoff_datetime, PULocationID, DOLocationID, fare_amount,
    total_amount)``. The truncation length is comfortably above NYC monthly
    cardinality (~3 million rows; 64-bit space has effective collision
    resistance around 2**32 ≈ 4 billion by the birthday bound).

    Determinism is the load-bearing property: rerunning the producer on the
    same Silver partition must emit identical ``event_id`` values so that
    Kafka key equality lets downstream consumers detect duplicates.
    """
    raise NotImplementedError(
        "derive_event_id is a scaffolding stub; see module docstring and design log decision 25."
    )


def produce_trip_completed_events(
    spark: Any,
    silver_partition_path: Path,
    producer_config: TripCompletedProducerConfig,
) -> TripCompletedProducerResult:
    """Read a Silver accepted partition and emit one event per row to Kafka.

    The flow is:

        1. Read ``silver_partition_path`` with Spark.
        2. Count rows to anchor the reconciliation invariant.
        3. For each row: derive ``event_id``, build a ``TripCompleted`` via
           :meth:`~nyc_cab_events.contracts.events.TripCompleted.create_validated`.
        4. On success, produce to ``producer_config.topic``.
        5. On :class:`~nyc_cab.exceptions.InvalidRequestError`, route to
           :func:`~nyc_cab_events.contracts.events.quarantine_topic_for`.
        6. Flush the underlying ``confluent_kafka.Producer``.
        7. Return a :class:`TripCompletedProducerResult` with counts and the
           reconciliation invariant enforced.

    The ``spark`` argument is typed as ``Any`` only until pyspark moves from
    an in-function import to a module-level import alongside this stub being
    filled in.
    """
    # pylint: disable=unused-argument
    raise NotImplementedError(
        "produce_trip_completed_events is a scaffolding stub; see module docstring "
        "and design log decision 25."
    )
