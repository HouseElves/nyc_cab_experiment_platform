"""Tests for :mod:`nyc_cab.ingestion.bronze_io`.

These tests cover the file-acquisition concerns of the Bronze pipeline:

* :class:`AcquiredSourceFile` — the post-acquisition artifact
* :class:`_BronzeSourceCache` — the private cache abstraction
* :func:`_derive_cache_file_path` — the deterministic cache-path derivation
* The public stub :func:`acquire_bronze_source_file` and the private stub
  :func:`_download_source_file`, both of which currently raise
  :class:`NotImplementedError`.
"""

from __future__ import annotations

import dataclasses

from pathlib import Path

import pytest

from nyc_cab.exceptions import InvalidRequestError
from nyc_cab.ingestion.bronze_io import (
    AcquiredSourceFile,
    _BronzeSourceCache,
    _derive_cache_file_path,
    _download_source_file,
    acquire_bronze_source_file,
)
from nyc_cab.ingestion.bronze_request import BronzeIngestionConfig


# --- AcquiredSourceFile: happy paths ----------------------------------------


def test_acquired_source_file_happy_path(tmp_path: Path) -> None:
    """A real file path with a real source URL produces a valid artifact."""
    file_path = tmp_path / "data.parquet"
    file_path.write_bytes(b"data")
    acquired = AcquiredSourceFile.create_validated(
        file_path, "https://example.invalid/data.parquet", True,
    )
    assert acquired.local_path == file_path
    assert acquired.cache_hit is True


def test_acquired_source_file_cache_miss(tmp_path: Path) -> None:
    """A cache_hit value of ``False`` is structurally valid."""
    file_path = tmp_path / "data.parquet"
    file_path.write_bytes(b"data")
    acquired = AcquiredSourceFile.create_validated(file_path, "https://x/y", False)
    assert acquired.cache_hit is False


# --- AcquiredSourceFile: type-check rejections ------------------------------


def test_acquired_source_file_rejects_string_local_path() -> None:
    """``local_path`` must be a ``Path``; strings are rejected."""
    with pytest.raises(InvalidRequestError) as info:
        AcquiredSourceFile.create_validated("/tmp/data.parquet", "https://x/y", True)
    names = [v[0] for v in info.value.violations]
    assert "local_path" in names


def test_acquired_source_file_rejects_int_source_url(tmp_path: Path) -> None:
    """``source_url`` must be a string."""
    file_path = tmp_path / "data.parquet"
    file_path.write_bytes(b"data")
    with pytest.raises(InvalidRequestError) as info:
        AcquiredSourceFile.create_validated(file_path, 12345, True)
    assert ("source_url", 12345) in info.value.violations


def test_acquired_source_file_rejects_non_bool_cache_hit(tmp_path: Path) -> None:
    """``cache_hit`` must be a bool."""
    file_path = tmp_path / "data.parquet"
    file_path.write_bytes(b"data")
    with pytest.raises(InvalidRequestError) as info:
        AcquiredSourceFile.create_validated(file_path, "https://x/y", "yes")
    assert ("cache_hit", "yes") in info.value.violations


# --- AcquiredSourceFile: structural rejections ------------------------------


def test_acquired_source_file_rejects_missing_file() -> None:
    """A path that does not exist on disk violates the file-must-exist rule."""
    with pytest.raises(InvalidRequestError) as info:
        AcquiredSourceFile.create_validated(
            Path("/no/such/file.parquet"), "https://x/y", False,
        )
    names = [v[0] for v in info.value.violations]
    assert "local_path" in names


def test_acquired_source_file_rejects_directory(tmp_path: Path) -> None:
    """A path that exists as a directory violates the file-must-exist rule."""
    with pytest.raises(InvalidRequestError) as info:
        AcquiredSourceFile.create_validated(tmp_path, "https://x/y", False)
    names = [v[0] for v in info.value.violations]
    assert "local_path" in names


def test_acquired_source_file_rejects_blank_source_url(tmp_path: Path) -> None:
    """A whitespace-only ``source_url`` violates the structural rule."""
    file_path = tmp_path / "data.parquet"
    file_path.write_bytes(b"data")
    with pytest.raises(InvalidRequestError) as info:
        AcquiredSourceFile.create_validated(file_path, "   ", True)
    assert ("source_url", "   ") in info.value.violations


# --- AcquiredSourceFile: composition --------------------------------------


def test_acquired_source_file_validity_check_passing(tmp_path: Path) -> None:
    """``validity_check`` returns a passing tuple for a valid instance."""
    file_path = tmp_path / "data.parquet"
    file_path.write_bytes(b"data")
    acquired = AcquiredSourceFile.create_validated(file_path, "https://x/y", True)
    passed, name, value = acquired.validity_check("source")
    assert passed is True
    assert name == "source"
    assert value is acquired


def test_acquired_source_file_validity_check_failing(tmp_path: Path) -> None:
    """``validity_check`` returns a failing tuple when the instance is invalid."""
    # Bypass ``create_validated`` to construct an invalid instance.
    acquired = AcquiredSourceFile(tmp_path, "   ", True)
    passed, name, value = acquired.validity_check("source")
    assert passed is False
    assert name == "source"
    assert value is acquired


# --- AcquiredSourceFile: frozenness -----------------------------------------


def test_acquired_source_file_is_frozen(tmp_path: Path) -> None:
    """:class:`AcquiredSourceFile` rejects attribute mutation."""

    file_path = tmp_path / "data.parquet"
    file_path.write_bytes(b"data")
    acquired = AcquiredSourceFile.create_validated(file_path, "https://x/y", True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        acquired.cache_hit = False  # type: ignore[misc]


# --- _BronzeSourceCache: happy paths ----------------------------------------


def test_cache_create_validated_happy_path(tmp_path: Path) -> None:
    """A well-formed cache constructs cleanly."""
    cache = _BronzeSourceCache.create_validated(tmp_path, 5)
    assert cache.cache_directory == tmp_path
    assert cache.max_files == 5


def test_cache_accepts_zero_max_files(tmp_path: Path) -> None:
    """``max_files=0`` is the canonical disabled-cache state."""
    cache = _BronzeSourceCache.create_validated(tmp_path, 0)
    assert cache.max_files == 0


def test_cache_accepts_nonexistent_directory(tmp_path: Path) -> None:
    """A non-existent cache directory is structurally valid."""
    nonexistent = tmp_path / "not-yet"
    cache = _BronzeSourceCache.create_validated(nonexistent, 5)
    assert cache.cache_directory == nonexistent


# --- _BronzeSourceCache: type-check rejections -----------------------------


def test_cache_rejects_string_cache_directory() -> None:
    """``cache_directory`` must be a ``Path``."""
    with pytest.raises(InvalidRequestError) as info:
        _BronzeSourceCache.create_validated("/tmp/cache", 5)
    names = [v[0] for v in info.value.violations]
    assert "cache_directory" in names


def test_cache_rejects_bool_max_files(tmp_path: Path) -> None:
    """``max_files`` rejects ``True``/``False``."""
    with pytest.raises(InvalidRequestError) as info:
        _BronzeSourceCache.create_validated(tmp_path, True)
    assert ("max_files", True) in info.value.violations


# --- _BronzeSourceCache: structural rejections -----------------------------


def test_cache_rejects_negative_max_files(tmp_path: Path) -> None:
    """Negative ``max_files`` violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        _BronzeSourceCache.create_validated(tmp_path, -1)
    assert ("max_files", -1) in info.value.violations


def test_cache_rejects_file_as_cache_directory(tmp_path: Path) -> None:
    """A path that exists as a regular file violates the directory rule."""
    file_path = tmp_path / "not-a-directory"
    file_path.write_text("data")
    with pytest.raises(InvalidRequestError) as info:
        _BronzeSourceCache.create_validated(file_path, 5)
    assert ("cache_directory", file_path) in info.value.violations


# --- _BronzeSourceCache: enabled property -----------------------------------


def test_cache_enabled_true_when_max_files_positive(tmp_path: Path) -> None:
    """A positive ``max_files`` makes the cache enabled."""
    cache = _BronzeSourceCache.create_validated(tmp_path, 1)
    assert cache.enabled is True


def test_cache_enabled_false_when_max_files_zero(tmp_path: Path) -> None:
    """A zero ``max_files`` makes the cache disabled."""
    cache = _BronzeSourceCache.create_validated(tmp_path, 0)
    assert cache.enabled is False


# --- _BronzeSourceCache: stubbed methods ------------------------------------


def test_cache_ensure_directory_raises_not_implemented(tmp_path: Path) -> None:
    """``ensure_directory`` is a stub awaiting implementation."""
    cache = _BronzeSourceCache.create_validated(tmp_path, 5)
    with pytest.raises(NotImplementedError):
        cache.ensure_directory()


def test_cache_evict_files_raises_not_implemented(tmp_path: Path) -> None:
    """``evict_files`` is a stub awaiting implementation."""
    cache = _BronzeSourceCache.create_validated(tmp_path, 5)
    with pytest.raises(NotImplementedError):
        cache.evict_files()


# --- _derive_cache_file_path ------------------------------------------------


def test_derive_cache_file_path_joins_directory_and_filename(tmp_path: Path) -> None:
    """The cache-path helper joins the cache directory with the source filename."""
    result = _derive_cache_file_path(tmp_path, "yellow_tripdata_2023-01.parquet")
    assert result == tmp_path / "yellow_tripdata_2023-01.parquet"


def test_derive_cache_file_path_with_nested_directory(tmp_path: Path) -> None:
    """The helper preserves the directory's full path structure."""
    nested = tmp_path / "deep" / "cache"
    result = _derive_cache_file_path(nested, "data.parquet")
    assert result == nested / "data.parquet"


# --- Stubs awaiting implementation ------------------------------------------


def test_download_source_file_raises_not_implemented(tmp_path: Path) -> None:
    """``_download_source_file`` is a stub awaiting implementation."""
    with pytest.raises(NotImplementedError):
        _download_source_file("https://x/y", tmp_path / "out.parquet", 60)


def test_acquire_bronze_source_file_raises_not_implemented(tmp_path: Path) -> None:
    """``acquire_bronze_source_file`` is a stub awaiting implementation."""
    config = BronzeIngestionConfig.create_validated(tmp_path, 10, 60)
    with pytest.raises(NotImplementedError):
        acquire_bronze_source_file("https://x/y", "data.parquet", config)
