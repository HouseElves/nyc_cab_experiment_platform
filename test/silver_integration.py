# pylint: disable=redefined-outer-name
# pylint: disable=duplicate-code
# Fixture helpers (partition path, pyarrow writers, _request) overlap with
# silver_entrypoint module tests by design — see design_log.md decision 28.
"""
Silver transformation integration tests (fast, synthetic, CI-safe).

These tests exercise the full ``transform_silver_month`` flow with the
shared session-scoped Spark session, the real Silver v1 contract, and a
synthetic Bronze partition pre-staged in the expected Hive directory layout.
No mocks, no monkeypatching. Every module boundary is exercised for real.

A dedicated Bronze partition helper is used here rather than the conftest's
``write_synthetic_yellow_parquet`` because that helper generates non-integral
doubles for ``passenger_count`` and ``RatecodeID``, and identical timestamps
for pickup and dropoff — both of which Silver rejects. The helper below
produces data that is clean under the full Silver v1 constraint set.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

import pyarrow as pa
import pyarrow.parquet as pq

from nyc_cab.config import load_config
from nyc_cab.contracts.silver import RejectionReason
from nyc_cab.transform.silver_entrypoint import SilverTransformResult, transform_silver_month
from nyc_cab.transform.silver_request import SilverTransformRequest

pytestmark = pytest.mark.spark


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CLEAN_COUNT = 10
_DIRTY_COUNT = 2
_MIXED_COUNT = _CLEAN_COUNT + _DIRTY_COUNT

_PICKUP  = datetime.datetime(2023, 1, 15, 10, 0, 0)
_DROPOFF = datetime.datetime(2023, 1, 15, 11, 0, 0)


# ---------------------------------------------------------------------------
# Bronze partition helpers
# ---------------------------------------------------------------------------


def _bronze_partition_path(data_root: Path) -> Path:
    """Return the Bronze Hive partition directory for yellow/2023/01."""
    return data_root / "bronze" / "cab_type=yellow" / "year=2023" / "month=1"


def _write_clean_bronze_partition(data_root: Path, row_count: int = _CLEAN_COUNT) -> Path:
    """Write a full 19-field Bronze partition where every row passes Silver constraints.

    All doubles are non-negative and integral where Silver requires it
    (``passenger_count``, ``RatecodeID``). Pickup strictly precedes dropoff.
    Returns the partition directory path.
    """
    partition_path = _bronze_partition_path(data_root)
    partition_path.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.table({
            "VendorID":                  pa.array([1]      * row_count, type=pa.int64()),
            "tpep_pickup_datetime":      pa.array([_PICKUP]  * row_count, type=pa.timestamp("us")),
            "tpep_dropoff_datetime":     pa.array([_DROPOFF] * row_count, type=pa.timestamp("us")),
            "passenger_count":           pa.array([1.0]    * row_count, type=pa.float64()),
            "trip_distance":             pa.array([2.0]    * row_count, type=pa.float64()),
            "RatecodeID":                pa.array([1.0]    * row_count, type=pa.float64()),
            "store_and_fwd_flag":        pa.array(["N"]    * row_count, type=pa.string()),
            "PULocationID":              pa.array([100]    * row_count, type=pa.int64()),
            "DOLocationID":              pa.array([200]    * row_count, type=pa.int64()),
            "payment_type":              pa.array([1]      * row_count, type=pa.int64()),
            "fare_amount":               pa.array([10.0]   * row_count, type=pa.float64()),
            "extra":                     pa.array([0.5]    * row_count, type=pa.float64()),
            "mta_tax":                   pa.array([0.5]    * row_count, type=pa.float64()),
            "tip_amount":                pa.array([2.0]    * row_count, type=pa.float64()),
            "tolls_amount":              pa.array([0.0]    * row_count, type=pa.float64()),
            "improvement_surcharge":     pa.array([0.3]    * row_count, type=pa.float64()),
            "total_amount":              pa.array([13.3]   * row_count, type=pa.float64()),
            "congestion_surcharge":      pa.array([2.5]    * row_count, type=pa.float64()),
            "Airport_fee":               pa.array([0.0]    * row_count, type=pa.float64()),
        }),
        str(partition_path / "data.parquet"),
    )
    return partition_path


def _write_mixed_bronze_partition(data_root: Path) -> Path:
    """Write a Bronze partition with _CLEAN_COUNT clean rows and _DIRTY_COUNT dirty rows.

    The dirty rows inject one violation each:

    - Index 10: ``passenger_count=1.5``  → NON_INTEGRAL_PASSENGER_COUNT
    - Index 11: ``fare_amount=None``     → NULL_FARE_AMOUNT

    Returns the partition directory path.
    """
    partition_path = _bronze_partition_path(data_root)
    partition_path.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.table({
            "VendorID":              pa.array([1]        * _MIXED_COUNT, type=pa.int64()),
            "tpep_pickup_datetime":  pa.array([_PICKUP]  * _MIXED_COUNT, type=pa.timestamp("us")),
            "tpep_dropoff_datetime": pa.array([_DROPOFF] * _MIXED_COUNT, type=pa.timestamp("us")),
            "passenger_count":       pa.array(
                [1.0] * _CLEAN_COUNT + [1.5, 1.0],
                type=pa.float64(),
            ),
            "trip_distance":         pa.array([2.0]      * _MIXED_COUNT, type=pa.float64()),
            "RatecodeID":            pa.array([1.0]      * _MIXED_COUNT, type=pa.float64()),
            "store_and_fwd_flag":    pa.array(["N"]      * _MIXED_COUNT, type=pa.string()),
            "PULocationID":          pa.array([100]      * _MIXED_COUNT, type=pa.int64()),
            "DOLocationID":          pa.array([200]      * _MIXED_COUNT, type=pa.int64()),
            "payment_type":          pa.array([1]        * _MIXED_COUNT, type=pa.int64()),
            "fare_amount":           pa.array(
                [10.0] * _CLEAN_COUNT + [10.0, None],
                type=pa.float64(),
            ),
            "extra":                 pa.array([0.5]      * _MIXED_COUNT, type=pa.float64()),
            "mta_tax":               pa.array([0.5]      * _MIXED_COUNT, type=pa.float64()),
            "tip_amount":            pa.array([2.0]      * _MIXED_COUNT, type=pa.float64()),
            "tolls_amount":          pa.array([0.0]      * _MIXED_COUNT, type=pa.float64()),
            "improvement_surcharge": pa.array([0.3]      * _MIXED_COUNT, type=pa.float64()),
            "total_amount":          pa.array([13.3]     * _MIXED_COUNT, type=pa.float64()),
            "congestion_surcharge":  pa.array([2.5]      * _MIXED_COUNT, type=pa.float64()),
            "Airport_fee":           pa.array([0.0]      * _MIXED_COUNT, type=pa.float64()),
        }),
        str(partition_path / "data.parquet"),
    )
    return partition_path


def _request() -> SilverTransformRequest:
    """Build a valid Silver transform request for yellow/2023/01."""
    return SilverTransformRequest.create_validated("yellow", 2023, 1)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_transform_produces_valid_result(spark, tmp_path) -> None:
    """Full transform run on a clean partition produces a valid SilverTransformResult."""
    _write_clean_bronze_partition(tmp_path)
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    result = transform_silver_month(spark, runtime, _request())
    assert isinstance(result, SilverTransformResult)
    assert result.is_valid()


def test_transform_all_clean_rows_accepted(spark, tmp_path) -> None:
    """Every row in a clean partition lands in accepted; rejected count is zero."""
    _write_clean_bronze_partition(tmp_path, row_count=_CLEAN_COUNT)
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    result = transform_silver_month(spark, runtime, _request())
    assert result.bronze_count == _CLEAN_COUNT
    assert result.accepted_count == _CLEAN_COUNT
    assert result.rejected_count == 0


def test_transform_result_traces_to_request(spark, tmp_path) -> None:
    """The result's request field is the original request object."""
    _write_clean_bronze_partition(tmp_path)
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    request = _request()
    result = transform_silver_month(spark, runtime, request)
    assert result.request is request


# ---------------------------------------------------------------------------
# Partition layout — accepted
# ---------------------------------------------------------------------------


def test_transform_accepted_partition_exists_on_disk(spark, tmp_path) -> None:
    """The accepted partition directory is created and contains parquet files."""
    _write_clean_bronze_partition(tmp_path)
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    result = transform_silver_month(spark, runtime, _request())
    assert result.silver_partition_path.exists()
    assert result.silver_partition_path.is_dir()
    assert list(result.silver_partition_path.glob("*.parquet"))


def test_transform_accepted_partition_uses_hive_layout(spark, tmp_path) -> None:
    """The accepted partition path follows the cab_type/year/month Hive directory layout."""
    _write_clean_bronze_partition(tmp_path)
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    result = transform_silver_month(spark, runtime, _request())
    parts = result.silver_partition_path.parts
    assert "cab_type=yellow" in parts
    assert "year=2023" in parts
    assert "month=1" in parts


def test_transform_accepted_drops_rejection_column(spark, tmp_path) -> None:
    """Reading back the accepted partition confirms ``_rejection_reasons`` was dropped."""
    _write_clean_bronze_partition(tmp_path)
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    result = transform_silver_month(spark, runtime, _request())
    accepted_df = spark.read.parquet(str(result.silver_partition_path))
    assert "_rejection_reasons" not in accepted_df.columns


# ---------------------------------------------------------------------------
# Mixed partition — reconciliation and rejected layout
# ---------------------------------------------------------------------------


def test_transform_reconciliation_holds_for_mixed_partition(spark, tmp_path) -> None:
    """bronze_count == accepted_count + rejected_count for a partition with violations."""
    _write_mixed_bronze_partition(tmp_path)
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    result = transform_silver_month(spark, runtime, _request())
    assert result.bronze_count == _MIXED_COUNT
    assert result.accepted_count == _CLEAN_COUNT
    assert result.rejected_count == _DIRTY_COUNT
    assert result.bronze_count == result.accepted_count + result.rejected_count


def test_transform_rejected_partition_uses_hive_layout(spark, tmp_path) -> None:
    """The rejected partition path follows the same Hive layout as the accepted partition."""
    _write_mixed_bronze_partition(tmp_path)
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    result = transform_silver_month(spark, runtime, _request())
    parts = result.silver_rejected_partition_path.parts
    assert "cab_type=yellow" in parts
    assert "year=2023" in parts
    assert "month=1" in parts


def test_transform_known_violations_appear_in_rejected(spark, tmp_path) -> None:
    """The rejection reasons injected in the mixed partition appear in the rejected output."""
    _write_mixed_bronze_partition(tmp_path)
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    result = transform_silver_month(spark, runtime, _request())
    rejected_df = spark.read.parquet(str(result.silver_rejected_partition_path))
    all_reasons = {
        reason
        for row in rejected_df.collect()
        for reason in row["_rejection_reasons"]
    }
    assert RejectionReason.NON_INTEGRAL_PASSENGER_COUNT.value in all_reasons
    assert RejectionReason.NULL_FARE_AMOUNT.value in all_reasons


# ---------------------------------------------------------------------------
# Idempotent re-execution
# ---------------------------------------------------------------------------


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
