"""
Define source and target path resolution.

Module Constraints
------------------

    - Path resolution is deterministic.
    - Path resolution is cheap to test.
    - Path resolution does no I/O.
    - *Spark* is not required for resolution operations.

Class Relationships
-------------------

.. mermaid::

    classDiagram

        dataclass <|-- BronzeResolvedPaths
        _Validated <|-- BronzeResolvedPaths

        class BronzeResolvedPaths {
            <<immutable>>
            string source_url
            string source_filename
            }
        BronzeResolvedPaths *-- Path : bronze_root_path
        BronzeResolvedPaths *-- Path : bronze_partition_path

"""

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from nyc_cab._validation import _Validated, CheckSpec, CheckTuple
from nyc_cab.config import RuntimeConfig
from nyc_cab.ingestion.bronze_request import BronzeIngestionRequest

from nyc_cab.contracts.bronze import (
    derive_bronze_source_url,
    derive_bronze_source_filename,
)


@dataclass(frozen=True)
class BronzeResolvedPaths(_Validated):
    """Describe resolved source and target paths for a Bronze ingestion request."""

    source_url: str
    source_filename: str
    bronze_root_path: Path
    bronze_partition_path: Path

    _type_check_specs: ClassVar[tuple[CheckSpec, ...]] = (
        ("source_url", str),
        ("source_filename", str),
        ("bronze_root_path", Path),
        ("bronze_partition_path", Path),
    )

    def _structural_checks(self) -> tuple[CheckTuple, ...]:
        """
        Provide structural validation rules for BronzeResolvedPaths instance attributes.

        Checks at this level are structural only. Bronze-specific support rules (i.e. semantic validations)
        remain the responsibility of the Bronze contract.
        """
        return (
            (self.source_url.strip() != "", "source_url", self.source_url),
            (self.source_filename.strip() != "", "source_filename", self.source_filename),
            # paths may or may not exist on the file system. But if they do exist, they must be directories.
            (not self.bronze_root_path.exists() or self.bronze_root_path.is_dir(), "bronze_root_path", self.bronze_root_path),
            (not self.bronze_partition_path.exists() or self.bronze_partition_path.is_dir(), "bronze_partition_path", self.bronze_partition_path),
        )


def derive_source_filename(request: BronzeIngestionRequest) -> str:
    """Return the canonical source filename for a Bronze ingestion request."""
    return derive_bronze_source_filename(request.cab_type, request.year, request.month)


def derive_source_url(request: BronzeIngestionRequest) -> str:
    """Return the canonical remote source URL for a Bronze ingestion request."""
    return derive_bronze_source_url(request.cab_type, request.year, request.month)


def derive_bronze_root_path(runtime_config: RuntimeConfig) -> Path:
    """Return the Bronze layer root path for the active deployment."""
    return runtime_config.paths.bronze


def derive_bronze_partition_path(runtime_config: RuntimeConfig, request: BronzeIngestionRequest) -> Path:
    """Return the Bronze partition directory for a Bronze ingestion request."""
    return (
        derive_bronze_root_path(runtime_config)
        / f"cab_type={request.cab_type}"
        / f"year={request.year}"
        / f"month={request.month}"
    )


def resolve_bronze_paths(
    runtime_config: RuntimeConfig,
    request: BronzeIngestionRequest,
) -> BronzeResolvedPaths:
    """Resolve the full set of source and target paths for a Bronze ingestion request."""
    return BronzeResolvedPaths.create_validated(
        derive_source_url(request),
        derive_source_filename(request),
        derive_bronze_root_path(runtime_config),
        derive_bronze_partition_path(runtime_config, request)
    )
