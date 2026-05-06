"""
Define typed request objects for invocation-specific ingestion inputs.

Class Relationships
-------------------

.. mermaid::

    classDiagram

        dataclass <|-- BronzeIngestionConfig

        class BronzeIngestionConfig {
            <<immutable>>
            +Self create_validated(Path source_cache_directory, integer source_cache_max_files, integer source_download_timeout_seconds)$
            +void validate(self)
            integer source_cache_max_files
            integer source_download_timeout_seconds
            }
            BronzeIngestionConfig *-- Path : source_cache_directory


        dataclass <|-- BronzeIngestionRequest

        class BronzeIngestionRequest {
            <<immutable>>
            +Self create_validated(string cab_type, integer year, integer month)$
            +void validate(self)
            string cab_type
            integer year
            integer month
            }

"""

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from nyc_cab._validation import _Validated, CheckSpec, CheckTuple
from nyc_cab.contracts.bronze import derive_period_id


@dataclass(frozen=True)
class BronzeIngestionConfig(_Validated):
    """Describe Bronze-specific execution settings."""

    source_cache_directory: Path
    source_cache_max_files: int
    source_download_timeout_seconds: int

    _type_check_specs: ClassVar[tuple[CheckSpec, ...]] = (
        ("source_cache_directory", Path),
        ("source_cache_max_files", int, bool),
        ("source_download_timeout_seconds", int, bool),
    )

    def _structural_checks(self) -> tuple[CheckTuple, ...]:
        """
        Provide structural validation rules for BronzeIngestionConfig instance attributes.

        Checks at this level are structural only. Bronze-specific support rules (i.e. semantic validations)
        remain the responsibility of the Bronze contract.
        """
        return (
            (not self.source_cache_directory.exists() or self.source_cache_directory.is_dir(), "source_cache_directory", self.source_cache_directory),
            (self.source_cache_max_files >= 0, "source_cache_max_files", self.source_cache_max_files),
            (self.source_download_timeout_seconds >= 0, "source_download_timeout_seconds", self.source_download_timeout_seconds),
        )


@dataclass(frozen=True)
class BronzeIngestionRequest(_Validated):
    """Describe one monthly Bronze ingestion request."""

    cab_type: str
    year: int
    month: int

    _type_check_specs: ClassVar[tuple[CheckSpec, ...]] = (
        ("cab_type", str),
        ("year", int, bool),
        ("month", int, bool),
    )

    def _structural_checks(self) -> tuple[CheckTuple, ...]:
        """
        Provide structural validation rules for BronzeIngestionRequest instance attributes.

        Checks at this level are structural only. Bronze-specific support rules (i.e. semantic validations)
        remain the responsibility of the Bronze contract.
        """
        return (
            (self.cab_type.strip() != "", "cab_type", self.cab_type),
            (1970 <= self.year <= 2050, "year", self.year),
            (1 <= self.month <= 12, "month", self.month),
        )

    @property
    def period_id(self) -> str:
        """Return the canonical ingestion period identifier."""
        return derive_period_id(self.year, self.month)
