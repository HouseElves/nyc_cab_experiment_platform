# pylint: disable=line-too-long
# Allow long lines for mermaid, it does not like embedded newlines.
"""
Trip-completed event contract.

This module defines the on-wire shape of ``trip.completed.v1`` events: the
dataclass that carries one event, the topic names, the deterministic
``event_id`` derivation, the Kafka routing key, JSON wire-format
serialization and deserialization, and the routing rule that sends invalid
candidates to the quarantine topic.

The contract is intentionally pure: no Spark, no Kafka, no Postgres. The
:class:`TripCompleted` dataclass uses the platform's ``_Validated`` mix-in
in exactly the same way as :class:`~nyc_cab.transform.silver_request.SilverTransformRequest`
and :class:`~nyc_cab.transform.silver_entrypoint.SilverTransformResult`. A
candidate that cannot be constructed via :meth:`TripCompleted.create_validated`
is routed to the quarantine topic by the producer; see
:func:`quarantine_topic_for`.

Wire-format envelope
--------------------

Each event carries two envelope fields and eight payload fields:

- ``event_id`` — deterministic SHA-256 derivation from the Silver source
  row (see :func:`derive_event_id`).
- ``schema_version`` — the wire-format major version. Currently fixed at
  :data:`SCHEMA_VERSION`. Strict equality is enforced on both construction
  and deserialization; a future ``v2`` lives on a new topic.

Class Relationships
-------------------

.. mermaid::

    classDiagram

        dataclass <|-- TripCompleted
        _Validated <|-- TripCompleted

        class TripCompleted {
            <<immutable>>
            string event_id
            string schema_version
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

Determinism and Wire Format
---------------------------

.. mermaid::

    flowchart LR
        SilverRow[Silver accepted row<br/>dict[str, Any]] --> Hash[derive_event_id<br/>SHA-256, 16 hex chars]
        Hash --> TripCompleted
        TripCompleted --> EventKey[event_key<br/>cab_type/year/month/hour]
        TripCompleted --> JSON[to_json / from_json<br/>strict wire format]
"""

from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar, Final, Mapping

from nyc_cab._validation import _Validated, CheckSpec, CheckTuple
from nyc_cab.exceptions import InvalidRequestError


# --- Topic names ------------------------------------------------------------

TRIP_COMPLETED_TOPIC: Final[str] = "trip.completed.v1"
"""Primary Kafka topic carrying validated ``TripCompleted`` events."""

TRIP_COMPLETED_QUARANTINE_TOPIC: Final[str] = "trip.completed.v1.invalid"
"""Quarantine topic for Silver rows that fail event-contract validation."""


# --- Wire-format version ----------------------------------------------------

SCHEMA_VERSION: Final[str] = "1"
"""Wire-format major version for the ``trip.completed.v1`` payload.

Strict equality is enforced on both construction and deserialization. A
future ``v2`` lives on a new topic with its own SCHEMA_VERSION constant;
producers and consumers are paired per major version. See design log
decision 37.
"""


# --- Rejection reasons ------------------------------------------------------

class EventRejectionReason(enum.Enum):
    """Enumerate why a Silver row failed ``TripCompleted`` contract validation.

    v1 collapses all reasons into ``INVALID_CONSTRUCTION``. The enum exists so
    that future, finer-grained reasons (negative fare, null pickup, etc.) can
    be added without changing the routing surface.
    """

    INVALID_CONSTRUCTION = "invalid_construction"


# --- Exceptions -------------------------------------------------------------


class InvalidEventPayloadError(ValueError):
    """A JSON payload could not be deserialized into a valid ``TripCompleted``.

    Raised by :func:`from_json` when the payload is malformed, missing
    required fields, has unexpected fields, fails schema-version validation,
    or fails structural contract validation after parsing. Consumers catch
    this to route the offending payload to a downstream quarantine action.
    """


# --- Event dataclass --------------------------------------------------------

@dataclass(frozen=True)
class TripCompleted(_Validated):
    """One ``trip.completed.v1`` event.

    The ``hour`` field is event-time bucketing derived from
    ``tpep_pickup_datetime`` on the source Silver row, not processing time
    (design log decision 33). ``produced_at`` carries the wall-clock time at
    which the producer emitted the event.

    A ``TripCompleted`` instance is on-wire-ready: every field has passed
    both a type check and a structural check at construction time, including
    strict equality of ``schema_version`` against :data:`SCHEMA_VERSION`
    (design log decision 37). Construction failures raise
    :class:`~nyc_cab.exceptions.InvalidRequestError`, which the producer
    catches to route the source row to the quarantine topic.
    """

    # pylint: disable=too-many-instance-attributes
    # The ten fields are the on-wire event contract; field count is set by
    # the contract, not by stylistic preference. The pylint default of 7 is
    # a reasonable heuristic for general classes; event dataclasses
    # routinely exceed it.

    event_id: str
    schema_version: str
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
        ("schema_version", str),
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
            (self.schema_version == SCHEMA_VERSION, "schema_version", self.schema_version),
            (self.cab_type.strip() != "", "cab_type", self.cab_type),
            (1900 <= self.year <= 2100, "year", self.year),
            (1 <= self.month <= 12, "month", self.month),
            (0 <= self.hour <= 23, "hour", self.hour),
            (self.trip_distance >= 0.0, "trip_distance", self.trip_distance),
            (self.fare_amount >= 0.0, "fare_amount", self.fare_amount),
            (0 <= self.passenger_count <= 9, "passenger_count", self.passenger_count),
            (self.produced_at.tzinfo is not None, "produced_at", self.produced_at),
        )


# --- Deterministic event_id derivation --------------------------------------

EVENT_ID_HASH_FIELDS: Final[tuple[str, ...]] = (
    "cab_type",
    "year",
    "month",
    "VendorID",
    "tpep_pickup_datetime",
    "tpep_dropoff_datetime",
    "PULocationID",
    "DOLocationID",
    "fare_amount",
    "total_amount",
)
"""Source-row fields fed into the ``event_id`` hash, in order.

The ordering is part of the contract: rerunning the producer over the same
Silver partition must produce identical ``event_id`` values, so any
permutation of this tuple is a wire-format breaking change.
"""

EVENT_ID_HASH_TRUNCATION: Final[int] = 16
"""Hex-character length of the truncated ``event_id`` (16 hex = 64 bits).

64 bits is comfortable for NYC monthly cardinality (~3M rows; effective
collision resistance at ~2**32 by the birthday bound, ~4 billion items
before a 50% collision probability).
"""

_HASH_FIELD_SEPARATOR: Final[str] = ":"
"""Separator joining hash input fields.

ISO-formatted datetimes contain colons themselves; the joiner is a known
positional marker between fields, not a tokenizer, so the embedded colons
inside individual field strings do not create ambiguity. The hash is
deterministic on the joined byte string regardless.
"""


def _format_hash_field(value: Any) -> str:
    """Canonicalize one source-row field for inclusion in the event_id hash."""
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return ""
    return str(value)


def derive_event_id(source: Mapping[str, Any]) -> str:
    """Derive a deterministic ``event_id`` from a Silver source row.

    The id is the SHA-256 hex digest, truncated to
    :data:`EVENT_ID_HASH_TRUNCATION` characters, of the ``:``-joined string
    representations of the fields in :data:`EVENT_ID_HASH_FIELDS`.
    Determinism is the load-bearing property: rerunning the producer over
    the same Silver partition must emit identical ``event_id`` values so
    that Kafka key equality lets downstream consumers detect duplicates.

    ``source`` is keyed by Silver field names. Extra keys are silently
    ignored — production callers pass a full Silver row dict containing
    many fields, and emitting a log line per call for the ignored keys
    would be hot-path noise (see design log decision 36). Missing keys
    raise :class:`KeyError`.
    """
    parts: list[str] = []
    for field in EVENT_ID_HASH_FIELDS:
        if field not in source:
            raise KeyError(f"derive_event_id missing required field: {field!r}")
        parts.append(_format_hash_field(source[field]))
    payload = _HASH_FIELD_SEPARATOR.join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:EVENT_ID_HASH_TRUNCATION]


# --- Kafka routing key ------------------------------------------------------


def event_key(event: TripCompleted) -> str:
    """Return the Kafka partition routing key for one ``TripCompleted`` event.

    The key has hour grain: ``cab_type/YYYY/MM/HH``. Hour-grain keys cause
    all events for the same ``(cab_type, year, month, hour)`` tuple to land
    on the same Kafka partition, which keeps the consumer aggregator
    partition-local (no cross-partition coordination needed for the
    ``trip_completed_hourly`` Postgres rows). See design log decision 35.
    """
    return f"{event.cab_type}/{event.year:04d}/{event.month:02d}/{event.hour:02d}"


# --- Wire-format serialization ----------------------------------------------

_PAYLOAD_FIELDS: Final[frozenset[str]] = frozenset({
    "event_id",
    "schema_version",
    "cab_type",
    "year",
    "month",
    "hour",
    "trip_distance",
    "fare_amount",
    "passenger_count",
    "produced_at",
})
"""Set of fields permitted on the JSON wire format.

Used by :func:`from_json` to reject payloads with missing or unexpected
keys (design log decision 38). Adding a field is a major-version change
and a new topic.
"""


def to_json(event: TripCompleted) -> str:
    """Serialize a ``TripCompleted`` to a JSON string.

    Datetimes are serialized via :meth:`datetime.isoformat`, producing a
    timezone-aware ISO 8601 string. The output is round-trip safe through
    :func:`from_json`.
    """
    return json.dumps({
        "event_id": event.event_id,
        "schema_version": event.schema_version,
        "cab_type": event.cab_type,
        "year": event.year,
        "month": event.month,
        "hour": event.hour,
        "trip_distance": event.trip_distance,
        "fare_amount": event.fare_amount,
        "passenger_count": event.passenger_count,
        "produced_at": event.produced_at.isoformat(),
    })


def from_json(payload: str | bytes) -> TripCompleted:
    """Deserialize a JSON payload into a validated ``TripCompleted`` event.

    Strict on every front:

    - Payload root must be a JSON object.
    - Required field set is exactly :data:`_PAYLOAD_FIELDS`; missing or
      unexpected keys raise :class:`InvalidEventPayloadError`.
    - ``schema_version`` must equal :data:`SCHEMA_VERSION` (checked before
      structural validation so the error message names the version
      mismatch rather than a generic structural failure).
    - ``produced_at`` must be a timezone-aware ISO 8601 datetime.
    - The fully-parsed payload is passed through
      :meth:`TripCompleted.create_validated`, so every structural rule
      that applies to a directly-constructed event applies here too.

    Failures at any stage raise :class:`InvalidEventPayloadError`. Consumers
    catch this exception to route the offending payload to a quarantine
    action (design log decision 38).
    """
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InvalidEventPayloadError(f"payload is not valid UTF-8: {exc}") from exc

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise InvalidEventPayloadError(f"payload is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise InvalidEventPayloadError(
            f"payload root must be a JSON object, got {type(data).__name__}"
        )

    keys = set(data.keys())
    missing = _PAYLOAD_FIELDS - keys
    if missing:
        raise InvalidEventPayloadError(
            f"payload missing required fields: {sorted(missing)}"
        )
    extra = keys - _PAYLOAD_FIELDS
    if extra:
        raise InvalidEventPayloadError(
            f"payload has unexpected fields: {sorted(extra)}"
        )

    if data["schema_version"] != SCHEMA_VERSION:
        raise InvalidEventPayloadError(
            f"unsupported schema_version: {data['schema_version']!r} "
            f"(expected {SCHEMA_VERSION!r})"
        )

    try:
        produced_at = datetime.fromisoformat(data["produced_at"])
    except (TypeError, ValueError) as exc:
        raise InvalidEventPayloadError(
            f"produced_at must be an ISO 8601 datetime string: {exc}"
        ) from exc

    if produced_at.tzinfo is None:
        raise InvalidEventPayloadError(
            "produced_at must be timezone-aware (no naive datetimes on the wire)"
        )

    try:
        return TripCompleted.create_validated(
            data["event_id"],
            data["schema_version"],
            data["cab_type"],
            data["year"],
            data["month"],
            data["hour"],
            data["trip_distance"],
            data["fare_amount"],
            data["passenger_count"],
            produced_at,
        )
    except InvalidRequestError as exc:
        raise InvalidEventPayloadError(
            f"payload fails contract validation: {exc.violations}"
        ) from exc


# --- Routing ----------------------------------------------------------------

def quarantine_topic_for(reason: EventRejectionReason) -> str:
    """Return the Kafka topic that an event with this rejection reason routes to.

    For v1 every rejection routes to the same quarantine topic. The indirection
    keeps producer code agnostic to per-reason fan-out decisions made later.
    """
    if not isinstance(reason, EventRejectionReason):
        raise TypeError(f"reason must be an EventRejectionReason, got {type(reason).__name__}")
    return TRIP_COMPLETED_QUARANTINE_TOPIC
