"""
Tests for :mod:`nyc_cab.ingestion.source_resolver`.

These tests cover the path-resolution layer of the Bronze pipeline:

* :class:`BronzeResolvedPaths` — the validated aggregate of source and target paths
* :func:`derive_source_filename` and :func:`derive_source_url` — request-shaped wrappers
* :func:`derive_bronze_root_path` and :func:`derive_bronze_partition_path` — path derivation
* :func:`resolve_bronze_paths` — the aggregator entry point
"""

from __future__ import annotations

import dataclasses

from pathlib import Path

import pytest

from nyc_cab.config import RuntimeConfig, load_config
from nyc_cab.exceptions import InvalidRequestError
from nyc_cab.ingestion.bronze_request import BronzeIngestionRequest
from nyc_cab.ingestion.source_resolver import (
    BronzeResolvedPaths,
    derive_bronze_partition_path,
    derive_bronze_root_path,
    derive_source_filename,
    derive_source_url,
    resolve_bronze_paths,
)

pytestmark = pytest.mark.unit


def _runtime_config(tmp_path: Path) -> RuntimeConfig:
    """Build a runtime config rooted at the supplied temporary directory."""
    return load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})


# --- BronzeResolvedPaths: happy paths ---------------------------------------


def test_resolved_paths_create_validated_happy_path(tmp_path: Path) -> None:
    """A well-formed paths bundle constructs cleanly."""
    paths = BronzeResolvedPaths.create_validated(
        "https://x/y/data.parquet",
        "data.parquet",
        tmp_path,
        tmp_path / "cab_type=yellow" / "year=2023" / "month=1",
    )
    assert paths.source_url == "https://x/y/data.parquet"
    assert paths.source_filename == "data.parquet"
    assert paths.bronze_root_path == tmp_path


def test_resolved_paths_accepts_nonexistent_partition(tmp_path: Path) -> None:
    """A non-existent partition path is structurally valid (created at write time)."""
    not_yet = tmp_path / "year=2023" / "month=1"
    paths = BronzeResolvedPaths.create_validated(
        "https://x/y/data.parquet", "data.parquet", tmp_path, not_yet,
    )
    assert paths.bronze_partition_path == not_yet


# --- BronzeResolvedPaths: type-check rejections -----------------------------


def test_resolved_paths_rejects_int_source_url(tmp_path: Path) -> None:
    """``source_url`` must be a string."""
    with pytest.raises(InvalidRequestError) as info:
        BronzeResolvedPaths.create_validated(
            12345, "data.parquet", tmp_path, tmp_path / "p",
        )
    assert ("source_url", 12345) in info.value.violations


def test_resolved_paths_rejects_int_source_filename(tmp_path: Path) -> None:
    """``source_filename`` must be a string."""
    with pytest.raises(InvalidRequestError) as info:
        BronzeResolvedPaths.create_validated(
            "https://x/y", 42, tmp_path, tmp_path / "p",
        )
    assert ("source_filename", 42) in info.value.violations


def test_resolved_paths_rejects_string_root_path(tmp_path: Path) -> None:
    """``bronze_root_path`` must be a ``Path``."""
    with pytest.raises(InvalidRequestError) as info:
        BronzeResolvedPaths.create_validated(
            "https://x/y", "data.parquet", str(tmp_path), tmp_path / "p",
        )
    names = [v[0] for v in info.value.violations]
    assert "bronze_root_path" in names


def test_resolved_paths_rejects_string_partition_path(tmp_path: Path) -> None:
    """``bronze_partition_path`` must be a ``Path``."""
    with pytest.raises(InvalidRequestError) as info:
        BronzeResolvedPaths.create_validated(
            "https://x/y", "data.parquet", tmp_path, str(tmp_path / "p"),
        )
    names = [v[0] for v in info.value.violations]
    assert "bronze_partition_path" in names


# --- BronzeResolvedPaths: structural rejections -----------------------------


def test_resolved_paths_rejects_blank_source_url(tmp_path: Path) -> None:
    """A whitespace-only ``source_url`` violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        BronzeResolvedPaths.create_validated(
            "   ", "data.parquet", tmp_path, tmp_path / "p",
        )
    assert ("source_url", "   ") in info.value.violations


def test_resolved_paths_rejects_blank_source_filename(tmp_path: Path) -> None:
    """A whitespace-only ``source_filename`` violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        BronzeResolvedPaths.create_validated(
            "https://x/y", "   ", tmp_path, tmp_path / "p",
        )
    assert ("source_filename", "   ") in info.value.violations


def test_resolved_paths_rejects_file_as_root_path(tmp_path: Path) -> None:
    """A regular file at the root path violates the directory rule."""
    file_path = tmp_path / "not-a-dir"
    file_path.write_text("data")
    with pytest.raises(InvalidRequestError) as info:
        BronzeResolvedPaths.create_validated(
            "https://x/y", "data.parquet", file_path, tmp_path / "p",
        )
    assert ("bronze_root_path", file_path) in info.value.violations


def test_resolved_paths_rejects_file_as_partition_path(tmp_path: Path) -> None:
    """A regular file at the partition path violates the directory rule."""
    file_path = tmp_path / "not-a-dir"
    file_path.write_text("data")
    with pytest.raises(InvalidRequestError) as info:
        BronzeResolvedPaths.create_validated(
            "https://x/y", "data.parquet", tmp_path, file_path,
        )
    assert ("bronze_partition_path", file_path) in info.value.violations


# --- Frozenness -------------------------------------------------------------


def test_resolved_paths_is_frozen(tmp_path: Path) -> None:
    """:class:`BronzeResolvedPaths` rejects attribute mutation."""

    paths = BronzeResolvedPaths.create_validated(
        "https://x/y", "data.parquet", tmp_path, tmp_path / "p",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        paths.source_url = "https://other"  # type: ignore[misc]


# --- derive_source_filename / derive_source_url -----------------------------


def test_derive_source_filename_uses_request_components() -> None:
    """The filename helper unpacks the request and consults the contract."""
    request = BronzeIngestionRequest.create_validated("yellow", 2023, 1)
    assert derive_source_filename(request) == "yellow_tripdata_2023-01.parquet"


def test_derive_source_filename_zero_pads_month() -> None:
    """Single-digit months in the request produce zero-padded filenames."""
    request = BronzeIngestionRequest.create_validated("yellow", 2023, 7)
    assert derive_source_filename(request) == "yellow_tripdata_2023-07.parquet"


def test_derive_source_url_concatenates_base_and_filename() -> None:
    """The URL helper combines the contract's base URL with the derived filename."""
    request = BronzeIngestionRequest.create_validated("yellow", 2023, 2)
    url = derive_source_url(request)
    assert url.startswith("https://")
    assert url.endswith("yellow_tripdata_2023-02.parquet")


# --- derive_bronze_root_path ------------------------------------------------


def test_derive_bronze_root_path_returns_paths_bronze(tmp_path: Path) -> None:
    """The root path is taken directly from the runtime config's ``paths.bronze``."""
    runtime = _runtime_config(tmp_path)
    assert derive_bronze_root_path(runtime) == runtime.paths.bronze


# --- derive_bronze_partition_path -------------------------------------------


def test_derive_bronze_partition_path_uses_hive_layout(tmp_path: Path) -> None:
    """The partition path uses Hive-style ``key=value`` directory components."""
    runtime = _runtime_config(tmp_path)
    request = BronzeIngestionRequest.create_validated("yellow", 2023, 1)
    partition = derive_bronze_partition_path(runtime, request)
    parts = partition.parts
    assert "cab_type=yellow" in parts
    assert "year=2023" in parts
    assert "month=1" in parts


def test_derive_bronze_partition_path_nests_under_bronze_root(tmp_path: Path) -> None:
    """The partition path lives under the Bronze root."""
    runtime = _runtime_config(tmp_path)
    request = BronzeIngestionRequest.create_validated("yellow", 2023, 1)
    partition = derive_bronze_partition_path(runtime, request)
    assert str(partition).startswith(str(derive_bronze_root_path(runtime)))


# --- resolve_bronze_paths ---------------------------------------------------


def test_resolve_bronze_paths_aggregates_validated_result(tmp_path: Path) -> None:
    """The aggregator returns a fully-validated :class:`BronzeResolvedPaths`."""
    runtime = _runtime_config(tmp_path)
    request = BronzeIngestionRequest.create_validated("yellow", 2023, 1)
    paths = resolve_bronze_paths(runtime, request)
    assert paths.is_valid()
    assert paths.source_filename == "yellow_tripdata_2023-01.parquet"
    assert paths.source_url.endswith("yellow_tripdata_2023-01.parquet")
    assert paths.bronze_root_path == runtime.paths.bronze
    assert "cab_type=yellow" in paths.bronze_partition_path.parts


def test_resolve_bronze_paths_preserves_request_period(tmp_path: Path) -> None:
    """The aggregator threads the request's year and month through to the partition."""
    runtime = _runtime_config(tmp_path)
    request = BronzeIngestionRequest.create_validated("yellow", 2023, 12)
    paths = resolve_bronze_paths(runtime, request)
    assert "year=2023" in paths.bronze_partition_path.parts
    assert "month=12" in paths.bronze_partition_path.parts
