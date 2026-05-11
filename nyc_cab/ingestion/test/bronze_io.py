# pylint: disable=redefined-outer-name
"""Tests for :mod:`nyc_cab.ingestion.bronze_io`.

These tests cover the file-acquisition concerns of the Bronze pipeline:

* :class:`AcquiredSourceFile` -- the post-acquisition artifact
* :class:`_BronzeSourceCache` -- the private cache abstraction, including
  ``ensure_directory`` and ``evict_files``
* :func:`_derive_cache_file_path` -- the deterministic cache-path derivation
* :func:`_download_source_file` -- streaming HTTP download (tested with a
  mocked ``requests.get``)
* :func:`acquire_bronze_source_file` -- cache-first acquisition (tested with
  a mocked ``_download_source_file``)
"""

from __future__ import annotations

import dataclasses
import os
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

from nyc_cab.exceptions import InvalidRequestError
from nyc_cab.ingestion.bronze_io import (
    AcquiredSourceFile,
    _BronzeSourceCache,
    _derive_cache_file_path,
    _download_source_file,
    acquire_bronze_source_file,
)
from nyc_cab.ingestion.bronze_request import BronzeIngestionConfig


# --- Helpers ----------------------------------------------------------------


def _fake_download(_source_url, destination_path, _timeout_seconds):
    """Write a small file to simulate a successful download."""
    destination_path.write_bytes(b"fake parquet content")


def _make_aged_files(directory: Path, names: list[str]) -> list[Path]:
    """Create files with staggered modification times, oldest first."""
    base_time = time.time() - 1000
    paths = []
    for i, name in enumerate(names):
        path = directory / name
        path.write_bytes(b"data")
        os.utime(path, (base_time + i, base_time + i))
        paths.append(path)
    return paths


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


# --- AcquiredSourceFile: composition ----------------------------------------


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


# --- _BronzeSourceCache: type-check rejections ------------------------------


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


# --- _BronzeSourceCache: structural rejections ------------------------------


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


# --- _BronzeSourceCache: ensure_directory -----------------------------------


def test_ensure_directory_creates_nonexistent_directory(tmp_path: Path) -> None:
    """``ensure_directory`` creates the cache directory when it does not exist."""
    cache_dir = tmp_path / "new_cache"
    cache = _BronzeSourceCache.create_validated(cache_dir, 5)
    cache.ensure_directory()
    assert cache_dir.exists()
    assert cache_dir.is_dir()


def test_ensure_directory_creates_nested_parents(tmp_path: Path) -> None:
    """``ensure_directory`` creates parent directories as needed."""
    cache_dir = tmp_path / "deep" / "nested" / "cache"
    cache = _BronzeSourceCache.create_validated(cache_dir, 5)
    cache.ensure_directory()
    assert cache_dir.exists()


def test_ensure_directory_is_idempotent(tmp_path: Path) -> None:
    """Calling ``ensure_directory`` when the directory already exists is a no-op."""
    cache = _BronzeSourceCache.create_validated(tmp_path, 5)
    cache.ensure_directory()
    assert tmp_path.exists()


# --- _BronzeSourceCache: evict_files ----------------------------------------


def test_evict_files_removes_oldest_when_over_limit(tmp_path: Path) -> None:
    """The oldest files are evicted first when the count exceeds ``max_files``."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _make_aged_files(cache_dir, ["oldest.parquet", "middle.parquet", "newest.parquet"])
    cache = _BronzeSourceCache.create_validated(cache_dir, 2)
    cache.evict_files()
    remaining = sorted(f.name for f in cache_dir.iterdir())
    assert remaining == ["middle.parquet", "newest.parquet"]


def test_evict_files_no_op_when_under_limit(tmp_path: Path) -> None:
    """No files are evicted when the count is at or below ``max_files``."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _make_aged_files(cache_dir, ["a.parquet", "b.parquet"])
    cache = _BronzeSourceCache.create_validated(cache_dir, 5)
    cache.evict_files()
    assert len(list(cache_dir.iterdir())) == 2


def test_evict_files_no_op_when_at_limit(tmp_path: Path) -> None:
    """No files are evicted when the count equals ``max_files``."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _make_aged_files(cache_dir, ["a.parquet", "b.parquet", "c.parquet"])
    cache = _BronzeSourceCache.create_validated(cache_dir, 3)
    cache.evict_files()
    assert len(list(cache_dir.iterdir())) == 3


def test_evict_files_no_op_when_directory_missing(tmp_path: Path) -> None:
    """Eviction is a no-op when the cache directory does not exist."""
    cache_dir = tmp_path / "nonexistent"
    cache = _BronzeSourceCache.create_validated(cache_dir, 5)
    cache.evict_files()


def test_evict_files_removes_all_when_max_files_zero(tmp_path: Path) -> None:
    """With ``max_files=0``, all cached files are evicted."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _make_aged_files(cache_dir, ["a.parquet", "b.parquet"])
    cache = _BronzeSourceCache.create_validated(cache_dir, 0)
    cache.evict_files()
    assert len(list(cache_dir.iterdir())) == 0


def test_evict_files_ignores_subdirectories(tmp_path: Path) -> None:
    """Eviction only counts and removes regular files, not subdirectories."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _make_aged_files(cache_dir, ["a.parquet", "b.parquet"])
    (cache_dir / "subdir").mkdir()
    cache = _BronzeSourceCache.create_validated(cache_dir, 1)
    cache.evict_files()
    remaining = list(cache_dir.iterdir())
    remaining_names = sorted(f.name for f in remaining)
    assert "subdir" in remaining_names
    assert len([f for f in remaining if f.is_file()]) == 1


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


# --- _download_source_file -------------------------------------------------


def test_download_source_file_writes_content(tmp_path: Path, monkeypatch) -> None:
    """A successful download writes the response content to the destination."""
    dest = tmp_path / "downloaded.parquet"
    mock_response = MagicMock()
    mock_response.iter_content.return_value = [b"chunk1", b"chunk2"]
    monkeypatch.setattr(
        "nyc_cab.ingestion.bronze_io.requests.get",
        lambda url, timeout, stream: mock_response,
    )
    _download_source_file("https://x/y", dest, 60)
    mock_response.raise_for_status.assert_called_once()
    assert dest.read_bytes() == b"chunk1chunk2"


def test_download_source_file_raises_on_http_error(tmp_path: Path, monkeypatch) -> None:
    """An HTTP error from the server propagates as an exception."""
    dest = tmp_path / "downloaded.parquet"
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = requests.HTTPError("404 Not Found")
    monkeypatch.setattr(
        "nyc_cab.ingestion.bronze_io.requests.get",
        lambda url, timeout, stream: mock_response,
    )
    with pytest.raises(requests.HTTPError):
        _download_source_file("https://x/y", dest, 60)


def test_download_source_file_passes_timeout(tmp_path: Path, monkeypatch) -> None:
    """The timeout value is forwarded to ``requests.get``."""
    dest = tmp_path / "downloaded.parquet"
    captured = {}

    def _capture_get(url, timeout, stream):  # pylint: disable=unused-argument
        captured["timeout"] = timeout
        mock = MagicMock()
        mock.iter_content.return_value = [b"data"]
        return mock

    monkeypatch.setattr("nyc_cab.ingestion.bronze_io.requests.get", _capture_get)
    _download_source_file("https://x/y", dest, 42)
    assert captured["timeout"] == 42


# --- acquire_bronze_source_file: cache hit ----------------------------------


def test_acquire_cache_hit_returns_existing_file(tmp_path: Path, monkeypatch) -> None:
    """When the cache is enabled and the file exists, no download occurs."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cached_file = cache_dir / "data.parquet"
    cached_file.write_bytes(b"cached content")
    config = BronzeIngestionConfig.create_validated(cache_dir, 5, 60)

    download_called = {"count": 0}
    def _no_download(_url, _path, _timeout):
        download_called["count"] += 1

    monkeypatch.setattr("nyc_cab.ingestion.bronze_io._download_source_file", _no_download)

    result = acquire_bronze_source_file("https://x/data.parquet", "data.parquet", config)

    assert result.cache_hit is True
    assert result.local_path == cached_file
    assert download_called["count"] == 0


# --- acquire_bronze_source_file: cache miss ---------------------------------


def test_acquire_cache_miss_downloads_and_returns(tmp_path: Path, monkeypatch) -> None:
    """When the file is not cached, it is downloaded and returned."""
    cache_dir = tmp_path / "cache"
    config = BronzeIngestionConfig.create_validated(cache_dir, 5, 60)

    monkeypatch.setattr("nyc_cab.ingestion.bronze_io._download_source_file", _fake_download)

    result = acquire_bronze_source_file("https://x/data.parquet", "data.parquet", config)

    assert result.cache_hit is False
    assert result.local_path == cache_dir / "data.parquet"
    assert result.local_path.exists()
    assert result.source_url == "https://x/data.parquet"


# --- acquire_bronze_source_file: cache disabled -----------------------------


def test_acquire_cache_disabled_always_downloads(tmp_path: Path, monkeypatch) -> None:
    """When the cache is disabled, every call downloads even if the file exists."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    existing = cache_dir / "data.parquet"
    existing.write_bytes(b"old content")
    config = BronzeIngestionConfig.create_validated(cache_dir, 0, 60)

    download_called = {"count": 0}
    def _counting_download(_url, destination_path, _timeout):
        destination_path.write_bytes(b"fresh content")
        download_called["count"] += 1

    monkeypatch.setattr("nyc_cab.ingestion.bronze_io._download_source_file", _counting_download)

    result = acquire_bronze_source_file("https://x/data.parquet", "data.parquet", config)

    assert result.cache_hit is False
    assert download_called["count"] == 1
    assert result.local_path.read_bytes() == b"fresh content"


# --- acquire_bronze_source_file: eviction -----------------------------------


def test_acquire_evicts_oldest_after_download(tmp_path: Path, monkeypatch) -> None:
    """After a cache-miss download, the cache trims to ``max_files``."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _make_aged_files(cache_dir, ["old1.parquet", "old2.parquet", "old3.parquet"])
    config = BronzeIngestionConfig.create_validated(cache_dir, 3, 60)

    monkeypatch.setattr("nyc_cab.ingestion.bronze_io._download_source_file", _fake_download)

    acquire_bronze_source_file("https://x/new.parquet", "new.parquet", config)

    remaining = sorted(f.name for f in cache_dir.iterdir() if f.is_file())
    assert len(remaining) == 3
    assert "new.parquet" in remaining
    assert "old1.parquet" not in remaining


def test_acquire_cache_disabled_skips_eviction(tmp_path: Path, monkeypatch) -> None:
    """When the cache is disabled, eviction does not run after download."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _make_aged_files(cache_dir, ["existing.parquet"])
    config = BronzeIngestionConfig.create_validated(cache_dir, 0, 60)

    monkeypatch.setattr("nyc_cab.ingestion.bronze_io._download_source_file", _fake_download)

    acquire_bronze_source_file("https://x/new.parquet", "new.parquet", config)

    # Both the existing file and the new download survive; no eviction.
    remaining = sorted(f.name for f in cache_dir.iterdir() if f.is_file())
    assert "existing.parquet" in remaining
    assert "new.parquet" in remaining


# --- acquire_bronze_source_file: source URL flows through -------------------


def test_acquire_source_url_flows_through_to_result(tmp_path: Path, monkeypatch) -> None:
    """The source URL passed to acquire appears in the returned artifact."""
    cache_dir = tmp_path / "cache"
    config = BronzeIngestionConfig.create_validated(cache_dir, 5, 60)
    monkeypatch.setattr("nyc_cab.ingestion.bronze_io._download_source_file", _fake_download)

    result = acquire_bronze_source_file("https://canonical/data.parquet", "data.parquet", config)

    assert result.source_url == "https://canonical/data.parquet"
