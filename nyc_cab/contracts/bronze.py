"""
Bronze-layer contract constraints for NYC Cab ingestion.

This module contains stable Bronze v1 contract facts: supported source slices,
source naming rules, partition columns, and raw schema field definitions.

The module intentionally performs no I/O and imports no Spark symbols.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, TypeAlias

from nyc_cab._validation import CheckTuple, raise_on_violations
from nyc_cab.exceptions import InvalidRequestError


Period: TypeAlias = tuple[int, int]


# Supported cab types

CAB_TYPE_YELLOW: Final[str] = "yellow"

BRONZE_SUPPORTED_CAB_TYPES: Final[tuple[str, ...]] = (CAB_TYPE_YELLOW,)

# Supported periods

BRONZE_SUPPORTED_PERIODS: Final[tuple[Period, ...]] = ((2023, 1), (2023, 2))

# Data source naming

BRONZE_SOURCE_URL_BASE: Final[str] = "https://d37ci6vzurychx.cloudfront.net/trip-data/"
BRONZE_SOURCE_FILENAME_TEMPLATE: Final[str] = "{cab_type}_tripdata_{year}-{month:02d}.parquet"

BRONZE_PARTITION_COLUMNS: Final[tuple[str, ...]] = ("cab_type", "year", "month")


# No-Spark schema definition

@dataclass(frozen=True, slots=True)
class BronzeSchemaField:
    """Describe one expected raw Bronze source field.

    The ``spark_type`` value matches the output of
    ``pyspark.sql.types.DataType.simpleString()``. Examples: ``"string"``,
    ``"int"``, ``"bigint"``, ``"double"``, ``"timestamp"``, ``"timestamp_ntz"``.
    Future maintainers transcribing schemas from Spark must use
    ``simpleString()``, not ``typeName()`` or ``jsonValue()``, which produce
    different stringifications.
    """

    name: str
    spark_type: str
    nullable: bool


# ---------------------------------------------------------------------------
# Bronze v1 Yellow cab schema
#
# Transcribed from the actual yellow_tripdata_2023-01.parquet file published
# by the NYC Taxi and Limousine Commission at:
#   https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_2023-01.parquet
#
# Column names and types verified via DuckDB DESCRIBE against all 12 months
# of the 2023 Yellow cab Parquet files. All columns are nullable in the
# source files.
#
# Notes on type decisions:
#
#   - ``passenger_count`` and ``RatecodeID`` are stored as DOUBLE in the
#     2023 Parquet files, despite representing integer-valued quantities.
#     Earlier TLC vintages stored these as INT. The contract records the
#     types as they actually appear in the source, not as they logically
#     should be. Silver-layer normalization may cast these to integer.
#
#   - ``Airport_fee`` uses a capital 'A' in the January 2023 Parquet file.
#     The TLC data dictionary PDF (March 2025) lists it as ``airport_fee``
#     (lowercase), and other monthly files also use lowercase. The contract
#     records the canonical name as ``Airport_fee``; the normalization
#     layer (see ``normalize_bronze_column_name``) handles casing and
#     underscore variations transparently.
#
#   - Timestamp columns use ``timestamp_ntz`` (timezone-naive microsecond
#     timestamps). This is the ``simpleString()`` output when PySpark reads
#     INT64 TIMESTAMP columns with ``spark.sql.parquet.inferTimestampNTZ``
#     enabled (the default since PySpark 3.4). If the platform runs on an
#     older PySpark version or disables this config, these columns will
#     read as ``timestamp`` instead. The type-family compatibility layer
#     (see ``BRONZE_TYPE_FAMILIES``) treats ``timestamp`` and
#     ``timestamp_ntz`` as equivalent at the Bronze level.
# ---------------------------------------------------------------------------

BRONZE_RAW_YELLOW_SCHEMA_FIELDS: Final[tuple[BronzeSchemaField, ...]] = (
    BronzeSchemaField("VendorID", "bigint", True),
    BronzeSchemaField("tpep_pickup_datetime", "timestamp_ntz", True),
    BronzeSchemaField("tpep_dropoff_datetime", "timestamp_ntz", True),
    BronzeSchemaField("passenger_count", "double", True),
    BronzeSchemaField("trip_distance", "double", True),
    BronzeSchemaField("RatecodeID", "double", True),
    BronzeSchemaField("store_and_fwd_flag", "string", True),
    BronzeSchemaField("PULocationID", "bigint", True),
    BronzeSchemaField("DOLocationID", "bigint", True),
    BronzeSchemaField("payment_type", "bigint", True),
    BronzeSchemaField("fare_amount", "double", True),
    BronzeSchemaField("extra", "double", True),
    BronzeSchemaField("mta_tax", "double", True),
    BronzeSchemaField("tip_amount", "double", True),
    BronzeSchemaField("tolls_amount", "double", True),
    BronzeSchemaField("improvement_surcharge", "double", True),
    BronzeSchemaField("total_amount", "double", True),
    BronzeSchemaField("congestion_surcharge", "double", True),
    BronzeSchemaField("Airport_fee", "double", True),
)


# Bronze schema equivalence
#
# TLC source files are not schema-stable across months. Column names may
# differ in casing or underscore placement (``Airport_fee`` vs
# ``airport_fee``), and types may shift between compatible families
# (``timestamp`` vs ``timestamp_ntz``, ``int`` vs ``bigint``).
#
# The equivalence layer below allows the Bronze contract to match flexibly
# on name and type while still catching genuinely wrong columns.


def normalize_bronze_column_name(name: str) -> str:
    """Return the normalized key used for Bronze schema equivalence."""
    return name.strip().replace("_", "").lower()


# This is a looser equivalence of {int, float} -> numeric than originally spec'd.
# The source data is inconsistent month-to-month about exact numeric types and doesn't
# distinguish well between them.


BRONZE_TYPE_FAMILIES: Final[Mapping[str, str]] = {
    "byte": "numeric",
    "short": "numeric",
    "int": "numeric",
    "bigint": "numeric",

    "float": "numeric",
    "double": "numeric",

    "string": "string",
    "varchar": "string",
    "char": "string",

    "timestamp": "timestamp",
    "timestamp_ntz": "timestamp",
}


def bronze_types_are_compatible(expected: str, observed: str) -> bool:
    """Return whether two Spark simpleString types are Bronze-compatible.

    Two types are compatible if they are identical or belong to the same
    type family. Two types that are both unknown to the family map are
    NOT considered compatible.
    """
    if expected == observed:
        return True
    expected_family = BRONZE_TYPE_FAMILIES.get(expected)
    observed_family = BRONZE_TYPE_FAMILIES.get(observed)
    return expected_family is not None and expected_family == observed_family


def get_bronze_schema_rename_plan(
    cab_type: str,
    observed_column_names: Sequence[str],
) -> tuple[tuple[str, str], ...]:
    """Return observed-to-canonical column rename pairs.

    The pair shape is ``(observed_name, canonical_name)``.
    Only differing names are returned.
    """
    expected_fields = get_bronze_raw_schema_fields(cab_type)

    expected_by_key = {
        normalize_bronze_column_name(field.name): field.name
        for field in expected_fields
    }

    observed_by_key: dict[str, str] = {}
    duplicates: list[tuple[str, object]] = []

    for observed_name in observed_column_names:
        key = normalize_bronze_column_name(observed_name)
        if key in observed_by_key:
            duplicates.append((f"{observed_name}.normalized_duplicate", key))
        observed_by_key[key] = observed_name

    if duplicates:
        raise InvalidRequestError(
            "Bronze observed column list contains normalized duplicate names",
            violations=duplicates,
        )

    rename_pairs: list[tuple[str, str]] = []
    for key, canonical_name in expected_by_key.items():
        observed_name = observed_by_key.get(key)
        if observed_name is not None and observed_name != canonical_name:
            rename_pairs.append((observed_name, canonical_name))

    return tuple(rename_pairs)


# Transformation helpers

def derive_period_id(year: int, month: int) -> str:
    """Transform a year and month into a canonical YYYY-MM period identifier."""
    return f"{year:04d}-{month:02d}"


def derive_bronze_source_filename(cab_type: str, year: int, month: int) -> str:
    """Return the canonical raw source filename for a Bronze slice."""
    return BRONZE_SOURCE_FILENAME_TEMPLATE.format(cab_type=cab_type, year=year, month=month)


def derive_bronze_source_url(cab_type: str, year: int, month: int) -> str:
    """Return the canonical remote source URL for a Bronze slice."""
    return BRONZE_SOURCE_URL_BASE + derive_bronze_source_filename(
        cab_type=cab_type, year=year, month=month,
    )


# Supported-slice validation

def is_supported_bronze_slice(cab_type: str, year: int, month: int) -> bool:
    """Return whether the requested Bronze slice is supported."""
    return cab_type in BRONZE_SUPPORTED_CAB_TYPES and (year, month) in BRONZE_SUPPORTED_PERIODS


def get_supported_bronze_slice_checks(cab_type: str, year: int, month: int) -> tuple[CheckTuple, ...]:
    """Return semantic support checks for a Bronze source slice."""
    return (
        (cab_type in BRONZE_SUPPORTED_CAB_TYPES, "cab_type", cab_type),
        ((year, month) in BRONZE_SUPPORTED_PERIODS, "period", derive_period_id(year, month)),
    )


def validate_supported_bronze_slice(cab_type: str, year: int, month: int) -> None:
    """Validate that a Bronze source slice is supported by the current contract."""
    raise_on_violations(
        get_supported_bronze_slice_checks(cab_type=cab_type, year=year, month=month),
        "Unsupported Bronze ingestion slice",
    )


# Schema-field accessor

def get_bronze_raw_schema_fields(cab_type: str) -> tuple[BronzeSchemaField, ...]:
    """Return the expected raw schema field definitions for a cab type."""
    if cab_type == CAB_TYPE_YELLOW:
        return BRONZE_RAW_YELLOW_SCHEMA_FIELDS
    raise InvalidRequestError(
        f"Unsupported Bronze cab type: {cab_type}",
        violations=(("cab_type", cab_type),),
    )


# Schema validation
#
# The schema check is split into two functions with different shapes:
#
#   - ``get_bronze_schema_field_checks`` emits one check tuple per expected
#     field for presence, ``spark_type``, and ``nullable``. Every violation
#     tuple has the framework's standard ``(field-name, observed-value)``
#     shape. Name matching uses ``normalize_bronze_column_name``; type
#     matching uses ``bronze_types_are_compatible``.
#
#   - ``validate_bronze_column_set`` enforces shape-of-columns invariants
#     (no duplicates, no normalized duplicates, no missing, no extras) using
#     normalized column names. These violations describe the column list
#     as a whole rather than a single field, so they raise a typed exception
#     directly with structured detail rather than producing flat tuples.
#
# Both functions share the input-coverage precondition: ``observed_column_names``,
# ``observed_types_by_name``, and ``observed_nullable_by_name`` must describe
# the same column set. Callers that violate the precondition get a ``ValueError``.


def _require_consistent_observation(
    observed_column_names: Sequence[str],
    observed_types_by_name: Mapping[str, str],
    observed_nullable_by_name: Mapping[str, bool],
) -> None:
    """Raise ValueError when the three observation arguments disagree on column coverage."""
    name_set = set(observed_column_names)
    types_set = set(observed_types_by_name)
    nullable_set = set(observed_nullable_by_name)
    if name_set != types_set or name_set != nullable_set:
        raise ValueError(
            "observed_column_names, observed_types_by_name, and "
            "observed_nullable_by_name must describe the same column set; "
            f"names={sorted(name_set)}, types={sorted(types_set)}, "
            f"nullable={sorted(nullable_set)}"
        )


def get_bronze_schema_field_checks(
    cab_type: str,
    observed_column_names: Sequence[str],
    observed_types_by_name: Mapping[str, str],
    observed_nullable_by_name: Mapping[str, bool],
) -> tuple[CheckTuple, ...]:
    """Return per-field checks for an observed Bronze source schema.

    Emits one tuple per expected field for each of presence, ``spark_type``,
    and ``nullable``. Name matching uses ``normalize_bronze_column_name``;
    type matching uses ``bronze_types_are_compatible``. Every tuple carries
    the standard ``(field-name, observed-value)`` shape and aggregates
    cleanly through :func:`raise_on_violations`. Shape-of-columns invariants
    (duplicates, extras) are not checked here; see
    :func:`validate_bronze_column_set`.
    """
    _require_consistent_observation(
        observed_column_names=observed_column_names,
        observed_types_by_name=observed_types_by_name,
        observed_nullable_by_name=observed_nullable_by_name,
    )
    expected_fields = get_bronze_raw_schema_fields(cab_type)

    observed_name_by_key = {
        normalize_bronze_column_name(name): name
        for name in observed_column_names
    }

    checks: list[CheckTuple] = []

    for field in expected_fields:
        key = normalize_bronze_column_name(field.name)
        observed_name = observed_name_by_key.get(key)
        is_present = observed_name is not None

        checks.append((is_present, field.name, "<missing>"))

        if is_present:
            observed_type = observed_types_by_name[observed_name]
            observed_nullable = observed_nullable_by_name[observed_name]

            checks.append((
                bronze_types_are_compatible(field.spark_type, observed_type),
                f"{field.name}.spark_type",
                observed_type,
            ))

            checks.append((
                observed_nullable == field.nullable,
                f"{field.name}.nullable",
                observed_nullable,
            ))

    return tuple(checks)


def validate_bronze_column_set(cab_type: str, observed_column_names: Sequence[str]) -> None:
    """Validate the shape of an observed Bronze column list.

    Uses Bronze schema equivalence rules for column names:
    case-insensitive, underscore-insensitive matching via
    ``normalize_bronze_column_name``.
    """
    expected_fields = get_bronze_raw_schema_fields(cab_type)
    expected_key_set = {
        normalize_bronze_column_name(field.name)
        for field in expected_fields
    }

    observed_name_counts = Counter(observed_column_names)
    observed_key_counts = Counter(
        normalize_bronze_column_name(name)
        for name in observed_column_names
    )

    observed_key_set = set(observed_key_counts)

    violations: list[tuple[str, object]] = []

    for column_name, count in sorted(observed_name_counts.items()):
        if count > 1:
            violations.append((f"{column_name}.duplicate_count", count))

    for key, count in sorted(observed_key_counts.items()):
        if count > 1:
            violations.append((f"{key}.normalized_duplicate_count", count))

    for missing_key in sorted(expected_key_set - observed_key_set):
        violations.append((f"{missing_key}.missing", missing_key))

    for extra_key in sorted(observed_key_set - expected_key_set):
        violations.append((f"{extra_key}.unexpected", extra_key))

    if violations:
        raise InvalidRequestError(
            "Bronze observed column list does not match contract shape",
            violations=violations,
        )


def validate_against_bronze_schema(
    cab_type: str,
    observed_column_names: Sequence[str],
    observed_types_by_name: Mapping[str, str],
    observed_nullable_by_name: Mapping[str, bool],
) -> None:
    """Validate that an observed raw Bronze source schema matches the contract.

    Runs the column-set shape check first, then the per-field check. Both
    raise :class:`InvalidRequestError` on failure.
    """
    validate_bronze_column_set(
        cab_type=cab_type,
        observed_column_names=observed_column_names,
    )
    raise_on_violations(
        get_bronze_schema_field_checks(
            cab_type=cab_type,
            observed_column_names=observed_column_names,
            observed_types_by_name=observed_types_by_name,
            observed_nullable_by_name=observed_nullable_by_name,
        ),
        "Bronze source schema validation failed",
    )
