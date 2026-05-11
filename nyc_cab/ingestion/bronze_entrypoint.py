"""
Bronze ingestion orchestration entry point.

This module owns the top-level ingestion function and the result type.
Contract facts, path derivation, and file acquisition live in their
respective sibling modules; see the ingestion package ``__init__`` for the
full module layout.

The ingestion flow is all-or-nothing: either the partition is written
cleanly or the previous state is preserved. Re-execution is always safe
under dynamic partition overwrite semantics.

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

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from pyspark.sql import SparkSession
from pyspark.sql.functions import lit

from nyc_cab._validation import _Validated, CheckSpec, CheckTuple
from nyc_cab.config import RuntimeConfig
from nyc_cab.contracts.bronze import (
    BRONZE_PARTITION_COLUMNS,
    get_bronze_schema_rename_plan,
    validate_against_bronze_schema,
    validate_supported_bronze_slice,
)
from nyc_cab.ingestion.bronze_io import AcquiredSourceFile, acquire_bronze_source_file
from nyc_cab.ingestion.bronze_request import (
    BronzeIngestionConfig,
    BronzeIngestionRequest,
)
from nyc_cab.ingestion.source_resolver import resolve_bronze_paths


logger = logging.getLogger(__name__)


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
            (self.row_count >= 0, "row_count", self.row_count),
        )


def _canonicalize_bronze_columns(df, cab_type: str):
    """Rename source-equivalent columns to canonical Bronze contract names."""
    rename_plan = get_bronze_schema_rename_plan(cab_type, df.columns)
    for observed_name, canonical_name in rename_plan:
        logger.info(
            "Canonicalizing Bronze column: observed=%s canonical=%s",
            observed_name,
            canonical_name,
        )
        df = df.withColumnRenamed(observed_name, canonical_name)
    return df


def ingest_bronze_month(
    spark: SparkSession,
    runtime_config: RuntimeConfig,
    ingestion_config: BronzeIngestionConfig,
    request: BronzeIngestionRequest,
) -> BronzeIngestionResult:
    """
    Ingest one monthly source file into the Bronze layer.

    This is a 5 step process that hooks into the defined API.

        1. Validate the request against the contract.
        2. Resolve paths.
        3. Acquire the source file.
        4. Read and validate against the contract schema.
        5. Write the partitioned output, count, return.

    The flow is all-or-nothing. Either the partition is written cleanly
    or the previous state is preserved. Re-execution is always safe under
    dynamic partition overwrite semantics set in ``apply_spark_config``.
    """
    # Step 1: validate the request against the Bronze v1 contract.
    # This is the Tier 3 semantic check; structural checks already fired at request construction time.
    logger.info("Validating Bronze slice: cab_type=%s period=%s", request.cab_type, request.period_id)
    validate_supported_bronze_slice(request.cab_type, request.year, request.month)

    # Step 2: resolve deterministic paths (pure, no I/O).
    paths = resolve_bronze_paths(runtime_config, request)
    logger.info("Resolved Bronze paths: source_url=%s partition=%s", paths.source_url, paths.bronze_partition_path)

    # Step 3: acquire the source file (cache-aware, may hit network).
    logger.info("Acquiring source file: filename=%s", paths.source_filename)
    source = acquire_bronze_source_file(paths.source_url, paths.source_filename, ingestion_config)
    logger.info("Acquired source file: local_path=%s cache_hit=%s", source.local_path, source.cache_hit)

    # Step 4: read the source parquet and validate against the contract schema.
    logger.info("Reading source parquet: local_path=%s", source.local_path)
    df = spark.read.parquet(str(source.local_path))

    observed_fields = df.schema.fields
    column_names = [f.name for f in observed_fields]
    types_by_name = {f.name: f.dataType.simpleString() for f in observed_fields}
    nullable_by_name = {f.name: f.nullable for f in observed_fields}
    logger.info("Source schema observed: %d columns", len(column_names))

    validate_against_bronze_schema(
        request.cab_type, column_names, types_by_name, nullable_by_name,
    )
    logger.info("Schema validated against Bronze contract: cab_type=%s", request.cab_type)

    df = _canonicalize_bronze_columns(df, request.cab_type)
    logger.info("Bronze columns canonicalized: cab_type=%s", request.cab_type)

    # Step 5: add partition columns, write, count, return.
    #
    # Dynamic partition overwrite requires both the Spark conf
    # (spark.sql.sources.partitionOverwriteMode=dynamic, set in apply_spark_config)
    # and the explicit partitionBy below. Do not remove either.
    logger.info("Writing Bronze partition: cab_type=%s year=%d month=%d", request.cab_type, request.year, request.month)

    df_partitioned = (
        df
        .withColumn("cab_type", lit(request.cab_type))
        .withColumn("year", lit(request.year))
        .withColumn("month", lit(request.month))
    )

    df_partitioned.write \
        .mode("overwrite") \
        .partitionBy(*BRONZE_PARTITION_COLUMNS) \
        .parquet(str(paths.bronze_root_path))

    row_count = df.count()
    logger.info("Wrote %d rows to %s", row_count, paths.bronze_partition_path)

    result = BronzeIngestionResult.create_validated(request, source, paths.bronze_partition_path, row_count)
    logger.info("Bronze ingestion complete: row_count=%d cache_hit=%s", row_count, source.cache_hit)
    return result
