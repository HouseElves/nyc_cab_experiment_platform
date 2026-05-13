# pylint: disable=redefined-outer-name
# pylint: disable=duplicate-code
# See design_log.md decision 28: Spark session fixture duplication across Silver test modules
# is intentional. The shared abstraction will be extracted when the test infrastructure stabilises.
"""Tests for :mod:`nyc_cab.transform.silver_validators`.

Each function is tested in isolation using small inline DataFrames. The
pre-normalization tests use Bronze-typed columns (double passenger_count,
double RatecodeID). The post-normalization tests use Silver-typed columns
(int passenger_count, int RatecodeID) with a pre-initialized rejection
column, mirroring the pipeline order that the entrypoint enforces.
"""

from __future__ import annotations

import datetime

import pytest
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    ArrayType,
    DoubleType,
    IntegerType,
    StringType,
    StructField,
    StructType,
    TimestampNTZType,
)

from nyc_cab.contracts.silver import SILVER_REJECTION_COLUMN, RejectionReason
from nyc_cab.transform.silver_validators import (
    apply_post_normalization_constraints,
    apply_pre_normalization_constraints,
    split_accepted_rejected,
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

# Bronze-typed: passenger_count and RatecodeID are double.
# Used for apply_pre_normalization_constraints tests.
_BRONZE_SCHEMA = StructType([
    StructField("passenger_count",       DoubleType(),       True),
    StructField("RatecodeID",            DoubleType(),       True),
    StructField("fare_amount",           DoubleType(),       True),
    StructField("trip_distance",         DoubleType(),       True),
    StructField("tpep_pickup_datetime",  TimestampNTZType(), True),
    StructField("tpep_dropoff_datetime", TimestampNTZType(), True),
])

# Silver-typed: passenger_count and RatecodeID are int (post-normalization).
# Used for apply_post_normalization_constraints and split_accepted_rejected tests.
_SILVER_SCHEMA = StructType([
    StructField("passenger_count",       IntegerType(),      True),
    StructField("RatecodeID",            IntegerType(),      True),
    StructField("fare_amount",           DoubleType(),       True),
    StructField("trip_distance",         DoubleType(),       True),
    StructField("tpep_pickup_datetime",  TimestampNTZType(), True),
    StructField("tpep_dropoff_datetime", TimestampNTZType(), True),
])

# Sentinel timestamps for pickup/dropoff tests.
_PICKUP          = datetime.datetime(2023, 1, 15, 10, 0, 0)
_DROPOFF         = datetime.datetime(2023, 1, 15, 11, 0, 0)
_REVERSED_PICKUP = datetime.datetime(2023, 1, 15, 12, 0, 0)  # after _DROPOFF

# One clean row in each type regime.
_CLEAN_BRONZE_ROW = (1.0, 1.0, 10.0, 2.0, _PICKUP, _DROPOFF)
_CLEAN_SILVER_ROW = (1,   1,   10.0, 2.0, _PICKUP, _DROPOFF)


# ---------------------------------------------------------------------------
# Spark fixture (module-scoped)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def spark(tmp_path_factory):
    """Create a local Spark session for silver_validators tests."""
    warehouse = tmp_path_factory.mktemp("spark_warehouse")
    session = (
        SparkSession.builder
        .master("local[1]")
        .appName("test_silver_validators")
        .config("spark.sql.warehouse.dir", str(warehouse))
        .config("spark.driver.extraJavaOptions", "-Dderby.system.home=" + str(warehouse))
        .getOrCreate()
    )
    yield session
    session.stop()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bronze_df(spark, rows: list) -> DataFrame:
    """Build a Bronze-typed DataFrame for pre-normalization tests."""
    return spark.createDataFrame(rows, schema=_BRONZE_SCHEMA)


def _silver_df(spark, rows: list) -> DataFrame:
    """Build a Silver-typed DataFrame with an initialized rejection column."""
    df = spark.createDataFrame(rows, schema=_SILVER_SCHEMA)
    return df.withColumn(SILVER_REJECTION_COLUMN, F.array().cast(ArrayType(StringType())))


def _reasons(row) -> set[str]:
    """Return the rejection reasons for a collected row as a set."""
    return set(row[SILVER_REJECTION_COLUMN])


# ---------------------------------------------------------------------------
# apply_pre_normalization_constraints: rejection column initialization
# ---------------------------------------------------------------------------


def test_pre_initializes_rejection_column(spark) -> None:
    """A clean row gets an empty (not null) rejection-reasons array."""
    df = _bronze_df(spark, [_CLEAN_BRONZE_ROW])
    result = apply_pre_normalization_constraints(df)
    assert SILVER_REJECTION_COLUMN in result.columns
    row = result.collect()[0]
    assert row[SILVER_REJECTION_COLUMN] == []


def test_pre_clean_row_stays_empty(spark) -> None:
    """A fully valid row accumulates no rejection reasons."""
    df = _bronze_df(spark, [_CLEAN_BRONZE_ROW])
    result = apply_pre_normalization_constraints(df)
    assert _reasons(result.collect()[0]) == set()


# ---------------------------------------------------------------------------
# apply_pre_normalization_constraints: integrality checks
# ---------------------------------------------------------------------------


def test_pre_tags_non_integral_passenger_count(spark) -> None:
    """passenger_count=1.5 fires NON_INTEGRAL_PASSENGER_COUNT."""
    row = (1.5, 1.0, 10.0, 2.0, _PICKUP, _DROPOFF)
    df = _bronze_df(spark, [row])
    result = apply_pre_normalization_constraints(df)
    assert RejectionReason.NON_INTEGRAL_PASSENGER_COUNT.value in _reasons(result.collect()[0])


def test_pre_tags_non_integral_ratecode(spark) -> None:
    """RatecodeID=2.5 fires NON_INTEGRAL_RATECODE."""
    row = (1.0, 2.5, 10.0, 2.0, _PICKUP, _DROPOFF)
    df = _bronze_df(spark, [row])
    result = apply_pre_normalization_constraints(df)
    assert RejectionReason.NON_INTEGRAL_RATECODE.value in _reasons(result.collect()[0])


def test_pre_does_not_tag_integral_as_non_integral(spark) -> None:
    """Integer-valued doubles (e.g. 2.0) do not fire the integrality check."""
    row = (2.0, 3.0, 10.0, 2.0, _PICKUP, _DROPOFF)
    df = _bronze_df(spark, [row])
    result = apply_pre_normalization_constraints(df)
    reasons = _reasons(result.collect()[0])
    assert RejectionReason.NON_INTEGRAL_PASSENGER_COUNT.value not in reasons
    assert RejectionReason.NON_INTEGRAL_RATECODE.value not in reasons


def test_pre_null_not_tagged_as_non_integral(spark) -> None:
    """A null passenger_count fires NULL_PASSENGER_COUNT but not NON_INTEGRAL.

    Spark's three-valued logic evaluates NULL != floor(NULL) as NULL (falsy),
    so null values must not accumulate a spurious integrality violation.
    """
    row = (None, 1.0, 10.0, 2.0, _PICKUP, _DROPOFF)
    df = _bronze_df(spark, [row])
    result = apply_pre_normalization_constraints(df)
    reasons = _reasons(result.collect()[0])
    assert RejectionReason.NULL_PASSENGER_COUNT.value in reasons
    assert RejectionReason.NON_INTEGRAL_PASSENGER_COUNT.value not in reasons


# ---------------------------------------------------------------------------
# apply_pre_normalization_constraints: null checks
# ---------------------------------------------------------------------------


def test_pre_tags_null_fare_amount(spark) -> None:
    """null fare_amount fires NULL_FARE_AMOUNT."""
    row = (1.0, 1.0, None, 2.0, _PICKUP, _DROPOFF)
    df = _bronze_df(spark, [row])
    result = apply_pre_normalization_constraints(df)
    assert RejectionReason.NULL_FARE_AMOUNT.value in _reasons(result.collect()[0])


def test_pre_tags_null_trip_distance(spark) -> None:
    """null trip_distance fires NULL_TRIP_DISTANCE."""
    row = (1.0, 1.0, 10.0, None, _PICKUP, _DROPOFF)
    df = _bronze_df(spark, [row])
    result = apply_pre_normalization_constraints(df)
    assert RejectionReason.NULL_TRIP_DISTANCE.value in _reasons(result.collect()[0])


def test_pre_tags_null_passenger_count(spark) -> None:
    """null passenger_count fires NULL_PASSENGER_COUNT."""
    row = (None, 1.0, 10.0, 2.0, _PICKUP, _DROPOFF)
    df = _bronze_df(spark, [row])
    result = apply_pre_normalization_constraints(df)
    assert RejectionReason.NULL_PASSENGER_COUNT.value in _reasons(result.collect()[0])


def test_pre_tags_null_pickup_datetime(spark) -> None:
    """null tpep_pickup_datetime fires NULL_PICKUP_DATETIME."""
    row = (1.0, 1.0, 10.0, 2.0, None, _DROPOFF)
    df = _bronze_df(spark, [row])
    result = apply_pre_normalization_constraints(df)
    assert RejectionReason.NULL_PICKUP_DATETIME.value in _reasons(result.collect()[0])


def test_pre_tags_null_dropoff_datetime(spark) -> None:
    """null tpep_dropoff_datetime fires NULL_DROPOFF_DATETIME."""
    row = (1.0, 1.0, 10.0, 2.0, _PICKUP, None)
    df = _bronze_df(spark, [row])
    result = apply_pre_normalization_constraints(df)
    assert RejectionReason.NULL_DROPOFF_DATETIME.value in _reasons(result.collect()[0])


def test_pre_accumulates_multiple_violations(spark) -> None:
    """A row with multiple violations accumulates all of their reasons."""
    # non-integral passenger_count AND null fare_amount on the same row
    row = (1.5, 1.0, None, 2.0, _PICKUP, _DROPOFF)
    df = _bronze_df(spark, [row])
    result = apply_pre_normalization_constraints(df)
    reasons = _reasons(result.collect()[0])
    assert RejectionReason.NON_INTEGRAL_PASSENGER_COUNT.value in reasons
    assert RejectionReason.NULL_FARE_AMOUNT.value in reasons


# ---------------------------------------------------------------------------
# apply_post_normalization_constraints
# ---------------------------------------------------------------------------


def test_post_clean_row_does_not_add_reasons(spark) -> None:
    """A fully valid Silver-typed row accumulates no post-normalization reasons."""
    df = _silver_df(spark, [_CLEAN_SILVER_ROW])
    result = apply_post_normalization_constraints(df)
    assert _reasons(result.collect()[0]) == set()


def test_post_tags_negative_fare(spark) -> None:
    """fare_amount < 0 fires NEGATIVE_FARE."""
    row = (1, 1, -1.0, 2.0, _PICKUP, _DROPOFF)
    df = _silver_df(spark, [row])
    result = apply_post_normalization_constraints(df)
    assert RejectionReason.NEGATIVE_FARE.value in _reasons(result.collect()[0])


def test_post_tags_negative_distance(spark) -> None:
    """trip_distance < 0 fires NEGATIVE_DISTANCE."""
    row = (1, 1, 10.0, -0.5, _PICKUP, _DROPOFF)
    df = _silver_df(spark, [row])
    result = apply_post_normalization_constraints(df)
    assert RejectionReason.NEGATIVE_DISTANCE.value in _reasons(result.collect()[0])


def test_post_tags_passenger_count_below_zero(spark) -> None:
    """passenger_count < 0 fires INVALID_PASSENGER_COUNT."""
    row = (-1, 1, 10.0, 2.0, _PICKUP, _DROPOFF)
    df = _silver_df(spark, [row])
    result = apply_post_normalization_constraints(df)
    assert RejectionReason.INVALID_PASSENGER_COUNT.value in _reasons(result.collect()[0])


def test_post_tags_passenger_count_above_nine(spark) -> None:
    """passenger_count > 9 fires INVALID_PASSENGER_COUNT."""
    row = (10, 1, 10.0, 2.0, _PICKUP, _DROPOFF)
    df = _silver_df(spark, [row])
    result = apply_post_normalization_constraints(df)
    assert RejectionReason.INVALID_PASSENGER_COUNT.value in _reasons(result.collect()[0])


def test_post_accepts_passenger_count_at_boundaries(spark) -> None:
    """passenger_count values 0 and 9 are within the valid range."""
    for count in (0, 9):
        row = (count, 1, 10.0, 2.0, _PICKUP, _DROPOFF)
        df = _silver_df(spark, [row])
        result = apply_post_normalization_constraints(df)
        assert RejectionReason.INVALID_PASSENGER_COUNT.value not in _reasons(result.collect()[0])


def test_post_tags_pickup_after_dropoff(spark) -> None:
    """pickup strictly after dropoff fires PICKUP_AFTER_DROPOFF."""
    row = (1, 1, 10.0, 2.0, _REVERSED_PICKUP, _DROPOFF)
    df = _silver_df(spark, [row])
    result = apply_post_normalization_constraints(df)
    assert RejectionReason.PICKUP_AFTER_DROPOFF.value in _reasons(result.collect()[0])


def test_post_tags_pickup_equal_to_dropoff(spark) -> None:
    """pickup == dropoff also fires PICKUP_AFTER_DROPOFF (strictly less is required)."""
    row = (1, 1, 10.0, 2.0, _PICKUP, _PICKUP)
    df = _silver_df(spark, [row])
    result = apply_post_normalization_constraints(df)
    assert RejectionReason.PICKUP_AFTER_DROPOFF.value in _reasons(result.collect()[0])


def test_post_null_fare_does_not_fire_negative_fare(spark) -> None:
    """A null fare_amount does not fire NEGATIVE_FARE.

    Spark's three-valued logic evaluates NULL < 0 as NULL (falsy). The pre-
    normalization null check is responsible for rejecting null fares; the
    post-normalization check must not double-tag them.
    """
    row = (1, 1, None, 2.0, _PICKUP, _DROPOFF)
    df = _silver_df(spark, [row])
    result = apply_post_normalization_constraints(df)
    assert RejectionReason.NEGATIVE_FARE.value not in _reasons(result.collect()[0])


def test_post_appends_to_existing_reasons(spark) -> None:
    """A pre-existing rejection reason is preserved alongside new post-norm reasons."""
    pre_existing = "pre_existing_reason"
    row = (1, 1, -5.0, 2.0, _PICKUP, _DROPOFF)
    df = (
        spark.createDataFrame([row], schema=_SILVER_SCHEMA)
        .withColumn(SILVER_REJECTION_COLUMN, F.array(F.lit(pre_existing)))
    )
    result = apply_post_normalization_constraints(df)
    reasons = _reasons(result.collect()[0])
    assert pre_existing in reasons
    assert RejectionReason.NEGATIVE_FARE.value in reasons


# ---------------------------------------------------------------------------
# split_accepted_rejected
# ---------------------------------------------------------------------------


def test_split_empty_array_goes_to_accepted(spark) -> None:
    """A row with an empty rejection array lands in the accepted DataFrame."""
    df = _silver_df(spark, [_CLEAN_SILVER_ROW])
    accepted, rejected = split_accepted_rejected(df)
    assert accepted.count() == 1
    assert rejected.count() == 0


def test_split_nonempty_array_goes_to_rejected(spark) -> None:
    """A row with a non-empty rejection array lands in the rejected DataFrame."""
    row = (1, 1, -5.0, 2.0, _PICKUP, _DROPOFF)
    df = (
        spark.createDataFrame([row], schema=_SILVER_SCHEMA)
        .withColumn(SILVER_REJECTION_COLUMN, F.array(F.lit(RejectionReason.NEGATIVE_FARE.value)))
    )
    accepted, rejected = split_accepted_rejected(df)
    assert accepted.count() == 0
    assert rejected.count() == 1


def test_split_accepted_drops_rejection_column(spark) -> None:
    """The accepted DataFrame does not contain the rejection column."""
    df = _silver_df(spark, [_CLEAN_SILVER_ROW])
    accepted, _ = split_accepted_rejected(df)
    assert SILVER_REJECTION_COLUMN not in accepted.columns


def test_split_rejected_retains_rejection_column(spark) -> None:
    """The rejected DataFrame retains the rejection column."""
    row = (1, 1, -5.0, 2.0, _PICKUP, _DROPOFF)
    df = (
        spark.createDataFrame([row], schema=_SILVER_SCHEMA)
        .withColumn(SILVER_REJECTION_COLUMN, F.array(F.lit(RejectionReason.NEGATIVE_FARE.value)))
    )
    _, rejected = split_accepted_rejected(df)
    assert SILVER_REJECTION_COLUMN in rejected.columns


def test_split_counts_are_correct(spark) -> None:
    """Accepted and rejected counts sum to the total input row count."""
    clean_rows = [_CLEAN_SILVER_ROW] * 3
    df_clean = _silver_df(spark, clean_rows)
    dirty_row = (1, 1, -5.0, 2.0, _PICKUP, _DROPOFF)
    df_dirty = (
        spark.createDataFrame([dirty_row] * 2, schema=_SILVER_SCHEMA)
        .withColumn(SILVER_REJECTION_COLUMN, F.array(F.lit(RejectionReason.NEGATIVE_FARE.value)))
    )
    df = df_clean.union(df_dirty)
    accepted, rejected = split_accepted_rejected(df)
    assert accepted.count() == 3
    assert rejected.count() == 2


def test_split_no_row_appears_in_both_partitions(spark) -> None:
    """Accepted and rejected are disjoint: their union equals the input."""
    clean_rows = [_CLEAN_SILVER_ROW] * 4
    df_clean = _silver_df(spark, clean_rows)
    dirty_row = (1, 1, -1.0, 2.0, _PICKUP, _DROPOFF)
    df_dirty = (
        spark.createDataFrame([dirty_row] * 3, schema=_SILVER_SCHEMA)
        .withColumn(SILVER_REJECTION_COLUMN, F.array(F.lit(RejectionReason.NEGATIVE_FARE.value)))
    )
    df = df_clean.union(df_dirty)
    total = df.count()
    accepted, rejected = split_accepted_rejected(df)
    assert accepted.count() + rejected.count() == total
