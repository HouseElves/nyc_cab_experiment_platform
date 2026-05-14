"""
Postgres aggregate sink for trip-completed events.

This module owns the ``trip_completed_hourly`` table contract: table name,
DDL constant, upsert API, and reconciliation query against Silver counts.

This module currently provides:

- :class:`PostgresSinkConfig` — fully implemented validated config
- :class:`HourlyBucket` — fully implemented validated value type
- :class:`ReconciliationResult` — fully implemented validated result
- :data:`TRIP_COMPLETED_HOURLY_TABLE` and :data:`TRIP_COMPLETED_HOURLY_DDL`
- :func:`ensure_table` — stub
- :func:`upsert_hourly_counts` — stub
- :func:`reconcile_against_silver` — stub

The stubs are guarded by :class:`NotImplementedError` and corresponding
``pytest.raises`` tests (design log decision 25). The :mod:`psycopg` import
lives inside each stub and will move to module level when the stubs are
filled in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Final

from nyc_cab._validation import _Validated, CheckSpec, CheckTuple


# pylint: disable=duplicate-code
# Decision 28 (in spirit): duplication between nyc_cab and nyc_cab_events is
# tolerated until a shared-vocabulary package is justified.


# --- Table identity ---------------------------------------------------------

TRIP_COMPLETED_HOURLY_TABLE: Final[str] = "trip_completed_hourly"
"""The aggregate table holding hourly event counts."""

TRIP_COMPLETED_HOURLY_DDL: Final[str] = """
CREATE TABLE IF NOT EXISTS trip_completed_hourly (
    cab_type        TEXT        NOT NULL,
    year            INTEGER     NOT NULL,
    month           INTEGER     NOT NULL,
    hour            INTEGER     NOT NULL,
    event_count     BIGINT      NOT NULL DEFAULT 0,
    last_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (cab_type, year, month, hour)
);
""".strip()
"""DDL for the aggregate table. Idempotent via ``IF NOT EXISTS``."""


# --- Configuration ----------------------------------------------------------


@dataclass(frozen=True)
class PostgresSinkConfig(_Validated):
    """Configure connection parameters for the Postgres sink.

    Loader code (in orchestration, not here) is expected to source these from
    environment variables following the platform's no-dotenv discipline
    (design log decision 4). This dataclass is the typed, validated surface
    those loaders construct against.
    """

    host: str
    port: int
    database: str
    user: str
    password: str

    _type_check_specs: ClassVar[tuple[CheckSpec, ...]] = (
        ("host", str),
        ("port", int, bool),
        ("database", str),
        ("user", str),
        ("password", str),
    )

    def _structural_checks(self) -> tuple[CheckTuple, ...]:
        """Return structural validation rules for the Postgres config."""
        return (
            (self.host.strip() != "", "host", self.host),
            (1 <= self.port <= 65535, "port", self.port),
            (self.database.strip() != "", "database", self.database),
            (self.user.strip() != "", "user", self.user),
        )


# --- Value types ------------------------------------------------------------


@dataclass(frozen=True)
class HourlyBucket(_Validated):
    """One row's worth of aggregate data for upsert into ``trip_completed_hourly``."""

    cab_type: str
    year: int
    month: int
    hour: int
    event_count: int

    _type_check_specs: ClassVar[tuple[CheckSpec, ...]] = (
        ("cab_type", str),
        ("year", int, bool),
        ("month", int, bool),
        ("hour", int, bool),
        ("event_count", int, bool),
    )

    def _structural_checks(self) -> tuple[CheckTuple, ...]:
        """Return structural validation rules for one aggregate row."""
        return (
            (self.cab_type.strip() != "", "cab_type", self.cab_type),
            (1900 <= self.year <= 2100, "year", self.year),
            (1 <= self.month <= 12, "month", self.month),
            (0 <= self.hour <= 23, "hour", self.hour),
            (self.event_count >= 0, "event_count", self.event_count),
        )


@dataclass(frozen=True)
class ReconciliationResult(_Validated):
    """Describe a reconciliation comparison for one monthly slice.

    ``is_reconciled`` is derivable from the two counts; it is materialized on
    the dataclass so reconciliation reports can be persisted or logged
    without recomputing the comparison.
    """

    cab_type: str
    year: int
    month: int
    silver_accepted_count: int
    postgres_event_count: int
    is_reconciled: bool

    _type_check_specs: ClassVar[tuple[CheckSpec, ...]] = (
        ("cab_type", str),
        ("year", int, bool),
        ("month", int, bool),
        ("silver_accepted_count", int, bool),
        ("postgres_event_count", int, bool),
        ("is_reconciled", bool),
    )

    def _structural_checks(self) -> tuple[CheckTuple, ...]:
        """Return structural validation rules including derivation consistency."""
        return (
            (self.cab_type.strip() != "", "cab_type", self.cab_type),
            (1900 <= self.year <= 2100, "year", self.year),
            (1 <= self.month <= 12, "month", self.month),
            (self.silver_accepted_count >= 0, "silver_accepted_count", self.silver_accepted_count),
            (self.postgres_event_count >= 0, "postgres_event_count", self.postgres_event_count),
            (
                self.is_reconciled == (self.silver_accepted_count == self.postgres_event_count),
                "is_reconciled",
                self.is_reconciled,
            ),
        )


# --- Stubs ------------------------------------------------------------------


def ensure_table(sink_config: PostgresSinkConfig) -> None:
    """Apply ``TRIP_COMPLETED_HOURLY_DDL`` against the configured database.

    Idempotent: the DDL uses ``CREATE TABLE IF NOT EXISTS``, so repeated calls
    converge to the same schema state. Intended to run once per consumer
    startup before the first upsert.
    """
    # pylint: disable=unused-argument
    raise NotImplementedError(
        "ensure_table is a scaffolding stub; see module docstring and design log decision 25."
    )


def upsert_hourly_counts(
    sink_config: PostgresSinkConfig,
    buckets: tuple[HourlyBucket, ...],
) -> int:
    """Upsert aggregate counts on the natural key ``(cab_type, year, month, hour)``.

    Uses ``INSERT ... ON CONFLICT (cab_type, year, month, hour) DO UPDATE SET
    event_count = EXCLUDED.event_count, last_updated_at = NOW()`` so consumer
    re-runs converge on the same row state. Returns the total row count
    affected (inserts plus updates).
    """
    # pylint: disable=unused-argument
    raise NotImplementedError(
        "upsert_hourly_counts is a scaffolding stub; see module docstring "
        "and design log decision 25."
    )


def reconcile_against_silver(
    sink_config: PostgresSinkConfig,
    cab_type: str,
    year: int,
    month: int,
    silver_accepted_count: int,
) -> ReconciliationResult:
    """Compare Postgres hourly counts (summed by month) against a Silver accepted count.

    Issues ``SELECT SUM(event_count) FROM trip_completed_hourly WHERE
    cab_type = %s AND year = %s AND month = %s`` and builds a
    :class:`ReconciliationResult` against the provided
    ``silver_accepted_count``. A ``NULL`` sum (no rows yet for the slice) is
    treated as a zero Postgres count.
    """
    # pylint: disable=unused-argument
    raise NotImplementedError(
        "reconcile_against_silver is a scaffolding stub; see module docstring "
        "and design log decision 25."
    )
