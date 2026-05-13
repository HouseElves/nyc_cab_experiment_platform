# pylint: disable=redefined-outer-name
"""
Silver transformation live tests (real Bronze data, on-demand).

These tests run the full ``transform_silver_month`` flow against Bronze
partitions produced from real TLC Yellow cab downloads. They are marked
``@pytest.mark.live`` and skipped by default in CI.

Run explicitly when you need to verify that real TLC data transforms
cleanly through the Silver v1 constraint set:

    pytest -m live test/silver_live.py

Prerequisites:

    - ``test/bronze_live.py`` does not need to run first; Bronze ingestion
      is performed inside the module fixture below.
    - Network access to ``d37ci6vzurychx.cloudfront.net``
    - Sufficient disk space for one or two monthly TLC files (~100-200 MB each)
    - The ``live`` marker must be registered in ``pytest.ini``::

        [pytest]
        markers =
            live: marks tests that require network access to external services
"""

from __future__ import annotations

import pytest

from nyc_cab.config import load_config
from nyc_cab.ingestion.bronze_entrypoint import ingest_bronze_month
from nyc_cab.ingestion.bronze_request import BronzeIngestionConfig, BronzeIngestionRequest
from nyc_cab.transform.silver_entrypoint import SilverTransformResult, transform_silver_month
from nyc_cab.transform.silver_request import SilverTransformRequest


pytestmark = pytest.mark.live

_EXPECTED_MIN_ROWS          = 1_000_000
_EXPECTED_MIN_ACCEPTED_RATIO = 0.90


# ---------------------------------------------------------------------------
# Module-scoped fixtures
#
# Bronze ingestion runs once per module so the ~200 MB download is
# not repeated for every assertion test. The Silver transform also runs once
# and its result is shared across the assertion tests below.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def live_runtime(spark, tmp_path_factory):
    """Download January 2023 TLC data, ingest to Bronze, return runtime config."""
    data_dir = tmp_path_factory.mktemp("silver_live_data")
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(data_dir)})
    cache_dir = data_dir / "cache"
    config = BronzeIngestionConfig.create_validated(cache_dir, 5, 120)
    request = BronzeIngestionRequest.create_validated("yellow", 2023, 1)
    ingest_bronze_month(spark, runtime, config, request)
    return runtime


@pytest.fixture(scope="module")
def live_result(spark, live_runtime) -> SilverTransformResult:
    """Transform January 2023 Bronze output through Silver once for all assertion tests."""
    request = SilverTransformRequest.create_validated("yellow", 2023, 1)
    return transform_silver_month(spark, live_runtime, request)


# ---------------------------------------------------------------------------
# January 2023 — assertion tests against live_result
# ---------------------------------------------------------------------------


def test_live_transform_produces_valid_result(live_result) -> None:
    """January 2023 Silver transform produces a valid SilverTransformResult."""
    assert isinstance(live_result, SilverTransformResult)
    assert live_result.is_valid()


def test_live_bronze_count_is_plausible(live_result) -> None:
    """Bronze row count exceeds the expected floor for a full month of TLC data."""
    assert live_result.bronze_count > _EXPECTED_MIN_ROWS


def test_live_reconciliation_holds(live_result) -> None:
    """bronze_count == accepted_count + rejected_count on real data."""
    assert live_result.bronze_count == live_result.accepted_count + live_result.rejected_count


def test_live_accepted_ratio_is_plausible(live_result) -> None:
    """At least 90 percent of January 2023 rows pass all Silver constraints."""
    accepted_ratio = live_result.accepted_count / live_result.bronze_count
    assert accepted_ratio >= _EXPECTED_MIN_ACCEPTED_RATIO


def test_live_partition_directories_exist(live_result) -> None:
    """Both Silver accepted and rejected partition directories exist on disk."""
    assert live_result.silver_partition_path.exists()
    assert live_result.silver_partition_path.is_dir()
    assert live_result.silver_rejected_partition_path.exists()
    assert live_result.silver_rejected_partition_path.is_dir()


# ---------------------------------------------------------------------------
# February 2023 — inline setup
#
# February 2023 is the month that motivated the Bronze schema equivalence
# layer: TLC changed passenger_count and RatecodeID from double to bigint.
# Bronze absorbs this via the numeric type family. Silver's pre-normalization
# integrality check uses col != floor(col), which evaluates to False (not
# NULL) for bigint values, so no rows are spuriously rejected for
# NON_INTEGRAL_PASSENGER_COUNT. This test confirms both layers handle the
# type drift end-to-end on real data.
# ---------------------------------------------------------------------------


def test_live_february_transforms_despite_schema_drift(spark, tmp_path_factory) -> None:
    """February 2023 transforms cleanly despite Bronze storing passenger_count as bigint."""
    data_dir = tmp_path_factory.mktemp("silver_live_feb")
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(data_dir)})
    cache_dir = data_dir / "cache"

    config = BronzeIngestionConfig.create_validated(cache_dir, 5, 120)
    bronze_request = BronzeIngestionRequest.create_validated("yellow", 2023, 2)
    ingest_bronze_month(spark, runtime, config, bronze_request)

    silver_request = SilverTransformRequest.create_validated("yellow", 2023, 2)
    result = transform_silver_month(spark, runtime, silver_request)

    assert result.is_valid()
    assert result.bronze_count > _EXPECTED_MIN_ROWS
    assert result.bronze_count == result.accepted_count + result.rejected_count
