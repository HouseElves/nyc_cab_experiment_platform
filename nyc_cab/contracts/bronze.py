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
    ``"int"``, ``"bigint"``, ``"double"``, ``"timestamp"``, ``"date"``. Future
    maintainers transcribing schemas from Spark must use ``simpleString()``,
    not ``typeName()`` or ``jsonValue()``, which produce different stringifications.
    """

    name: str
    spark_type: str
    nullable: bool


# Yellow cab schema
BRONZE_RAW_YELLOW_SCHEMA_FIELDS: Final[tuple[BronzeSchemaField, ...]] = (
    # Fill with real Yellow 2023 schema fields.
)


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
#   - ``get_bronze_schema_field_checks`` emits one check tuple per expected field for
#     presence, ``spark_type``, and ``nullable``. Every violation tuple has the
#     framework's standard ``(field-name, observed-value)`` shape.
#
#   - ``validate_bronze_column_set`` enforces shape-of-columns invariants
#     (no duplicates, no extras). These violations describe the column list
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
    and ``nullable``. Every tuple carries the standard ``(field-name,
    observed-value)`` shape and aggregates cleanly through
    :func:`raise_on_violations`. Shape-of-columns invariants (duplicates,
    extras) are not checked here; see :func:`validate_bronze_column_set`.
    """
    _require_consistent_observation(
        observed_column_names=observed_column_names,
        observed_types_by_name=observed_types_by_name,
        observed_nullable_by_name=observed_nullable_by_name,
    )
    expected_fields = get_bronze_raw_schema_fields(cab_type)
    observed_name_set = set(observed_column_names)
    checks: list[CheckTuple] = []
    for field in expected_fields:
        is_present = field.name in observed_name_set
        checks.append((is_present, field.name, "<missing>"))
        if is_present:
            checks.append((
                observed_types_by_name[field.name] == field.spark_type,
                f"{field.name}.spark_type",
                observed_types_by_name[field.name],
            ))
            checks.append((
                observed_nullable_by_name[field.name] == field.nullable,
                f"{field.name}.nullable",
                observed_nullable_by_name[field.name],
            ))
    return tuple(checks)


def validate_bronze_column_set(
    cab_type: str,
    observed_column_names: Sequence[str],
) -> None:
    """Validate the shape of an observed Bronze column list.

    Rejects duplicate column names and column names not declared in the
    contract's expected fields. Raises :class:`InvalidRequestError` with
    structured ``violations`` describing each duplicate and each extra. This
    function does not check field types or nullability; see
    :func:`get_bronze_schema_field_checks`.
    """
    expected_fields = get_bronze_raw_schema_fields(cab_type)
    expected_name_set = {field.name for field in expected_fields}
    observed_name_counts = Counter(observed_column_names)
    violations: list[tuple[str, object]] = []
    for column_name, count in sorted(observed_name_counts.items()):
        if count > 1:
            violations.append((f"{column_name}.duplicate_count", count))
    for extra_column_name in sorted(set(observed_column_names) - expected_name_set):
        violations.append((f"{extra_column_name}.unexpected", extra_column_name))
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
