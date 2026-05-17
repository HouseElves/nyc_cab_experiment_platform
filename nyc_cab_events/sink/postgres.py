"""
Postgres aggregate sink for trip-completed events.

This module owns the ``trip_completed_hourly`` table contract: table name,
DDL constant, upsert API, and reconciliation query against Silver counts.

The implementation is layered:

- :data:`TRIP_COMPLETED_HOURLY_DDL` — the table's DDL with
  ``IF NOT EXISTS`` plus CHECK constraints mirroring the
  :class:`HourlyBucket` structural rules at the DB layer.
- :class:`PostgresSinkConfig` — validated connection parameters.
- :class:`HourlyBucket` — validated value type for one upsert row.
- :class:`ReconciliationResult` — validated comparison result with
  the derivation invariant ``is_reconciled == (silver == postgres)``
  enforced structurally.
- :func:`_connect` — factory returning an open ``psycopg.Connection``.
  Module-level so tests can monkeypatch it to inject a fake connection.
- :func:`ensure_table` — apply the DDL idempotently.
- :func:`upsert_hourly_counts` — overwrite-on-conflict upsert per
  design log decision 42. ``ON CONFLICT (cab_type, year, month, hour)
  DO UPDATE SET event_count = EXCLUDED.event_count`` makes the consumer's
  complete-slice computation the single source of truth for the row's
  count; existing values are overwritten, not accumulated.
- :func:`reconcile_against_silver` — slice-bounded
  ``SELECT COALESCE(SUM(event_count), 0)`` query, builds a
  :class:`ReconciliationResult` against the caller's
  ``silver_accepted_count``.

Connection management is per-call: each function opens a connection, runs
its work inside a transaction, commits, and closes. Postgres connection
setup is fast at our cadence (one connection per consumer batch, plus
one per reconciliation query); a managed pool would be over-engineering
for this scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Final, Sequence

import psycopg

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
    year            SMALLINT    NOT NULL CHECK (year BETWEEN 1900 AND 2100),
    month           SMALLINT    NOT NULL CHECK (month BETWEEN 1 AND 12),
    hour            SMALLINT    NOT NULL CHECK (hour BETWEEN 0 AND 23),
    event_count     BIGINT      NOT NULL CHECK (event_count >= 0),
    last_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (cab_type, year, month, hour)
);
""".strip()
"""DDL for the aggregate table. Idempotent via ``IF NOT EXISTS``. The
CHECK constraints mirror :class:`HourlyBucket`'s structural rules at the
database layer — defense-in-depth against an out-of-band writer."""


_UPSERT_SQL: Final[str] = """
INSERT INTO trip_completed_hourly
    (cab_type, year, month, hour, event_count, last_updated_at)
VALUES (%s, %s, %s, %s, %s, NOW())
ON CONFLICT (cab_type, year, month, hour) DO UPDATE
    SET event_count = EXCLUDED.event_count,
        last_updated_at = EXCLUDED.last_updated_at
""".strip()
"""Overwrite-on-conflict upsert per design log decision 42. The
consumer's complete-slice computation replaces the existing row's count
on every successful run; partial writes are not supported by the
contract."""


_RECONCILE_SQL: Final[str] = """
SELECT COALESCE(SUM(event_count), 0)
FROM trip_completed_hourly
WHERE cab_type = %s AND year = %s AND month = %s
""".strip()
"""Slice-bounded sum query for reconciliation against a Silver
accepted count. ``COALESCE`` collapses the empty-slice case to zero so
the caller does not need to special-case ``NULL``."""


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

    def to_dsn(self) -> str:
        """Return a libpq-style DSN string for display, logging, and
        diagnostic purposes only.

        The password is unconditionally redacted to ``***`` so the
        returned string is safe to log. Connection opening is done in
        :func:`_connect` via keyword arguments (design log decision 43);
        the keyword-argument path delegates quoting and escaping to
        psycopg and is the only connection primitive in the codebase.
        This method must not be used as the input to
        :func:`psycopg.connect`.
        """
        return (
            f"host={self.host} port={self.port} dbname={self.database} "
            f"user={self.user} password=***"
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


# --- Connection factory -----------------------------------------------------


def _connect(sink_config: PostgresSinkConfig) -> psycopg.Connection:
    """Open a psycopg3 connection from a sink config.

    Module-level so tests can monkeypatch it to return a recording fake.
    Production callers use the returned connection inside ``with`` to get
    transactional commit-on-success / rollback-on-exception semantics.

    Connection parameters are passed as keyword arguments rather than as
    a DSN string. String-built DSNs are fragile in the presence of
    spaces or libpq-significant characters (``=``, ``'``, ``\\``) in
    passwords, users, or database names; the keyword form lets psycopg
    do the quoting. :meth:`PostgresSinkConfig.to_dsn` is retained for
    display, logging, and diagnostic purposes only — not for opening
    connections.
    """
    return psycopg.connect(
        host=sink_config.host,
        port=sink_config.port,
        dbname=sink_config.database,
        user=sink_config.user,
        password=sink_config.password,
    )


# --- Implementations --------------------------------------------------------


def ensure_table(sink_config: PostgresSinkConfig) -> None:
    """Apply ``TRIP_COMPLETED_HOURLY_DDL`` against the configured database.

    Idempotent: the DDL uses ``CREATE TABLE IF NOT EXISTS``, so repeated calls
    converge to the same schema state. The CHECK constraints in the DDL
    mirror :class:`HourlyBucket`'s structural rules at the DB layer.
    Intended to run once per consumer startup before the first upsert.
    """
    with _connect(sink_config) as conn:
        with conn.cursor() as cur:
            cur.execute(TRIP_COMPLETED_HOURLY_DDL)
        conn.commit()


def upsert_hourly_counts(
    sink_config: PostgresSinkConfig,
    buckets: Sequence[HourlyBucket],
) -> int:
    """Overwrite-on-conflict upsert per design log decision 42.

    Each bucket is upserted via ``ON CONFLICT (cab_type, year, month,
    hour) DO UPDATE SET event_count = EXCLUDED.event_count``. The
    consumer's complete-slice count replaces the row's existing value;
    partial writes are not supported by the contract. Returns the number
    of rows affected (one per bucket — either inserted or updated).

    An empty ``buckets`` sequence is a no-op and returns zero.
    """
    if not buckets:
        return 0

    params = [
        (b.cab_type, b.year, b.month, b.hour, b.event_count)
        for b in buckets
    ]

    with _connect(sink_config) as conn:
        with conn.cursor() as cur:
            cur.executemany(_UPSERT_SQL, params)
            rows_affected = cur.rowcount
        conn.commit()

    return rows_affected


def reconcile_against_silver(
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    # Five arguments are all genuinely required: sink config, three-field
    # slice identity, and the Silver count to compare against. Slice
    # metadata may bundle into a shared dataclass when decision 28's
    # vocabulary trigger fires.
    sink_config: PostgresSinkConfig,
    cab_type: str,
    year: int,
    month: int,
    silver_accepted_count: int,
) -> ReconciliationResult:
    """Compare Postgres hourly counts (summed by month) against a Silver accepted count.

    Issues a slice-bounded ``SELECT COALESCE(SUM(event_count), 0)`` and
    builds a :class:`ReconciliationResult` against the provided
    ``silver_accepted_count``. An empty slice (no rows yet) is treated as
    a Postgres count of zero, so the result is well-defined even on a
    fresh database.
    """
    with _connect(sink_config) as conn:
        with conn.cursor() as cur:
            cur.execute(_RECONCILE_SQL, (cab_type, year, month))
            row = cur.fetchone()
        # No commit needed for a SELECT; the with-block closes cleanly.

    postgres_event_count = int(row[0]) if row is not None else 0

    return ReconciliationResult.create_validated(
        cab_type,
        year,
        month,
        silver_accepted_count,
        postgres_event_count,
        silver_accepted_count == postgres_event_count,
    )
