"""
Define the Bronze ingestion contract and orchestration entry point.

The Bronze layer pins four unbreakable rules for ingestion:

    1. The expected raw schema.
    2. The allowed dataset identities.
    3. The routing path/partition constants when those are contract-specific.
    4. The canonical source location and filename pattern.

Module Constraints
------------------

    - The schema is explicit.
    - The schema is versioned.
    - The ingestion does not infer columns from source files.
    - The reader behavior is anchored to an unbreakable documented contract.

Class Relationships
-------------------

.. mermaid::

    classDiagram

        dataclass <|-- BronzeIngestionResult
        _Validated <|-- BronzeIngestionResult

        class BronzeIngestionResult {
            <<immutable>>
            integer row_count
            }
        BronzeIngestionResult *-- BronzeIngestionRequest : request
        BronzeIngestionResult *-- AcquiredSourceFile : source
        BronzeIngestionResult *-- Path : bronze_partition_path

"""

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from pyspark.sql import SparkSession

from nyc_cab._validation import _Validated, CheckSpec, CheckTuple
from nyc_cab.config import RuntimeConfig
from nyc_cab.ingestion.bronze_io import AcquiredSourceFile
from nyc_cab.ingestion.bronze_request import (
    BronzeIngestionConfig,
    BronzeIngestionRequest,
)


@dataclass(frozen=True)
class BronzeIngestionResult(_Validated):
    """
    Describe the result of a Bronze ingestion run.

    The result composes the original request, the source acquisition outcome,
    and the new facts produced by the run (partition path and row count).
    Every value is therefore traceable to the layer that produced it.
    """

    request: BronzeIngestionRequest
    source: AcquiredSourceFile
    bronze_partition_path: Path
    row_count: int

    _type_check_specs: ClassVar[tuple[CheckSpec, ...]] = (
        ("request", BronzeIngestionRequest),
        ("source", AcquiredSourceFile),
        ("bronze_partition_path", Path),
        ("row_count", int, bool),
    )

    def _structural_checks(self) -> tuple[CheckTuple, ...]:
        """
        Provide structural validation rules for BronzeIngestionResult instance attributes.

        Checks at this level are structural only. Bronze-specific support rules (i.e. semantic validations)
        remain the responsibility of the Bronze contract.
        """
        return (
            self.request.validity_check("request"),
            self.source.validity_check("source"),
            (not self.bronze_partition_path.exists() or self.bronze_partition_path.is_dir(), "bronze_partition_path", self.bronze_partition_path),
            (self.row_count >= 0, "row_count", self.row_count)
        )


def ingest_bronze_month(
    spark: SparkSession,
    runtime_config: RuntimeConfig,
    ingestion_config: BronzeIngestionConfig,
    request: BronzeIngestionRequest,
) -> BronzeIngestionResult:
    """Ingest one monthly source file into the Bronze layer."""
    raise NotImplementedError
