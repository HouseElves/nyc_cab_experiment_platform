# pylint: disable=redefined-outer-name
"""
Tests for :mod:`nyc_cab.transform.silver_entrypoint`.

These tests cover:

* :class:`SilverTransformResult` -- the post-transformation result, including
  the reconciliation invariant (``bronze_count == accepted_count +
  rejected_count``), type-check rejection, structural rejection, and
  ``validity_check`` chaining.
* :func:`transform_silver_month` -- the 11-step orchestration flow. A synthetic
  Bronze partition is pre-staged in the expected Hive directory layout so the
  full pipeline (pre-normalization, type cast, post-normalization, split, write)
  runs against real data with zero mocks.
"""

from __future__ import annotations

import dataclasses
import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pyspark.sql import SparkSession

from nyc_cab.config import load_config
from nyc_cab.contracts.silver import RejectionReason
from nyc_cab.exceptions import InvalidRequestError
from nyc_cab.transform.silver_entrypoint import SilverTransformResult, transform_silver_month
from nyc_cab.transform.silver_request import SilverTransformRequest


# ---------------------------------------------------------------------------
# Constants and schema
# ---------------------------------------------------------------------------

_CLEAN_COUNT = 5
_DIRTY_COUNT = 5
_TOTAL_COUNT = _CLEAN_COUNT + _DIRTY_COUNT

# Sentinel datetime values used across the dirty partition fixture.
_PICKUP  = datetime.datetime(2023, 1, 15, 10, 0, 0)
_DROPOFF = datetime.datetime(2023, 1, 15, 11, 0, 0)
# Reversed pickup fires PICKUP_AFTER_DROPOFF when paired with _DROPOFF.
_REVERSED_PICKUP = datetime.datetime(2023, 1, 15, 12, 0, 0)

# Minimal Bronze schema: only the six fields Silver actually processes.
# passenger_count and RatecodeID are double here (Bronze type); Silver
# normalizes them to int. The remaining 13 Bronze columns are not required
# for Silver correctness and are omitted for fixture clarity.
_BRONZE_ARROW_SCHEMA = pa.schema([
    pa.field("passenger_count",      pa.float64(), nullable=True),
    pa.field("RatecodeID",           pa.float64(), nullable=True),
    pa.field("fare_amount",          pa.float64(), nullable=True),
    pa.field("trip_distance",        pa.float64(), nullable=True),
    pa.field("tpep_pickup_datetime",  pa.timestamp("us"), nullable=True),
    pa.field("tpep_dropoff_datetime", pa.timestamp("us"), nullable=True),
])


# ---------------------------------------------------------------------------
# Spark fixture (module-scoped)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def spark(tmp_path_factory):
    """Create a local Spark session for Silver entrypoint tests."""
    warehouse = tmp_path_factory.mktemp("spark_warehouse")
    session = (
        SparkSession.builder
        .master("local[1]")
        .appName("test_silver_entrypoint")
        .config("spark.sql.sources.partitionOverwriteMode", "dynamic")
        .config("spark.sql.warehouse.dir", str(warehouse))
        .config("spark.driver.extraJavaOptions", "-Dderby.system.home=" + str(warehouse))
        .getOrCreate()
    )
    yield session
    session.stop()


# ---------------------------------------------------------------------------
# Bronze partition helpers
# ---------------------------------------------------------------------------


def _bronze_partition_path(data_root: Path) -> Path:
    """Return the Bronze Hive partition directory for yellow/2023/01."""
    return data_root / "bronze" / "cab_type=yellow" / "year=2023" / "month=1"


def _write_clean_bronze_partition(data_root: Path, row_count: int = _CLEAN_COUNT) -> Path:
    """Write a synthetic Bronze partition where all rows pass Silver constraints.

    Every row has an integral passenger_count and RatecodeID, non-null
    constraint-checked fields, non-negative amounts, and pickup before
    dropoff. Returns the partition directory path.
    """
    partition_path = _bronze_partition_path(data_root)
    partition_path.mkdir(parents=True, exist_ok=True)
    table = pa.table(
        {
            "passenger_count":      pa.array([1.0]     * row_count, type=pa.float64()),
            "RatecodeID":           pa.array([1.0]     * row_count, type=pa.float64()),
            "fare_amount":          pa.array([10.0]    * row_count, type=pa.float64()),
            "trip_distance":        pa.array([2.0]     * row_count, type=pa.float64()),
            "tpep_pickup_datetime":  pa.array([_PICKUP]  * row_count, type=pa.timestamp("us")),
            "tpep_dropoff_datetime": pa.array([_DROPOFF] * row_count, type=pa.timestamp("us")),
        },
        schema=_BRONZE_ARROW_SCHEMA,
    )
    pq.write_table(table, str(partition_path / "data.parquet"))
    return partition_path


def _write_dirty_bronze_partition(data_root: Path) -> Path:
    """Write a Bronze partition with _CLEAN_COUNT clean rows and _DIRTY_COUNT dirty rows.

    The dirty rows inject exactly one Silver constraint violation each:

    - Index 5: passenger_count=1.5  → NON_INTEGRAL_PASSENGER_COUNT
    - Index 6: fare_amount=None     → NULL_FARE_AMOUNT
    - Index 7: trip_distance=-1.0   → NEGATIVE_DISTANCE
    - Index 8: pickup after dropoff → PICKUP_AFTER_DROPOFF
    - Index 9: fare_amount=-5.0     → NEGATIVE_FARE

    Returns the partition directory path.
    """
    partition_path = _bronze_partition_path(data_root)
    partition_path.mkdir(parents=True, exist_ok=True)
    table = pa.table(
        {
            "passenger_count": pa.array(
                [1.0] * _CLEAN_COUNT + [1.5, 1.0, 1.0, 1.0, 1.0],
                type=pa.float64(),
            ),
            "RatecodeID": pa.array([1.0] * _TOTAL_COUNT, type=pa.float64()),
            "fare_amount": pa.array(
                [10.0] * _CLEAN_COUNT + [10.0, None, 10.0, 10.0, -5.0],
                type=pa.float64(),
            ),
            "trip_distance": pa.array(
                [2.0] * _CLEAN_COUNT + [2.0, 2.0, -1.0, 2.0, 2.0],
                type=pa.float64(),
            ),
            "tpep_pickup_datetime": pa.array(
                [_PICKUP] * _CLEAN_COUNT + [_PICKUP, _PICKUP, _PICKUP, _REVERSED_PICKUP, _PICKUP],
                type=pa.timestamp("us"),
            ),
            "tpep_dropoff_datetime": pa.array(
                [_DROPOFF] * _TOTAL_COUNT,
                type=pa.timestamp("us"),
            ),
        },
        schema=_BRONZE_ARROW_SCHEMA,
    )
    pq.write_table(table, str(partition_path / "data.parquet"))
    return partition_path


def _request() -> SilverTransformRequest:
    """Build a valid Silver transform request for yellow/2023/01."""
    return SilverTransformRequest.create_validated("yellow", 2023, 1)


# ---------------------------------------------------------------------------
# SilverTransformResult: happy paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_result_create_validated_happy_path(tmp_path: Path) -> None:
    """A well-formed result with consistent counts constructs cleanly."""
    silver = tmp_path / "silver"
    silver.mkdir()
    rejected = tmp_path / "silver_rejected"
    rejected.mkdir()
    result = SilverTransformResult.create_validated(
        _request(), silver, rejected, 1000, 950, 50,
    )
    assert result.bronze_count == 1000
    assert result.accepted_count == 950
    assert result.rejected_count == 50
    assert result.is_valid()


@pytest.mark.unit
def test_result_accepts_zero_rejected(tmp_path: Path) -> None:
    """Zero rejected rows is valid (all rows accepted)."""
    silver = tmp_path / "silver"
    silver.mkdir()
    rejected = tmp_path / "rejected"
    rejected.mkdir()
    result = SilverTransformResult.create_validated(
        _request(), silver, rejected, 500, 500, 0,
    )
    assert result.rejected_count == 0


@pytest.mark.unit
def test_result_accepts_all_rejected(tmp_path: Path) -> None:
    """All rows rejected is valid (zero accepted)."""
    silver = tmp_path / "silver"
    silver.mkdir()
    rejected = tmp_path / "rejected"
    rejected.mkdir()
    result = SilverTransformResult.create_validated(
        _request(), silver, rejected, 500, 0, 500,
    )
    assert result.accepted_count == 0


@pytest.mark.unit
def test_result_accepts_nonexistent_partition_paths(tmp_path: Path) -> None:
    """Partition directories that don't exist yet are structurally valid."""
    silver = tmp_path / "silver" / "not_yet"
    rejected = tmp_path / "rejected" / "not_yet"
    result = SilverTransformResult.create_validated(
        _request(), silver, rejected, 100, 90, 10,
    )
    assert not result.silver_partition_path.exists()
    assert not result.silver_rejected_partition_path.exists()


# ---------------------------------------------------------------------------
# SilverTransformResult: type-check rejections
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_result_rejects_non_request(tmp_path: Path) -> None:
    """``request`` must be a SilverTransformRequest."""
    with pytest.raises(InvalidRequestError) as info:
        SilverTransformResult.create_validated(
            "not-a-request", tmp_path, tmp_path, 100, 90, 10,
        )
    names = [v[0] for v in info.value.violations]
    assert "request" in names


@pytest.mark.unit
def test_result_rejects_string_silver_path(tmp_path: Path) -> None:
    """``silver_partition_path`` must be a Path."""
    with pytest.raises(InvalidRequestError) as info:
        SilverTransformResult.create_validated(
            _request(), "/tmp/silver", tmp_path, 100, 90, 10,
        )
    names = [v[0] for v in info.value.violations]
    assert "silver_partition_path" in names


@pytest.mark.unit
def test_result_rejects_bool_bronze_count(tmp_path: Path) -> None:
    """``bronze_count`` rejects bool despite int compatibility."""
    with pytest.raises(InvalidRequestError) as info:
        SilverTransformResult.create_validated(
            _request(), tmp_path, tmp_path, True, 0, 0,
        )
    assert ("bronze_count", True) in info.value.violations


# ---------------------------------------------------------------------------
# SilverTransformResult: structural rejections
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_result_rejects_negative_bronze_count(tmp_path: Path) -> None:
    """Negative bronze_count violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        SilverTransformResult.create_validated(
            _request(), tmp_path, tmp_path, -1, 0, 0,
        )
    names = [v[0] for v in info.value.violations]
    assert "bronze_count" in names


@pytest.mark.unit
def test_result_rejects_negative_accepted_count(tmp_path: Path) -> None:
    """Negative accepted_count violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        SilverTransformResult.create_validated(
            _request(), tmp_path, tmp_path, 100, -1, 101,
        )
    names = [v[0] for v in info.value.violations]
    assert "accepted_count" in names


@pytest.mark.unit
def test_result_rejects_file_as_silver_path(tmp_path: Path) -> None:
    """A regular file at the silver path violates the directory rule."""
    file_path = tmp_path / "not-a-dir"
    file_path.write_text("data")
    with pytest.raises(InvalidRequestError) as info:
        SilverTransformResult.create_validated(
            _request(), file_path, tmp_path, 100, 90, 10,
        )
    assert ("silver_partition_path", file_path) in info.value.violations


# ---------------------------------------------------------------------------
# Reconciliation invariant
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_result_rejects_inconsistent_counts(tmp_path: Path) -> None:
    """bronze_count != accepted + rejected violates the reconciliation invariant."""
    with pytest.raises(InvalidRequestError) as info:
        SilverTransformResult.create_validated(
            _request(), tmp_path, tmp_path, 100, 90, 20,
        )
    names = [v[0] for v in info.value.violations]
    assert "reconciliation" in names


@pytest.mark.unit
def test_result_rejects_overcounted_accepted(tmp_path: Path) -> None:
    """accepted_count exceeding bronze_count violates reconciliation."""
    with pytest.raises(InvalidRequestError) as info:
        SilverTransformResult.create_validated(
            _request(), tmp_path, tmp_path, 100, 101, 0,
        )
    names = [v[0] for v in info.value.violations]
    assert "reconciliation" in names


@pytest.mark.unit
def test_reconciliation_passes_at_zero(tmp_path: Path) -> None:
    """Zero bronze, zero accepted, zero rejected satisfies the invariant."""
    result = SilverTransformResult.create_validated(
        _request(), tmp_path, tmp_path, 0, 0, 0,
    )
    assert result.bronze_count == 0


# ---------------------------------------------------------------------------
# validity_check chaining
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_result_chaining_catches_invalid_request(tmp_path: Path) -> None:
    """A structurally-bad request bubbles up as a request violation."""
    bad_request = SilverTransformRequest("yellow", 2023, 13)
    with pytest.raises(InvalidRequestError) as info:
        SilverTransformResult.create_validated(
            bad_request, tmp_path, tmp_path, 100, 90, 10,
        )
    names = [v[0] for v in info.value.violations]
    assert "request" in names


# ---------------------------------------------------------------------------
# Frozenness
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_result_is_frozen(tmp_path: Path) -> None:
    """``SilverTransformResult`` rejects attribute mutation."""
    result = SilverTransformResult.create_validated(
        _request(), tmp_path, tmp_path, 100, 90, 10,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.bronze_count = 200  # type: ignore[misc]


# ---------------------------------------------------------------------------
# transform_silver_month: contract rejection (no Spark required)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_transform_rejects_unsupported_period(tmp_path: Path) -> None:
    """An unsupported period raises InvalidRequestError before any Spark interaction."""
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    bad_request = SilverTransformRequest.create_validated("yellow", 2023, 6)
    with pytest.raises(InvalidRequestError) as info:
        transform_silver_month(None, runtime, bad_request)  # type: ignore[arg-type]
    names = [v[0] for v in info.value.violations]
    assert "period" in names


@pytest.mark.unit
def test_transform_rejects_unsupported_cab_type(tmp_path: Path) -> None:
    """An unsupported cab type raises InvalidRequestError before any Spark interaction."""
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    bad_request = SilverTransformRequest.create_validated("green", 2023, 1)
    with pytest.raises(InvalidRequestError) as info:
        transform_silver_month(None, runtime, bad_request)  # type: ignore[arg-type]
    names = [v[0] for v in info.value.violations]
    assert "cab_type" in names


# ---------------------------------------------------------------------------
# transform_silver_month: happy path (clean partition)
# ---------------------------------------------------------------------------


@pytest.mark.spark
def test_transform_produces_valid_result(spark, tmp_path) -> None:
    """A clean Bronze partition produces a valid SilverTransformResult."""
    _write_clean_bronze_partition(tmp_path)
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    result = transform_silver_month(spark, runtime, _request())
    assert isinstance(result, SilverTransformResult)
    assert result.is_valid()


@pytest.mark.spark
def test_transform_all_clean_rows_accepted(spark, tmp_path) -> None:
    """All rows in a clean partition land in accepted with zero rejected."""
    _write_clean_bronze_partition(tmp_path, row_count=_CLEAN_COUNT)
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    result = transform_silver_month(spark, runtime, _request())
    assert result.accepted_count == _CLEAN_COUNT
    assert result.rejected_count == 0


@pytest.mark.spark
def test_transform_bronze_count_matches_source(spark, tmp_path) -> None:
    """The bronze_count in the result equals the number of rows in the source partition."""
    _write_clean_bronze_partition(tmp_path, row_count=_CLEAN_COUNT)
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    result = transform_silver_month(spark, runtime, _request())
    assert result.bronze_count == _CLEAN_COUNT


@pytest.mark.spark
def test_transform_result_traces_to_request(spark, tmp_path) -> None:
    """The result's request field is the original request object."""
    _write_clean_bronze_partition(tmp_path)
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    request = _request()
    result = transform_silver_month(spark, runtime, request)
    assert result.request is request


# ---------------------------------------------------------------------------
# transform_silver_month: partition layout
# ---------------------------------------------------------------------------


@pytest.mark.spark
def test_transform_writes_silver_partition_directory(spark, tmp_path) -> None:
    """The transform creates the Silver accepted partition directory on disk."""
    _write_clean_bronze_partition(tmp_path)
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    result = transform_silver_month(spark, runtime, _request())
    assert result.silver_partition_path.exists()
    assert result.silver_partition_path.is_dir()
    assert list(result.silver_partition_path.glob("*.parquet"))


@pytest.mark.spark
def test_transform_writes_rejected_partition_directory(spark, tmp_path) -> None:
    """The transform creates the Silver rejected partition directory on disk."""
    _write_dirty_bronze_partition(tmp_path)
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    result = transform_silver_month(spark, runtime, _request())
    assert result.silver_rejected_partition_path.exists()
    assert result.silver_rejected_partition_path.is_dir()
    assert list(result.silver_rejected_partition_path.glob("*.parquet"))


@pytest.mark.spark
def test_transform_silver_partition_uses_hive_layout(spark, tmp_path) -> None:
    """The Silver accepted partition path follows the cab_type/year/month Hive layout."""
    _write_clean_bronze_partition(tmp_path)
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    result = transform_silver_month(spark, runtime, _request())
    parts = result.silver_partition_path.parts
    assert "cab_type=yellow" in parts
    assert "year=2023" in parts
    assert "month=1" in parts


@pytest.mark.spark
def test_transform_rejected_partition_uses_hive_layout(spark, tmp_path) -> None:
    """The Silver rejected partition path follows the cab_type/year/month Hive layout."""
    _write_dirty_bronze_partition(tmp_path)
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    result = transform_silver_month(spark, runtime, _request())
    parts = result.silver_rejected_partition_path.parts
    assert "cab_type=yellow" in parts
    assert "year=2023" in parts
    assert "month=1" in parts


# ---------------------------------------------------------------------------
# transform_silver_month: accepted output schema
# ---------------------------------------------------------------------------


@pytest.mark.spark
def test_transform_accepted_drops_rejection_column(spark, tmp_path) -> None:
    """The accepted partition does not contain the ``_rejection_reasons`` column."""
    _write_clean_bronze_partition(tmp_path)
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    result = transform_silver_month(spark, runtime, _request())
    accepted_df = spark.read.parquet(str(result.silver_partition_path))
    assert "_rejection_reasons" not in accepted_df.columns


@pytest.mark.spark
def test_transform_accepted_normalizes_passenger_count_to_int(spark, tmp_path) -> None:
    """The accepted partition stores passenger_count as int (Silver type)."""
    _write_clean_bronze_partition(tmp_path)
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    result = transform_silver_month(spark, runtime, _request())
    accepted_df = spark.read.parquet(str(result.silver_partition_path))
    schema_map = {f.name: f.dataType.simpleString() for f in accepted_df.schema.fields}
    assert schema_map["passenger_count"] == "int"


@pytest.mark.spark
def test_transform_accepted_normalizes_ratecode_to_int(spark, tmp_path) -> None:
    """The accepted partition stores RatecodeID as int (Silver type)."""
    _write_clean_bronze_partition(tmp_path)
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    result = transform_silver_month(spark, runtime, _request())
    accepted_df = spark.read.parquet(str(result.silver_partition_path))
    schema_map = {f.name: f.dataType.simpleString() for f in accepted_df.schema.fields}
    assert schema_map["RatecodeID"] == "int"


# ---------------------------------------------------------------------------
# transform_silver_month: rejected output and reconciliation
# ---------------------------------------------------------------------------


@pytest.mark.spark
def test_transform_reconciliation_invariant_with_dirty_partition(spark, tmp_path) -> None:
    """bronze_count == accepted_count + rejected_count holds for a mixed partition."""
    _write_dirty_bronze_partition(tmp_path)
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    result = transform_silver_month(spark, runtime, _request())
    assert result.bronze_count == result.accepted_count + result.rejected_count
    assert result.bronze_count == _TOTAL_COUNT


@pytest.mark.spark
def test_transform_dirty_counts_match_expected_violations(spark, tmp_path) -> None:
    """Exactly _DIRTY_COUNT rows are rejected and _CLEAN_COUNT accepted."""
    _write_dirty_bronze_partition(tmp_path)
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    result = transform_silver_month(spark, runtime, _request())
    assert result.accepted_count == _CLEAN_COUNT
    assert result.rejected_count == _DIRTY_COUNT


@pytest.mark.spark
def test_transform_rejected_retains_rejection_column(spark, tmp_path) -> None:
    """The rejected partition retains the ``_rejection_reasons`` array column."""
    _write_dirty_bronze_partition(tmp_path)
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    result = transform_silver_month(spark, runtime, _request())
    rejected_df = spark.read.parquet(str(result.silver_rejected_partition_path))
    assert "_rejection_reasons" in rejected_df.columns


@pytest.mark.spark
def test_transform_known_violations_appear_in_rejected(spark, tmp_path) -> None:
    """Each of the five injected violation reasons appears in the rejected partition."""
    _write_dirty_bronze_partition(tmp_path)
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    result = transform_silver_month(spark, runtime, _request())
    rejected_df = spark.read.parquet(str(result.silver_rejected_partition_path))
    all_reasons = {
        reason
        for row in rejected_df.collect()
        for reason in row["_rejection_reasons"]
    }
    assert RejectionReason.NON_INTEGRAL_PASSENGER_COUNT.value in all_reasons
    assert RejectionReason.NULL_FARE_AMOUNT.value             in all_reasons
    assert RejectionReason.NEGATIVE_DISTANCE.value            in all_reasons
    assert RejectionReason.PICKUP_AFTER_DROPOFF.value         in all_reasons
    assert RejectionReason.NEGATIVE_FARE.value                in all_reasons


# ---------------------------------------------------------------------------
# transform_silver_month: idempotent re-execution
# ---------------------------------------------------------------------------


@pytest.mark.spark
def test_transform_idempotent_reexecution(spark, tmp_path) -> None:
    """Running the same transform twice produces identical counts without error."""
    _write_clean_bronze_partition(tmp_path, row_count=_CLEAN_COUNT)
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    request = _request()

    first  = transform_silver_month(spark, runtime, request)
    second = transform_silver_month(spark, runtime, request)

    assert first.accepted_count == second.accepted_count
    assert first.rejected_count == second.rejected_count

    # Read-back count assertion: proves partition overwrite, not append.
    readback_count = spark.read.parquet(str(second.silver_partition_path)).count()
    assert readback_count == _CLEAN_COUNT
