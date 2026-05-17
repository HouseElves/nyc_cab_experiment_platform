# pylint: disable=redefined-outer-name
"""Tests for :mod:`nyc_cab_events.sink.postgres`."""

from __future__ import annotations

import dataclasses

import pytest

from nyc_cab.exceptions import InvalidRequestError
from nyc_cab_events.sink.postgres import (
    TRIP_COMPLETED_HOURLY_DDL,
    TRIP_COMPLETED_HOURLY_TABLE,
    HourlyBucket,
    PostgresSinkConfig,
    ReconciliationResult,
    _connect,
    ensure_table,
    reconcile_against_silver,
    upsert_hourly_counts,
)


# Module-level ``pytestmark = pytest.mark.unit`` is intentionally absent.
# Unit-tier tests are marked individually with ``@pytest.mark.unit`` so the
# ``@pytest.mark.postgres`` integration tests further down do not inherit
# the ``unit`` mark; a module-level mark would make ``pytest -m unit`` try
# to run the live-Postgres tests.


# --- Table identity constants -----------------------------------------------


@pytest.mark.unit
def test_trip_completed_hourly_table_constant() -> None:
    """The table name constant is the canonical identifier."""
    assert TRIP_COMPLETED_HOURLY_TABLE == "trip_completed_hourly"


@pytest.mark.unit
def test_trip_completed_hourly_ddl_uses_if_not_exists() -> None:
    """The DDL is idempotent via ``IF NOT EXISTS``."""
    assert "CREATE TABLE IF NOT EXISTS" in TRIP_COMPLETED_HOURLY_DDL


@pytest.mark.unit
def test_trip_completed_hourly_ddl_declares_natural_key() -> None:
    """The DDL declares the four-column natural key."""
    assert "PRIMARY KEY (cab_type, year, month, hour)" in TRIP_COMPLETED_HOURLY_DDL


# --- PostgresSinkConfig: happy paths ----------------------------------------


@pytest.mark.unit
def test_sink_config_happy_path() -> None:
    """A well-formed sink config constructs cleanly."""
    config = PostgresSinkConfig.create_validated(
        "localhost", 5432, "nyc_cab_events", "nyc_cab", "nyc_cab",
    )
    assert config.host == "localhost"
    assert config.port == 5432
    assert config.database == "nyc_cab_events"


@pytest.mark.unit
def test_sink_config_accepts_empty_password() -> None:
    """Empty password is permitted (trust-auth deployments are legitimate)."""
    config = PostgresSinkConfig.create_validated(
        "localhost", 5432, "db", "user", "",
    )
    assert config.password == ""


# --- PostgresSinkConfig: type rejections ------------------------------------


@pytest.mark.unit
def test_sink_config_rejects_non_string_host() -> None:
    """``host`` must be a string."""
    with pytest.raises(InvalidRequestError) as info:
        PostgresSinkConfig.create_validated(0, 5432, "db", "user", "pw")
    names = [v[0] for v in info.value.violations]
    assert "host" in names


@pytest.mark.unit
def test_sink_config_rejects_bool_port() -> None:
    """``port`` rejects bool despite int compatibility."""
    with pytest.raises(InvalidRequestError) as info:
        PostgresSinkConfig.create_validated("localhost", True, "db", "user", "pw")
    assert ("port", True) in info.value.violations


@pytest.mark.unit
def test_sink_config_rejects_non_string_database() -> None:
    """``database`` must be a string."""
    with pytest.raises(InvalidRequestError) as info:
        PostgresSinkConfig.create_validated("localhost", 5432, None, "user", "pw")
    names = [v[0] for v in info.value.violations]
    assert "database" in names


@pytest.mark.unit
def test_sink_config_rejects_non_string_user() -> None:
    """``user`` must be a string."""
    with pytest.raises(InvalidRequestError) as info:
        PostgresSinkConfig.create_validated("localhost", 5432, "db", None, "pw")
    names = [v[0] for v in info.value.violations]
    assert "user" in names


@pytest.mark.unit
def test_sink_config_rejects_non_string_password() -> None:
    """``password`` must be a string (None is rejected)."""
    with pytest.raises(InvalidRequestError) as info:
        PostgresSinkConfig.create_validated("localhost", 5432, "db", "user", None)
    names = [v[0] for v in info.value.violations]
    assert "password" in names


# --- PostgresSinkConfig: structural rejections ------------------------------


@pytest.mark.unit
def test_sink_config_rejects_blank_host() -> None:
    """Blank host violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        PostgresSinkConfig.create_validated("   ", 5432, "db", "user", "pw")
    assert ("host", "   ") in info.value.violations


@pytest.mark.unit
def test_sink_config_rejects_port_zero() -> None:
    """Port 0 is out of the 1-65535 range."""
    with pytest.raises(InvalidRequestError) as info:
        PostgresSinkConfig.create_validated("localhost", 0, "db", "user", "pw")
    assert ("port", 0) in info.value.violations


@pytest.mark.unit
def test_sink_config_rejects_port_above_max() -> None:
    """Port 65536 is out of the 1-65535 range."""
    with pytest.raises(InvalidRequestError) as info:
        PostgresSinkConfig.create_validated("localhost", 65536, "db", "user", "pw")
    assert ("port", 65536) in info.value.violations


@pytest.mark.unit
def test_sink_config_rejects_blank_database() -> None:
    """Blank database violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        PostgresSinkConfig.create_validated("localhost", 5432, "", "user", "pw")
    assert ("database", "") in info.value.violations


@pytest.mark.unit
def test_sink_config_rejects_blank_user() -> None:
    """Blank user violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        PostgresSinkConfig.create_validated("localhost", 5432, "db", "", "pw")
    assert ("user", "") in info.value.violations


@pytest.mark.unit
def test_sink_config_is_frozen() -> None:
    """``PostgresSinkConfig`` rejects attribute mutation."""
    config = PostgresSinkConfig.create_validated("localhost", 5432, "db", "user", "pw")
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.host = "other"  # type: ignore[misc]


# --- HourlyBucket -----------------------------------------------------------


@pytest.mark.unit
def test_hourly_bucket_happy_path() -> None:
    """A well-formed bucket constructs cleanly."""
    bucket = HourlyBucket.create_validated("yellow", 2023, 1, 14, 42)
    assert bucket.cab_type == "yellow"
    assert bucket.year == 2023
    assert bucket.month == 1
    assert bucket.hour == 14
    assert bucket.event_count == 42


@pytest.mark.unit
def test_hourly_bucket_zero_count_allowed() -> None:
    """Zero ``event_count`` is allowed (used for sentinel rows)."""
    bucket = HourlyBucket.create_validated("yellow", 2023, 1, 0, 0)
    assert bucket.event_count == 0


@pytest.mark.unit
def test_hourly_bucket_rejects_hour_twenty_four() -> None:
    """Hour 24 is above the 0-23 range."""
    with pytest.raises(InvalidRequestError) as info:
        HourlyBucket.create_validated("yellow", 2023, 1, 24, 0)
    assert ("hour", 24) in info.value.violations


@pytest.mark.unit
def test_hourly_bucket_rejects_negative_count() -> None:
    """Negative ``event_count`` violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        HourlyBucket.create_validated("yellow", 2023, 1, 0, -1)
    assert ("event_count", -1) in info.value.violations


@pytest.mark.unit
def test_hourly_bucket_rejects_bool_hour() -> None:
    """``hour`` rejects bool despite int compatibility."""
    with pytest.raises(InvalidRequestError) as info:
        HourlyBucket.create_validated("yellow", 2023, 1, True, 0)
    assert ("hour", True) in info.value.violations


@pytest.mark.unit
def test_hourly_bucket_is_frozen() -> None:
    """``HourlyBucket`` rejects attribute mutation."""
    bucket = HourlyBucket.create_validated("yellow", 2023, 1, 0, 0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        bucket.event_count = 1  # type: ignore[misc]


# --- ReconciliationResult ---------------------------------------------------


@pytest.mark.unit
def test_reconciliation_result_matching_counts_is_reconciled() -> None:
    """Matching Silver and Postgres counts produce a reconciled result."""
    result = ReconciliationResult.create_validated(
        "yellow", 2023, 1, 1000, 1000, True,
    )
    assert result.is_reconciled is True


@pytest.mark.unit
def test_reconciliation_result_mismatching_counts_not_reconciled() -> None:
    """Mismatching counts produce a not-reconciled result."""
    result = ReconciliationResult.create_validated(
        "yellow", 2023, 1, 1000, 999, False,
    )
    assert result.is_reconciled is False


@pytest.mark.unit
def test_reconciliation_result_rejects_inconsistent_flag_true() -> None:
    """``is_reconciled=True`` must match the count comparison."""
    with pytest.raises(InvalidRequestError) as info:
        ReconciliationResult.create_validated(
            "yellow", 2023, 1, 1000, 999, True,
        )
    names = [v[0] for v in info.value.violations]
    assert "is_reconciled" in names


@pytest.mark.unit
def test_reconciliation_result_rejects_inconsistent_flag_false() -> None:
    """``is_reconciled=False`` must match the count comparison."""
    with pytest.raises(InvalidRequestError) as info:
        ReconciliationResult.create_validated(
            "yellow", 2023, 1, 1000, 1000, False,
        )
    names = [v[0] for v in info.value.violations]
    assert "is_reconciled" in names


@pytest.mark.unit
def test_reconciliation_result_rejects_negative_silver_count() -> None:
    """Negative ``silver_accepted_count`` violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        ReconciliationResult.create_validated(
            "yellow", 2023, 1, -1, 0, False,
        )
    assert ("silver_accepted_count", -1) in info.value.violations


@pytest.mark.unit
def test_reconciliation_result_is_frozen() -> None:
    """``ReconciliationResult`` rejects attribute mutation."""
    result = ReconciliationResult.create_validated(
        "yellow", 2023, 1, 0, 0, True,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.is_reconciled = False  # type: ignore[misc]


# --- Fake psycopg connection for unit-tier tests ----------------------------


class _FakeCursor:
    """Recording stand-in for ``psycopg.Cursor``.

    Captures every ``execute`` and ``executemany`` call, plus the
    ``fetchone`` value for SELECT-style tests. Context-manager protocol
    is implemented as no-op enter/exit so the production code's
    ``with cur:`` blocks work unchanged.
    """

    def __init__(self) -> None:
        self.executed: list[tuple[str, object]] = []
        self.executemany_calls: list[tuple[str, list]] = []
        self.fetchone_value: object = (0,)
        self.rowcount: int = 0

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, sql: str, params: object = None) -> None:
        """Record one execute call."""
        self.executed.append((sql, params))

    def executemany(self, sql: str, params_list: object) -> None:
        """Record one executemany call and set ``rowcount`` to the batch size."""
        as_list = list(params_list)
        self.executemany_calls.append((sql, as_list))
        self.rowcount = len(as_list)

    def fetchone(self) -> object:
        """Return whatever the test has set as the fetchone value."""
        return self.fetchone_value


class _FakeConnection:
    """Recording stand-in for ``psycopg.Connection``."""

    def __init__(self) -> None:
        self.cursor_instance = _FakeCursor()
        self.commits = 0

    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def cursor(self) -> _FakeCursor:
        """Return the single fake cursor (one per connection in this fake)."""
        return self.cursor_instance

    def commit(self) -> None:
        """Increment the commit counter."""
        self.commits += 1


@pytest.fixture
def sink_config() -> PostgresSinkConfig:
    """A valid sink config for unit tests."""
    return PostgresSinkConfig.create_validated(
        "localhost", 5432, "nyc_cab_events", "nyc_cab", "nyc_cab",
    )


@pytest.fixture
def fake_conn(monkeypatch: pytest.MonkeyPatch) -> _FakeConnection:
    """Monkeypatch the psycopg connect factory to return a recording fake."""
    conn = _FakeConnection()
    monkeypatch.setattr(
        "nyc_cab_events.sink.postgres._connect",
        lambda _cfg: conn,
    )
    return conn


# --- ensure_table -----------------------------------------------------------


@pytest.mark.unit
def test_ensure_table_executes_ddl(
    sink_config: PostgresSinkConfig, fake_conn: _FakeConnection,
) -> None:
    """``ensure_table`` issues exactly the DDL constant against the connection."""
    ensure_table(sink_config)
    assert len(fake_conn.cursor_instance.executed) == 1
    sql, params = fake_conn.cursor_instance.executed[0]
    assert sql == TRIP_COMPLETED_HOURLY_DDL
    assert params is None


@pytest.mark.unit
def test_ensure_table_commits(
    sink_config: PostgresSinkConfig, fake_conn: _FakeConnection,
) -> None:
    """``ensure_table`` commits the DDL within the connection's transaction."""
    ensure_table(sink_config)
    assert fake_conn.commits == 1


# --- upsert_hourly_counts ---------------------------------------------------


@pytest.mark.unit
def test_upsert_hourly_counts_empty_is_noop(
    sink_config: PostgresSinkConfig, fake_conn: _FakeConnection,
) -> None:
    """An empty buckets sequence does not open the connection and returns zero."""
    result = upsert_hourly_counts(sink_config, ())
    assert result == 0
    # The fake_conn fixture is here for monkeypatch installation; the
    # production code should not have used it.
    assert fake_conn.cursor_instance.executemany_calls == []


@pytest.mark.unit
def test_upsert_hourly_counts_executes_one_executemany(
    sink_config: PostgresSinkConfig, fake_conn: _FakeConnection,
) -> None:
    """``upsert_hourly_counts`` makes one ``executemany`` call across all buckets."""
    buckets = (
        HourlyBucket.create_validated("yellow", 2023, 1, 14, 100),
        HourlyBucket.create_validated("yellow", 2023, 1, 15, 80),
    )
    upsert_hourly_counts(sink_config, buckets)
    assert len(fake_conn.cursor_instance.executemany_calls) == 1


@pytest.mark.unit
def test_upsert_hourly_counts_uses_overwrite_on_conflict_clause(
    sink_config: PostgresSinkConfig, fake_conn: _FakeConnection,
) -> None:
    """The upsert SQL uses ``ON CONFLICT ... DO UPDATE SET event_count = EXCLUDED.event_count``."""
    buckets = (HourlyBucket.create_validated("yellow", 2023, 1, 14, 100),)
    upsert_hourly_counts(sink_config, buckets)
    sql, _params = fake_conn.cursor_instance.executemany_calls[0]
    assert "ON CONFLICT (cab_type, year, month, hour) DO UPDATE" in sql
    assert "event_count = EXCLUDED.event_count" in sql


@pytest.mark.unit
def test_upsert_hourly_counts_binds_bucket_fields_in_pk_order(
    sink_config: PostgresSinkConfig, fake_conn: _FakeConnection,
) -> None:
    """Bucket fields bind to the SQL placeholders in PK order plus event_count."""
    buckets = (HourlyBucket.create_validated("yellow", 2023, 1, 14, 100),)
    upsert_hourly_counts(sink_config, buckets)
    _sql, params = fake_conn.cursor_instance.executemany_calls[0]
    assert params == [("yellow", 2023, 1, 14, 100)]


@pytest.mark.unit
def test_upsert_hourly_counts_returns_rowcount(
    sink_config: PostgresSinkConfig, fake_conn: _FakeConnection,
) -> None:
    """``upsert_hourly_counts`` returns ``cursor.rowcount`` (one row per bucket)."""
    buckets = tuple(
        HourlyBucket.create_validated("yellow", 2023, 1, h, 10 * h)
        for h in range(5)
    )
    result = upsert_hourly_counts(sink_config, buckets)
    assert result == 5
    _ = fake_conn  # silence unused-var lint; fixture is required for monkeypatch


@pytest.mark.unit
def test_upsert_hourly_counts_commits(
    sink_config: PostgresSinkConfig, fake_conn: _FakeConnection,
) -> None:
    """A non-empty upsert commits the transaction."""
    buckets = (HourlyBucket.create_validated("yellow", 2023, 1, 14, 100),)
    upsert_hourly_counts(sink_config, buckets)
    assert fake_conn.commits == 1


# --- reconcile_against_silver -----------------------------------------------


@pytest.mark.unit
def test_reconcile_against_silver_executes_slice_bounded_sum(
    sink_config: PostgresSinkConfig, fake_conn: _FakeConnection,
) -> None:
    """The reconcile query is bounded by the (cab_type, year, month) slice."""
    fake_conn.cursor_instance.fetchone_value = (1000,)
    reconcile_against_silver(sink_config, "yellow", 2023, 1, 1000)
    sql, params = fake_conn.cursor_instance.executed[0]
    assert "SELECT COALESCE(SUM(event_count), 0)" in sql
    assert "WHERE cab_type = %s AND year = %s AND month = %s" in sql
    assert params == ("yellow", 2023, 1)


@pytest.mark.unit
def test_reconcile_against_silver_returns_reconciled_when_equal(
    sink_config: PostgresSinkConfig, fake_conn: _FakeConnection,
) -> None:
    """Equal counts yield ``is_reconciled=True``."""
    fake_conn.cursor_instance.fetchone_value = (1000,)
    result = reconcile_against_silver(sink_config, "yellow", 2023, 1, 1000)
    assert result.silver_accepted_count == 1000
    assert result.postgres_event_count == 1000
    assert result.is_reconciled is True


@pytest.mark.unit
def test_reconcile_against_silver_returns_unreconciled_when_different(
    sink_config: PostgresSinkConfig, fake_conn: _FakeConnection,
) -> None:
    """Different counts yield ``is_reconciled=False``."""
    fake_conn.cursor_instance.fetchone_value = (999,)
    result = reconcile_against_silver(sink_config, "yellow", 2023, 1, 1000)
    assert result.silver_accepted_count == 1000
    assert result.postgres_event_count == 999
    assert result.is_reconciled is False


@pytest.mark.unit
def test_reconcile_against_silver_treats_empty_slice_as_zero(
    sink_config: PostgresSinkConfig, fake_conn: _FakeConnection,
) -> None:
    """An empty slice (Postgres sum=0) reconciles against a Silver count of zero."""
    fake_conn.cursor_instance.fetchone_value = (0,)
    result = reconcile_against_silver(sink_config, "yellow", 2023, 1, 0)
    assert result.postgres_event_count == 0
    assert result.is_reconciled is True


@pytest.mark.unit
def test_reconcile_against_silver_unreconciles_zero_against_nonzero(
    sink_config: PostgresSinkConfig, fake_conn: _FakeConnection,
) -> None:
    """An empty slice does not reconcile against a non-zero Silver count."""
    fake_conn.cursor_instance.fetchone_value = (0,)
    result = reconcile_against_silver(sink_config, "yellow", 2023, 1, 1000)
    assert result.postgres_event_count == 0
    assert result.is_reconciled is False


@pytest.mark.unit
def test_reconcile_against_silver_returns_validated_result(
    sink_config: PostgresSinkConfig, fake_conn: _FakeConnection,
) -> None:
    """The returned :class:`ReconciliationResult` has gone through ``create_validated``."""
    fake_conn.cursor_instance.fetchone_value = (1000,)
    result = reconcile_against_silver(sink_config, "yellow", 2023, 1, 1000)
    assert isinstance(result, ReconciliationResult)
    assert result.cab_type == "yellow"
    assert result.year == 2023
    assert result.month == 1


# --- Postgres-tier integration tests ----------------------------------------
# These run against a live Postgres broker (docker-compose) and are
# deselected without it.


@pytest.fixture
def live_sink_config() -> PostgresSinkConfig:
    """Sink config pointed at the docker-compose Postgres instance."""
    return PostgresSinkConfig.create_validated(
        "localhost", 5432, "nyc_cab_events", "nyc_cab", "nyc_cab",
    )


@pytest.fixture
def clean_table(live_sink_config: PostgresSinkConfig):
    """Yield with the trip_completed_hourly table present and empty.

    The table is created (idempotent) before the test, truncated, then
    truncated again after. Tests within this fixture can assume a fresh
    aggregate state. Uses the sink module's :func:`_connect` primitive
    rather than building a DSN string — see the ``to_dsn`` docstring for
    why the string form is display-only.
    """
    ensure_table(live_sink_config)
    with _connect(live_sink_config) as conn:
        with conn.cursor() as cur:
            cur.execute(f"TRUNCATE TABLE {TRIP_COMPLETED_HOURLY_TABLE}")
        conn.commit()
    yield
    with _connect(live_sink_config) as conn:
        with conn.cursor() as cur:
            cur.execute(f"TRUNCATE TABLE {TRIP_COMPLETED_HOURLY_TABLE}")
        conn.commit()


@pytest.mark.postgres
def test_postgres_ensure_table_is_idempotent(live_sink_config: PostgresSinkConfig) -> None:
    """``ensure_table`` can be called twice without error."""
    ensure_table(live_sink_config)
    ensure_table(live_sink_config)


@pytest.mark.postgres
def test_postgres_upsert_round_trips_through_reconcile(
    live_sink_config: PostgresSinkConfig, clean_table,
) -> None:
    """Upsert then reconcile: the sum of upserted counts matches the Silver count."""
    _ = clean_table
    buckets = (
        HourlyBucket.create_validated("yellow", 2023, 1, 0, 100),
        HourlyBucket.create_validated("yellow", 2023, 1, 1, 200),
        HourlyBucket.create_validated("yellow", 2023, 1, 2, 300),
    )
    upsert_hourly_counts(live_sink_config, buckets)
    result = reconcile_against_silver(live_sink_config, "yellow", 2023, 1, 600)
    assert result.postgres_event_count == 600
    assert result.is_reconciled is True


@pytest.mark.postgres
def test_postgres_upsert_overwrites_on_conflict(
    live_sink_config: PostgresSinkConfig, clean_table,
) -> None:
    """A second upsert with the same PK overwrites the prior value (decision 42)."""
    _ = clean_table
    first = (HourlyBucket.create_validated("yellow", 2023, 1, 0, 100),)
    upsert_hourly_counts(live_sink_config, first)
    second = (HourlyBucket.create_validated("yellow", 2023, 1, 0, 250),)
    upsert_hourly_counts(live_sink_config, second)
    result = reconcile_against_silver(live_sink_config, "yellow", 2023, 1, 250)
    assert result.postgres_event_count == 250
    assert result.is_reconciled is True


@pytest.mark.postgres
def test_postgres_reconcile_empty_slice_is_zero(
    live_sink_config: PostgresSinkConfig, clean_table,
) -> None:
    """A slice with no upserts reconciles at zero."""
    _ = clean_table
    result = reconcile_against_silver(live_sink_config, "yellow", 2099, 12, 0)
    assert result.postgres_event_count == 0
    assert result.is_reconciled is True
