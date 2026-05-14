# pylint: disable=line-too-long
# Allow long lines for mermaid, it does not like embedded newlines.
"""
Trip-completed event contract.

This module defines the on-wire shape of ``trip.completed.v1`` events: the
dataclass that carries one event, the topic names, and the routing rule that
sends invalid candidates to the quarantine topic.

The contract is intentionally pure: no Spark, no Kafka, no Postgres. The
:class:`TripCompleted` dataclass uses the platform's ``_Validated`` mix-in
in exactly the same way as :class:`~nyc_cab.transform.silver_request.SilverTransformRequest`
and :class:`~nyc_cab.transform.silver_entrypoint.SilverTransformResult`. A
candidate that cannot be constructed via :meth:`TripCompleted.create_validated`
is routed to the quarantine topic by the producer; see
:func:`quarantine_topic_for`.

Class Relationships
-------------------

.. mermaid::

    classDiagram

        dataclass <|-- TripCompleted
        _Validated <|-- TripCompleted

        class TripCompleted {
            <<immutable>>
            string event_id
            string cab_type
            integer year
            integer month
            integer hour
            float trip_distance
            float fare_amount
            integer passenger_count
            datetime produced_at
        }

        class EventRejectionReason {
            <<enumeration>>
            INVALID_CONSTRUCTION
        }
        TripCompleted ..> EventRejectionReason : routed by

Topic Routing
-------------

.. mermaid::

    flowchart LR
        Producer -->|valid| Topic[trip.completed.v1]
        Producer -.->|invalid| Quarantine[trip.completed.v1.invalid]

The ``quarantine_topic_for`` helper exists so future fan-out (per-reason DLQs,
versioned quarantine topics) is a one-line change in this module rather than
producer surgery.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Final

from nyc_cab._validation import _Validated, CheckSpec, CheckTuple


# --- Topic names ------------------------------------------------------------

TRIP_COMPLETED_TOPIC: Final[str] = "trip.completed.v1"
"""Primary Kafka topic carrying validated ``TripCompleted`` events."""

TRIP_COMPLETED_QUARANTINE_TOPIC: Final[str] = "trip.completed.v1.invalid"
"""Quarantine topic for Silver rows that fail event-contract validation."""


# --- Rejection reasons ------------------------------------------------------

class EventRejectionReason(enum.Enum):
    """Enumerate why a Silver row failed ``TripCompleted`` contract validation.

    v1 collapses all reasons into ``INVALID_CONSTRUCTION``. The enum exists so
    that future, finer-grained reasons (negative fare, null pickup, etc.) can
    be added without changing the routing surface.
    """

    INVALID_CONSTRUCTION = "invalid_construction"


# --- Event dataclass --------------------------------------------------------

@dataclass(frozen=True)
class TripCompleted(_Validated):
    """One ``trip.completed.v1`` event.

    The ``hour`` field is event-time bucketing derived from
    ``tpep_pickup_datetime`` on the source Silver row, not processing time
    (design log decision 33). ``produced_at`` carries the wall-clock time at
    which the producer emitted the event.

    A ``TripCompleted`` instance is on-wire-ready: every field has passed
    both a type check and a structural check at construction time. Construction
    failures raise :class:`~nyc_cab.exceptions.InvalidRequestError`, which the
    producer catches to route the source row to the quarantine topic.
    """

    # pylint: disable=too-many-instance-attributes
    # The nine fields are the on-wire event contract; field count is set by
    # the contract, not by stylistic preference. The pylint default of 7 is a
    # reasonable heuristic for general classes; event dataclasses routinely
    # exceed it.

    event_id: str
    cab_type: str
    year: int
    month: int
    hour: int
    trip_distance: float
    fare_amount: float
    passenger_count: int
    produced_at: datetime

    _type_check_specs: ClassVar[tuple[CheckSpec, ...]] = (
        ("event_id", str),
        ("cab_type", str),
        ("year", int, bool),
        ("month", int, bool),
        ("hour", int, bool),
        ("trip_distance", float),
        ("fare_amount", float),
        ("passenger_count", int, bool),
        ("produced_at", datetime),
    )

    def _structural_checks(self) -> tuple[CheckTuple, ...]:
        """Return structural validation rules for one event."""
        return (
            (self.event_id.strip() != "", "event_id", self.event_id),
            (self.cab_type.strip() != "", "cab_type", self.cab_type),
            (1900 <= self.year <= 2100, "year", self.year),
            (1 <= self.month <= 12, "month", self.month),
            (0 <= self.hour <= 23, "hour", self.hour),
            (self.trip_distance >= 0.0, "trip_distance", self.trip_distance),
            (self.fare_amount >= 0.0, "fare_amount", self.fare_amount),
            (0 <= self.passenger_count <= 9, "passenger_count", self.passenger_count),
            (self.produced_at.tzinfo is not None, "produced_at", self.produced_at),
        )


# --- Routing ----------------------------------------------------------------

def quarantine_topic_for(reason: EventRejectionReason) -> str:
    """Return the Kafka topic that an event with this rejection reason routes to.

    For v1 every rejection routes to the same quarantine topic. The indirection
    keeps producer code agnostic to per-reason fan-out decisions made later.
    """
    if not isinstance(reason, EventRejectionReason):
        raise TypeError(f"reason must be an EventRejectionReason, got {type(reason).__name__}")
    # All v1 reasons share one quarantine topic. Per-reason fan-out lands here
    # when policy starts to differentiate.
    return TRIP_COMPLETED_QUARANTINE_TOPIC
