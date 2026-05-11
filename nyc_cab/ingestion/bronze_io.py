"""
Define cache-aware Bronze file acquisition logic.

Module Constraints
------------------

    - The logic is scoped to local cache + remote fetch behavior.
    - The only concern is file acquisition for downstream consumption.
    - *Spark* is not required for acquisition operations.


Class Relationships
-------------------

    .. mermaid::

        classDiagram

            dataclass <|-- AcquiredSourceFile
            _Validated <|-- AcquiredSourceFile

            class AcquiredSourceFile {
                <<immutable>>
                string source_url
                bool cache_hit
                }
            AcquiredSourceFile *-- Path : local_path


"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import requests

from nyc_cab._validation import _Validated, CheckSpec, CheckTuple
from nyc_cab.ingestion.bronze_request import BronzeIngestionConfig


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AcquiredSourceFile(_Validated):
    """Describe an acquired Bronze source file."""

    local_path: Path
    source_url: str
    cache_hit: bool

    _type_check_specs: ClassVar[tuple[CheckSpec, ...]] = (
        ("local_path", Path),
        ("source_url", str),
        ("cache_hit", bool),
    )

    def _structural_checks(self) -> tuple[CheckTuple, ...]:
        """
        Provide structural validation rules for instance attributes.

        Checks at this level are structural only. Bronze-specific support rules (i.e. semantic validations)
        remain the responsibility of the Bronze contract.
        """
        return (
            # acquired files MUST exist and MUST be regular files
            (self.local_path.exists() and self.local_path.is_file(), "local_path", self.local_path),
            (self.source_url.strip() != "", "source_url", self.source_url),
        )


@dataclass(frozen=True)
class _BronzeSourceCache(_Validated):
    """Manage local cache-aware acquisition of Bronze source files."""

    cache_directory: Path
    max_files: int

    _type_check_specs: ClassVar[tuple[CheckSpec, ...]] = (
        ("cache_directory", Path),
        ("max_files", int, bool),
    )

    def _structural_checks(self) -> tuple[CheckTuple, ...]:
        """
        Provide structural validation rules for instance attributes.

        Checks at this level are structural only. Bronze-specific support rules (i.e. semantic validations)
        remain the responsibility of the Bronze contract.
        """
        return (
            # cache directory may not exist yet; must be a directory if it does
            (not self.cache_directory.exists() or self.cache_directory.is_dir(), "cache_directory", self.cache_directory),
            (self.max_files >= 0, "max_files", self.max_files),
        )

    @property
    def enabled(self) -> bool:
        """Return whether this cache will retain any files."""
        return self.max_files > 0

    def ensure_directory(self) -> None:
        """Create the source cache directory if it does not exist."""
        self.cache_directory.mkdir(parents=True, exist_ok=True)

    def evict_files(self) -> None:
        """
        Trim the local source cache to the configured file limit.

        Evicts the oldest files first (by modification time) until the
        number of cached files is at or below ``max_files``.
        """
        if not self.cache_directory.exists():
            return
        cached_files = sorted(
            [f for f in self.cache_directory.iterdir() if f.is_file()],
            key=lambda f: f.stat().st_mtime,
        )
        evict_count = 0
        while len(cached_files) > self.max_files:
            evicted = cached_files.pop(0)
            evicted.unlink()
            evict_count += 1
        if evict_count > 0:
            logger.info("Evicted %d cached file(s)", evict_count)


def _derive_cache_file_path(cache_directory: Path, source_filename: str) -> Path:
    """Return the deterministic local cache file path."""
    return cache_directory / source_filename


def _download_source_file(source_url: str, destination_path: Path, timeout_seconds: int) -> None:
    """Download a remote source file to a local path."""
    response = requests.get(source_url, timeout=timeout_seconds, stream=True)
    response.raise_for_status()
    with open(destination_path, "wb") as out:
        for chunk in response.iter_content(chunk_size=8192):
            out.write(chunk)


def acquire_bronze_source_file(source_url: str, source_filename: str, config: BronzeIngestionConfig) -> AcquiredSourceFile:
    """
    Acquire a Bronze source file using cache-first logic.

    When the cache is enabled (``max_files > 0``), the function checks for a
    cached copy before downloading. After a successful download, the cache is
    trimmed to the configured file limit.

    When the cache is disabled (``max_files == 0``), every call downloads
    fresh. The file is still written to the cache directory (it serves as the
    download staging location) but is never checked for reuse and never
    evicted.
    """
    cache = _BronzeSourceCache.create_validated(
        config.source_cache_directory,
        config.source_cache_max_files,
    )
    cache_file_path = _derive_cache_file_path(cache.cache_directory, source_filename)

    # Cache hit: return the existing file without downloading.
    if cache.enabled and cache_file_path.is_file():
        logger.info("Cache hit: %s", cache_file_path)
        return AcquiredSourceFile.create_validated(cache_file_path, source_url, True)

    # Cache miss (or cache disabled): download.
    logger.info("Cache miss: downloading from %s", source_url)
    cache.ensure_directory()
    _download_source_file(source_url, cache_file_path, config.source_download_timeout_seconds)
    logger.info("Downloaded to %s", cache_file_path)

    # Evict oldest files if the cache is enabled and over the limit.
    if cache.enabled:
        cache.evict_files()

    return AcquiredSourceFile.create_validated(cache_file_path, source_url, False)
