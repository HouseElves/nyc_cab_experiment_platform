"""
Define cache-aware Bronze file acquisition logic.

Module Constraints
------------------

    - The logic is scoped to local cache + remote fetch behavior.
    - The only concern is file acquisition for downstream consumption.
    - *Spark* is not required for acquisition operations


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

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from nyc_cab._validation import _Validated, CheckSpec, CheckTuple
from nyc_cab.ingestion.bronze_request import BronzeIngestionConfig


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
        Provide structural validation rules for AcquiredSourceFile instance attributes.

        Checks at this level are structural only. Bronze-specific support rules (i.e. semantic validations)
        remain the responsibility of the Bronze contract.
        """
        return (
            (self.local_path.exists() and self.local_path.is_file(), "local_path", self.local_path),
            (self.source_url.strip() != "", "source_url", self.source_url)
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
        Provide structural validation rules for _BronzeSourceCache instance attributes.

        Checks at this level are structural only. Bronze-specific support rules (i.e. semantic validations)
        remain the responsibility of the Bronze contract.
        """
        return (
            (not self.cache_directory.exists() or self.cache_directory.is_dir(), "cache_directory", self.cache_directory),
            (self.max_files >= 0, "max_files", self.max_files),
        )

    @property
    def enabled(self) -> bool:
        """Return whether this cache will retain any files."""
        return self.max_files > 0

    def ensure_directory(self) -> None:
        """Create the source cache directory if it does not exist."""
        raise NotImplementedError

    def evict_files(self) -> None:
        """Trim the local source cache to the configured file limit."""
        raise NotImplementedError


def _derive_cache_file_path(cache_directory: Path, source_filename: str) -> Path:
    """Return the deterministic local cache file path."""
    return cache_directory / source_filename


def _download_source_file(source_url: str, destination_path: Path, timeout_seconds: int) -> None:
    """Download a remote source file to a local path."""
    raise NotImplementedError


def acquire_bronze_source_file(source_url: str, source_filename: str, config: BronzeIngestionConfig) -> AcquiredSourceFile:
    """Return a local source file path, using cache-first acquisition."""
    raise NotImplementedError
