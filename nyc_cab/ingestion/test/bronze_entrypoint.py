"""Tests for :mod:`nyc_cab.ingestion.bronze`.

These tests cover:

* :class:`BronzeIngestionResult` — the post-ingestion result, which composes a
  request, an acquired source file, and the new facts produced by the run
  (partition path, row count). The structural checks delegate to the composed
  members via :meth:`_Validated.validity_check`.
* :func:`ingest_bronze_month` — the orchestration stub that currently raises
  :class:`NotImplementedError`.
"""

from __future__ import annotations

import dataclasses

from pathlib import Path

import pytest

from nyc_cab.config import load_config
from nyc_cab.exceptions import InvalidRequestError
from nyc_cab.ingestion.bronze_entrypoint import BronzeIngestionResult, ingest_bronze_month
from nyc_cab.ingestion.bronze_io import AcquiredSourceFile
from nyc_cab.ingestion.bronze_request import (
    BronzeIngestionConfig,
    BronzeIngestionRequest,
)


def _request() -> BronzeIngestionRequest:
    """Build a valid Bronze ingestion request for tests."""
    return BronzeIngestionRequest.create_validated("yellow", 2023, 1)


def _source(tmp_path: Path) -> AcquiredSourceFile:
    """Build a valid acquired-source artifact rooted in ``tmp_path``."""
    file_path = tmp_path / "yellow_tripdata_2023-01.parquet"
    file_path.write_bytes(b"data")
    return AcquiredSourceFile.create_validated(file_path, "https://x/y", True)


# --- BronzeIngestionResult: happy paths -------------------------------------


def test_result_create_validated_happy_path(tmp_path: Path) -> None:
    """A well-formed result constructs cleanly."""
    partition = tmp_path / "year=2023" / "month=1"
    partition.mkdir(parents=True)
    result = BronzeIngestionResult.create_validated(
        _request(), _source(tmp_path), partition, 12345,
    )
    assert result.row_count == 12345
    assert result.is_valid()


def test_result_accepts_zero_row_count(tmp_path: Path) -> None:
    """Zero rows is structurally valid (empty partition write)."""
    partition = tmp_path / "year=2023" / "month=1"
    partition.mkdir(parents=True)
    result = BronzeIngestionResult.create_validated(
        _request(), _source(tmp_path), partition, 0,
    )
    assert result.row_count == 0


def test_result_accepts_nonexistent_partition_path(tmp_path: Path) -> None:
    """A partition directory that doesn't exist yet is structurally valid."""
    not_yet = tmp_path / "year=2023" / "month=1"
    result = BronzeIngestionResult.create_validated(
        _request(), _source(tmp_path), not_yet, 100,
    )
    assert result.bronze_partition_path == not_yet


# --- BronzeIngestionResult: type-check rejections ---------------------------


def test_result_rejects_non_request(tmp_path: Path) -> None:
    """``request`` must be a :class:`BronzeIngestionRequest`."""
    with pytest.raises(InvalidRequestError) as info:
        BronzeIngestionResult.create_validated(
            "not-a-request", _source(tmp_path), tmp_path, 100,
        )
    names = [v[0] for v in info.value.violations]
    assert "request" in names


def test_result_rejects_non_source(tmp_path: Path) -> None:
    """``source`` must be an :class:`AcquiredSourceFile`."""
    with pytest.raises(InvalidRequestError) as info:
        BronzeIngestionResult.create_validated(
            _request(), "not-a-source", tmp_path, 100,
        )
    names = [v[0] for v in info.value.violations]
    assert "source" in names


def test_result_rejects_string_partition_path(tmp_path: Path) -> None:
    """``bronze_partition_path`` must be a ``Path``."""
    with pytest.raises(InvalidRequestError) as info:
        BronzeIngestionResult.create_validated(
            _request(), _source(tmp_path), str(tmp_path), 100,
        )
    names = [v[0] for v in info.value.violations]
    assert "bronze_partition_path" in names


def test_result_rejects_bool_row_count(tmp_path: Path) -> None:
    """``row_count`` rejects ``True``/``False`` despite int compatibility."""
    with pytest.raises(InvalidRequestError) as info:
        BronzeIngestionResult.create_validated(
            _request(), _source(tmp_path), tmp_path, True,
        )
    assert ("row_count", True) in info.value.violations


def test_result_rejects_string_row_count(tmp_path: Path) -> None:
    """``row_count`` must be an int."""
    with pytest.raises(InvalidRequestError) as info:
        BronzeIngestionResult.create_validated(
            _request(), _source(tmp_path), tmp_path, "100",
        )
    assert ("row_count", "100") in info.value.violations


# --- BronzeIngestionResult: structural rejections ---------------------------


def test_result_rejects_negative_row_count(tmp_path: Path) -> None:
    """Negative row counts violate the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        BronzeIngestionResult.create_validated(
            _request(), _source(tmp_path), tmp_path, -1,
        )
    assert ("row_count", -1) in info.value.violations


def test_result_rejects_file_as_partition_path(tmp_path: Path) -> None:
    """A regular file at the partition path violates the directory rule."""
    file_path = tmp_path / "not-a-dir"
    file_path.write_text("data")
    with pytest.raises(InvalidRequestError) as info:
        BronzeIngestionResult.create_validated(
            _request(), _source(tmp_path), file_path, 100,
        )
    assert ("bronze_partition_path", file_path) in info.value.violations


# --- validity_check chaining ------------------------------------------------


def test_result_chaining_catches_invalid_request(tmp_path: Path) -> None:
    """A structurally-bad request bubbles up as a ``request`` violation."""
    # Bypass create_validated to construct a structurally-invalid request.
    bad_request = BronzeIngestionRequest("yellow", 2023, 13)
    with pytest.raises(InvalidRequestError) as info:
        BronzeIngestionResult.create_validated(
            bad_request, _source(tmp_path), tmp_path, 100,
        )
    names = [v[0] for v in info.value.violations]
    assert "request" in names


def test_result_chaining_catches_invalid_source(tmp_path: Path) -> None:
    """A structurally-bad source bubbles up as a ``source`` violation."""
    # Bypass create_validated to construct an invalid source (blank URL).
    file_path = tmp_path / "data.parquet"
    file_path.write_bytes(b"data")
    bad_source = AcquiredSourceFile(file_path, "   ", True)
    with pytest.raises(InvalidRequestError) as info:
        BronzeIngestionResult.create_validated(
            _request(), bad_source, tmp_path, 100,
        )
    names = [v[0] for v in info.value.violations]
    assert "source" in names


def test_result_chaining_aggregates_multiple_member_failures(tmp_path: Path) -> None:
    """Multiple structurally-bad composed members all surface in one exception."""
    bad_request = BronzeIngestionRequest("yellow", 2023, 13)
    file_path = tmp_path / "data.parquet"
    file_path.write_bytes(b"data")
    bad_source = AcquiredSourceFile(file_path, "   ", True)
    with pytest.raises(InvalidRequestError) as info:
        BronzeIngestionResult.create_validated(
            bad_request, bad_source, tmp_path, 100,
        )
    names = [v[0] for v in info.value.violations]
    assert "request" in names
    assert "source" in names


# --- Frozenness -------------------------------------------------------------


def test_result_is_frozen(tmp_path: Path) -> None:
    """:class:`BronzeIngestionResult` rejects attribute mutation."""

    partition = tmp_path / "year=2023" / "month=1"
    partition.mkdir(parents=True)
    result = BronzeIngestionResult.create_validated(
        _request(), _source(tmp_path), partition, 100,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.row_count = 200  # type: ignore[misc]


# --- ingest_bronze_month stub -----------------------------------------------


def test_ingest_bronze_month_raises_not_implemented(tmp_path: Path) -> None:
    """The orchestration stub raises ``NotImplementedError`` until implemented."""
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    config = BronzeIngestionConfig.create_validated(tmp_path / "cache", 10, 60)
    request = _request()
    with pytest.raises(NotImplementedError):
        # The Spark argument is unused by the stub; ``None`` is acceptable here.
        ingest_bronze_month(None, runtime, config, request)  # type: ignore[arg-type]
