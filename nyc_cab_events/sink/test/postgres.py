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
    ensure_table,
    reconcile_against_silver,
    upsert_hourly_counts,
)

pytestmark = pytest.mark.unit


# --- Table identity constants -----------------------------------------------


def test_trip_completed_hourly_table_constant() -> None:
    """The table name constant is the canonical identifier."""
    assert TRIP_COMPLETED_HOURLY_TABLE == "trip_completed_hourly"


def test_trip_completed_hourly_ddl_uses_if_not_exists() -> None:
    """The DDL is idempotent via ``IF NOT EXISTS``."""
    assert "CREATE TABLE IF NOT EXISTS" in TRIP_COMPLETED_HOURLY_DDL


def test_trip_completed_hourly_ddl_declares_natural_key() -> None:
    """The DDL declares the four-column natural key."""
    assert "PRIMARY KEY (cab_type, year, month, hour)" in TRIP_COMPLETED_HOURLY_DDL


# --- PostgresSinkConfig: happy paths ----------------------------------------


def test_sink_config_happy_path() -> None:
    """A well-formed sink config constructs cleanly."""
    config = PostgresSinkConfig.create_validated(
        "localhost", 5432, "nyc_cab_events", "nyc_cab", "nyc_cab",
    )
    assert config.host == "localhost"
    assert config.port == 5432
    assert config.database == "nyc_cab_events"


def test_sink_config_accepts_empty_password() -> None:
    """Empty password is permitted (trust-auth deployments are legitimate)."""
    config = PostgresSinkConfig.create_validated(
        "localhost", 5432, "db", "user", "",
    )
    assert config.password == ""


# --- PostgresSinkConfig: type rejections ------------------------------------


def test_sink_config_rejects_non_string_host() -> None:
    """``host`` must be a string."""
    with pytest.raises(InvalidRequestError) as info:
        PostgresSinkConfig.create_validated(0, 5432, "db", "user", "pw")
    names = [v[0] for v in info.value.violations]
    assert "host" in names


def test_sink_config_rejects_bool_port() -> None:
    """``port`` rejects bool despite int compatibility."""
    with pytest.raises(InvalidRequestError) as info:
        PostgresSinkConfig.create_validated("localhost", True, "db", "user", "pw")
    assert ("port", True) in info.value.violations


def test_sink_config_rejects_non_string_database() -> None:
    """``database`` must be a string."""
    with pytest.raises(InvalidRequestError) as info:
        PostgresSinkConfig.create_validated("localhost", 5432, None, "user", "pw")
    names = [v[0] for v in info.value.violations]
    assert "database" in names


def test_sink_config_rejects_non_string_user() -> None:
    """``user`` must be a string."""
    with pytest.raises(InvalidRequestError) as info:
        PostgresSinkConfig.create_validated("localhost", 5432, "db", None, "pw")
    names = [v[0] for v in info.value.violations]
    assert "user" in names


def test_sink_config_rejects_non_string_password() -> None:
    """``password`` must be a string (None is rejected)."""
    with pytest.raises(InvalidRequestError) as info:
        PostgresSinkConfig.create_validated("localhost", 5432, "db", "user", None)
    names = [v[0] for v in info.value.violations]
    assert "password" in names


# --- PostgresSinkConfig: structural rejections ------------------------------


def test_sink_config_rejects_blank_host() -> None:
    """Blank host violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        PostgresSinkConfig.create_validated("   ", 5432, "db", "user", "pw")
    assert ("host", "   ") in info.value.violations


def test_sink_config_rejects_port_zero() -> None:
    """Port 0 is out of the 1-65535 range."""
    with pytest.raises(InvalidRequestError) as info:
        PostgresSinkConfig.create_validated("localhost", 0, "db", "user", "pw")
    assert ("port", 0) in info.value.violations


def test_sink_config_rejects_port_above_max() -> None:
    """Port 65536 is out of the 1-65535 range."""
    with pytest.raises(InvalidRequestError) as info:
        PostgresSinkConfig.create_validated("localhost", 65536, "db", "user", "pw")
    assert ("port", 65536) in info.value.violations


def test_sink_config_rejects_blank_database() -> None:
    """Blank database violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        PostgresSinkConfig.create_validated("localhost", 5432, "", "user", "pw")
    assert ("database", "") in info.value.violations


def test_sink_config_rejects_blank_user() -> None:
    """Blank user violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        PostgresSinkConfig.create_validated("localhost", 5432, "db", "", "pw")
    assert ("user", "") in info.value.violations


def test_sink_config_is_frozen() -> None:
    """``PostgresSinkConfig`` rejects attribute mutation."""
    config = PostgresSinkConfig.create_validated("localhost", 5432, "db", "user", "pw")
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.host = "other"  # type: ignore[misc]


# --- HourlyBucket -----------------------------------------------------------


def test_hourly_bucket_happy_path() -> None:
    """A well-formed bucket constructs cleanly."""
    bucket = HourlyBucket.create_validated("yellow", 2023, 1, 14, 42)
    assert bucket.cab_type == "yellow"
    assert bucket.year == 2023
    assert bucket.month == 1
    assert bucket.hour == 14
    assert bucket.event_count == 42


def test_hourly_bucket_zero_count_allowed() -> None:
    """Zero ``event_count`` is allowed (used for sentinel rows)."""
    bucket = HourlyBucket.create_validated("yellow", 2023, 1, 0, 0)
    assert bucket.event_count == 0


def test_hourly_bucket_rejects_hour_twenty_four() -> None:
    """Hour 24 is above the 0-23 range."""
    with pytest.raises(InvalidRequestError) as info:
        HourlyBucket.create_validated("yellow", 2023, 1, 24, 0)
    assert ("hour", 24) in info.value.violations


def test_hourly_bucket_rejects_negative_count() -> None:
    """Negative ``event_count`` violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        HourlyBucket.create_validated("yellow", 2023, 1, 0, -1)
    assert ("event_count", -1) in info.value.violations


def test_hourly_bucket_rejects_bool_hour() -> None:
    """``hour`` rejects bool despite int compatibility."""
    with pytest.raises(InvalidRequestError) as info:
        HourlyBucket.create_validated("yellow", 2023, 1, True, 0)
    assert ("hour", True) in info.value.violations


def test_hourly_bucket_is_frozen() -> None:
    """``HourlyBucket`` rejects attribute mutation."""
    bucket = HourlyBucket.create_validated("yellow", 2023, 1, 0, 0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        bucket.event_count = 1  # type: ignore[misc]


# --- ReconciliationResult ---------------------------------------------------


def test_reconciliation_result_matching_counts_is_reconciled() -> None:
    """Matching Silver and Postgres counts produce a reconciled result."""
    result = ReconciliationResult.create_validated(
        "yellow", 2023, 1, 1000, 1000, True,
    )
    assert result.is_reconciled is True


def test_reconciliation_result_mismatching_counts_not_reconciled() -> None:
    """Mismatching counts produce a not-reconciled result."""
    result = ReconciliationResult.create_validated(
        "yellow", 2023, 1, 1000, 999, False,
    )
    assert result.is_reconciled is False


def test_reconciliation_result_rejects_inconsistent_flag_true() -> None:
    """``is_reconciled=True`` must match the count comparison."""
    with pytest.raises(InvalidRequestError) as info:
        ReconciliationResult.create_validated(
            "yellow", 2023, 1, 1000, 999, True,
        )
    names = [v[0] for v in info.value.violations]
    assert "is_reconciled" in names


def test_reconciliation_result_rejects_inconsistent_flag_false() -> None:
    """``is_reconciled=False`` must match the count comparison."""
    with pytest.raises(InvalidRequestError) as info:
        ReconciliationResult.create_validated(
            "yellow", 2023, 1, 1000, 1000, False,
        )
    names = [v[0] for v in info.value.violations]
    assert "is_reconciled" in names


def test_reconciliation_result_rejects_negative_silver_count() -> None:
    """Negative ``silver_accepted_count`` violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        ReconciliationResult.create_validated(
            "yellow", 2023, 1, -1, 0, False,
        )
    assert ("silver_accepted_count", -1) in info.value.violations


def test_reconciliation_result_is_frozen() -> None:
    """``ReconciliationResult`` rejects attribute mutation."""
    result = ReconciliationResult.create_validated(
        "yellow", 2023, 1, 0, 0, True,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.is_reconciled = False  # type: ignore[misc]


# --- Stub coverage (decision 25) --------------------------------------------


@pytest.fixture
def sink_config() -> PostgresSinkConfig:
    """A valid sink config for stub tests."""
    return PostgresSinkConfig.create_validated(
        "localhost", 5432, "nyc_cab_events", "nyc_cab", "nyc_cab",
    )


def test_ensure_table_is_not_implemented(sink_config: PostgresSinkConfig) -> None:
    """``ensure_table`` raises ``NotImplementedError`` until implemented."""
    with pytest.raises(NotImplementedError):
        ensure_table(sink_config)


def test_upsert_hourly_counts_is_not_implemented(sink_config: PostgresSinkConfig) -> None:
    """``upsert_hourly_counts`` raises ``NotImplementedError`` until implemented."""
    with pytest.raises(NotImplementedError):
        upsert_hourly_counts(sink_config, ())


def test_reconcile_against_silver_is_not_implemented(sink_config: PostgresSinkConfig) -> None:
    """``reconcile_against_silver`` raises ``NotImplementedError`` until implemented."""
    with pytest.raises(NotImplementedError):
        reconcile_against_silver(sink_config, "yellow", 2023, 1, 0)
