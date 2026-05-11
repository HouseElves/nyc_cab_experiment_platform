# pylint: disable=redefined-outer-name
"""Tests for :mod:`nyc_cab.contracts.bronze`.

These tests cover the Bronze v1 contract surface: derivation helpers,
slice-support predicates and check builders, the schema-field accessor,
the schema equivalence layer (name normalization, type families, rename
plan), and the per-field and column-set schema validators.

Schema-validator tests use ``monkeypatch.setattr`` to substitute a minimal
three-field stub schema so the validator paths are exercised against known
data independent of the real schema content. A separate section at the end
verifies structural properties of the real ``BRONZE_RAW_YELLOW_SCHEMA_FIELDS``.
"""

from __future__ import annotations

import dataclasses

import pytest

from nyc_cab.contracts.bronze import (
    BRONZE_PARTITION_COLUMNS,
    BRONZE_RAW_YELLOW_SCHEMA_FIELDS,
    BRONZE_SOURCE_FILENAME_TEMPLATE,
    BRONZE_SOURCE_URL_BASE,
    BRONZE_SUPPORTED_CAB_TYPES,
    BRONZE_SUPPORTED_PERIODS,
    BRONZE_TYPE_FAMILIES,
    BronzeSchemaField,
    CAB_TYPE_YELLOW,
    bronze_types_are_compatible,
    derive_bronze_source_filename,
    derive_bronze_source_url,
    derive_period_id,
    get_bronze_raw_schema_fields,
    get_bronze_schema_field_checks,
    get_bronze_schema_rename_plan,
    get_supported_bronze_slice_checks,
    is_supported_bronze_slice,
    normalize_bronze_column_name,
    validate_against_bronze_schema,
    validate_bronze_column_set,
    validate_supported_bronze_slice,
)
from nyc_cab.exceptions import InvalidRequestError


_STUB_YELLOW_FIELDS: tuple[BronzeSchemaField, ...] = (
    BronzeSchemaField(name="VendorID", spark_type="int", nullable=True),
    BronzeSchemaField(name="trip_distance", spark_type="double", nullable=True),
    BronzeSchemaField(name="payment_type", spark_type="bigint", nullable=False),
)


# --- Module-level constants -------------------------------------------------

def test_cab_type_yellow_constant() -> None:
    """``CAB_TYPE_YELLOW`` exposes the canonical yellow identifier."""
    assert CAB_TYPE_YELLOW == "yellow"

def test_supported_cab_types_contains_yellow_only() -> None:
    """Yellow is currently the only supported cab type."""
    assert BRONZE_SUPPORTED_CAB_TYPES == (CAB_TYPE_YELLOW,)

def test_supported_periods_lists_jan_and_feb_2023() -> None:
    """The contract supports exactly January and February 2023."""
    assert BRONZE_SUPPORTED_PERIODS == ((2023, 1), (2023, 2))

def test_partition_columns_match_documented_layout() -> None:
    """Partition columns match the cab_type/year/month layout."""
    assert BRONZE_PARTITION_COLUMNS == ("cab_type", "year", "month")

def test_source_url_base_targets_cloudfront() -> None:
    """The source URL base targets the canonical TLC CloudFront host."""
    assert BRONZE_SOURCE_URL_BASE.startswith("https://")
    assert "cloudfront.net" in BRONZE_SOURCE_URL_BASE

def test_source_filename_template_uses_expected_placeholders() -> None:
    """The filename template contains the cab_type, year, and month placeholders."""
    assert "{cab_type}" in BRONZE_SOURCE_FILENAME_TEMPLATE
    assert "{year}" in BRONZE_SOURCE_FILENAME_TEMPLATE
    assert "{month:02d}" in BRONZE_SOURCE_FILENAME_TEMPLATE


# --- BronzeSchemaField ------------------------------------------------------

def test_bronze_schema_field_is_frozen() -> None:
    """``BronzeSchemaField`` instances reject attribute mutation."""
    field = BronzeSchemaField(name="x", spark_type="int", nullable=True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        field.name = "y"  # type: ignore[misc]

def test_bronze_schema_field_stores_all_three_attributes() -> None:
    """The dataclass exposes its three constructor arguments by name."""
    field = BronzeSchemaField(name="trip_distance", spark_type="double", nullable=True)
    assert field.name == "trip_distance"
    assert field.spark_type == "double"
    assert field.nullable is True


# --- Transformation helpers -------------------------------------------------

def test_derive_period_id_zero_pads_single_digit_month() -> None:
    """Single-digit months are zero-padded to two characters."""
    assert derive_period_id(2023, 1) == "2023-01"

def test_derive_period_id_handles_two_digit_month() -> None:
    """Two-digit months pass through unchanged."""
    assert derive_period_id(2023, 12) == "2023-12"

def test_derive_bronze_source_filename_uses_template() -> None:
    """The filename derivation produces the canonical TLC filename."""
    assert derive_bronze_source_filename("yellow", 2023, 1) == "yellow_tripdata_2023-01.parquet"

def test_derive_bronze_source_filename_zero_pads_month() -> None:
    """The filename derivation zero-pads single-digit months."""
    assert derive_bronze_source_filename("yellow", 2023, 7) == "yellow_tripdata_2023-07.parquet"

def test_derive_bronze_source_url_concatenates_base_and_filename() -> None:
    """The URL derivation combines the base URL and the canonical filename."""
    expected = "https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_2023-02.parquet"
    assert derive_bronze_source_url("yellow", 2023, 2) == expected


# --- is_supported_bronze_slice ----------------------------------------------

def test_is_supported_bronze_slice_accepts_yellow_jan_2023() -> None:
    """Yellow January 2023 is a supported slice."""
    assert is_supported_bronze_slice("yellow", 2023, 1) is True

def test_is_supported_bronze_slice_accepts_yellow_feb_2023() -> None:
    """Yellow February 2023 is a supported slice."""
    assert is_supported_bronze_slice("yellow", 2023, 2) is True

def test_is_supported_bronze_slice_rejects_unsupported_cab() -> None:
    """An unsupported cab type returns ``False``."""
    assert is_supported_bronze_slice("green", 2023, 1) is False

def test_is_supported_bronze_slice_rejects_unsupported_period() -> None:
    """An unsupported period returns ``False``."""
    assert is_supported_bronze_slice("yellow", 2023, 3) is False

def test_is_supported_bronze_slice_rejects_when_both_components_unsupported() -> None:
    """Both unsupported cab and period yield ``False``."""
    assert is_supported_bronze_slice("green", 2024, 6) is False


# --- get_supported_bronze_slice_checks --------------------------------------

def test_get_supported_bronze_slice_checks_all_pass_for_supported_slice() -> None:
    """Every check passes for a supported slice."""
    checks = get_supported_bronze_slice_checks("yellow", 2023, 1)
    assert all(passed for (passed, _, _) in checks)

def test_get_supported_bronze_slice_checks_flags_unsupported_cab() -> None:
    """An unsupported cab type produces a single failing check."""
    checks = get_supported_bronze_slice_checks("green", 2023, 1)
    failed = [(name, value) for (passed, name, value) in checks if not passed]
    assert failed == [("cab_type", "green")]

def test_get_supported_bronze_slice_checks_flags_unsupported_period() -> None:
    """An unsupported period produces a single failing check."""
    checks = get_supported_bronze_slice_checks("yellow", 2023, 3)
    failed = [(name, value) for (passed, name, value) in checks if not passed]
    assert failed == [("period", "2023-03")]

def test_get_supported_bronze_slice_checks_flags_both_when_both_fail() -> None:
    """Two unsupported components produce two failing checks."""
    checks = get_supported_bronze_slice_checks("green", 2023, 3)
    failed = [(name, value) for (passed, name, value) in checks if not passed]
    assert failed == [("cab_type", "green"), ("period", "2023-03")]


# --- validate_supported_bronze_slice ----------------------------------------

def test_validate_supported_bronze_slice_silent_for_supported() -> None:
    """The validator returns silently for a supported slice."""
    validate_supported_bronze_slice("yellow", 2023, 1)

def test_validate_supported_bronze_slice_raises_for_unsupported() -> None:
    """The validator raises ``InvalidRequestError`` for an unsupported slice."""
    with pytest.raises(InvalidRequestError) as info:
        validate_supported_bronze_slice("green", 2023, 3)
    names = [v[0] for v in info.value.violations]
    assert names == ["cab_type", "period"]


# --- get_bronze_raw_schema_fields -------------------------------------------

def test_get_bronze_raw_schema_fields_returns_yellow_fields_for_yellow() -> None:
    """The accessor returns the yellow schema for the yellow cab type."""
    fields = get_bronze_raw_schema_fields("yellow")
    assert isinstance(fields, tuple)
    assert len(fields) > 0

def test_get_bronze_raw_schema_fields_raises_for_unknown_cab() -> None:
    """Unknown cab types produce a typed ``InvalidRequestError``."""
    with pytest.raises(InvalidRequestError) as info:
        get_bronze_raw_schema_fields("magenta")
    assert info.value.violations == (("cab_type", "magenta"),)


# --- normalize_bronze_column_name -------------------------------------------

def test_normalize_lowercases() -> None:
    """Mixed-case names are lowercased."""
    assert normalize_bronze_column_name("VendorID") == "vendorid"

def test_normalize_strips_underscores() -> None:
    """Underscores are removed."""
    assert normalize_bronze_column_name("Airport_fee") == "airportfee"

def test_normalize_strips_whitespace() -> None:
    """Leading and trailing whitespace is stripped."""
    assert normalize_bronze_column_name("  trip_distance  ") == "tripdistance"

def test_normalize_makes_case_variants_equivalent() -> None:
    """Different casings of the same name normalize to the same key."""
    assert normalize_bronze_column_name("Airport_fee") == normalize_bronze_column_name("airport_fee")
    assert normalize_bronze_column_name("VendorID") == normalize_bronze_column_name("vendorid")


# --- BRONZE_TYPE_FAMILIES ---------------------------------------------------

def test_type_families_group_numerics() -> None:
    """All integer and floating types map to 'numeric'."""
    assert BRONZE_TYPE_FAMILIES["byte"] == "numeric"
    assert BRONZE_TYPE_FAMILIES["bigint"] == "numeric"
    assert BRONZE_TYPE_FAMILIES["short"] == "numeric"
    assert BRONZE_TYPE_FAMILIES["int"] == "numeric"
    assert BRONZE_TYPE_FAMILIES["float"] == "numeric"
    assert BRONZE_TYPE_FAMILIES["double"] == "numeric"


def test_type_families_group_timestamps() -> None:
    """Timezone-aware and timezone-naive timestamps map to the same family."""
    assert BRONZE_TYPE_FAMILIES["timestamp"] == BRONZE_TYPE_FAMILIES["timestamp_ntz"]


# --- bronze_types_are_compatible --------------------------------------------

def test_compatible_exact_match() -> None:
    """Identical types are compatible."""
    assert bronze_types_are_compatible("bigint", "bigint") is True

def test_compatible_same_family() -> None:
    """Types in the same family are compatible."""
    assert bronze_types_are_compatible("int", "bigint") is True
    assert bronze_types_are_compatible("timestamp", "timestamp_ntz") is True
    assert bronze_types_are_compatible("float", "double") is True

def test_compatible_rejects_different_families() -> None:
    """Types in different families are not compatible."""
    assert bronze_types_are_compatible("double", "string") is False
    assert bronze_types_are_compatible("bigint", "string") is False
    assert bronze_types_are_compatible("string", "int") is False

def test_compatible_rejects_unknown_types() -> None:
    """Two types not in the family map are not considered compatible."""
    assert bronze_types_are_compatible("decimal", "binary") is False

def test_compatible_rejects_known_vs_unknown() -> None:
    """A known type is not compatible with an unknown type."""
    assert bronze_types_are_compatible("bigint", "decimal") is False
    assert bronze_types_are_compatible("decimal", "bigint") is False


# --- get_bronze_schema_rename_plan ------------------------------------------

def test_rename_plan_empty_when_names_match(monkeypatch) -> None:
    """No renames needed when observed names match canonical names exactly."""
    monkeypatch.setattr("nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS", _STUB_YELLOW_FIELDS)
    plan = get_bronze_schema_rename_plan("yellow", ["VendorID", "trip_distance", "payment_type"])
    assert plan == ()

def test_rename_plan_produces_case_rename(monkeypatch) -> None:
    """A case-different observed name produces a rename pair."""
    monkeypatch.setattr("nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS", _STUB_YELLOW_FIELDS)
    plan = get_bronze_schema_rename_plan("yellow", ["vendorid", "trip_distance", "payment_type"])
    assert ("vendorid", "VendorID") in plan

def test_rename_plan_produces_underscore_rename(monkeypatch) -> None:
    """An underscore-different observed name produces a rename pair."""
    stub = (BronzeSchemaField("Airport_fee", "double", True),)
    monkeypatch.setattr("nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS", stub)
    plan = get_bronze_schema_rename_plan("yellow", ["airport_fee"])
    assert ("airport_fee", "Airport_fee") in plan

def test_rename_plan_ignores_unmatched_observed_columns(monkeypatch) -> None:
    """Observed columns not in the contract produce no renames."""
    monkeypatch.setattr("nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS", _STUB_YELLOW_FIELDS)
    plan = get_bronze_schema_rename_plan("yellow", ["VendorID", "trip_distance", "payment_type", "extra_col"])
    assert len(plan) == 0

def test_rename_plan_raises_on_normalized_duplicates(monkeypatch) -> None:
    """Two observed columns normalizing to the same key raise an error."""
    monkeypatch.setattr("nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS", _STUB_YELLOW_FIELDS)
    with pytest.raises(InvalidRequestError) as info:
        get_bronze_schema_rename_plan("yellow", ["VendorID", "vendorid", "trip_distance"])
    names = [v[0] for v in info.value.violations]
    assert "vendorid.normalized_duplicate" in names


# --- get_bronze_schema_field_checks (monkeypatched) -------------------------

def test_get_bronze_schema_field_checks_happy_path(monkeypatch) -> None:
    """All checks pass when observation matches the contract exactly."""
    monkeypatch.setattr("nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS", _STUB_YELLOW_FIELDS)
    checks = get_bronze_schema_field_checks(
        "yellow",
        ["VendorID", "trip_distance", "payment_type"],
        {"VendorID": "int", "trip_distance": "double", "payment_type": "bigint"},
        {"VendorID": True, "trip_distance": True, "payment_type": False},
    )
    assert all(passed for (passed, _, _) in checks)

def test_get_bronze_schema_field_checks_passes_with_case_variant_names(monkeypatch) -> None:
    """Observed names that differ only in case still pass presence checks."""
    monkeypatch.setattr("nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS", _STUB_YELLOW_FIELDS)
    checks = get_bronze_schema_field_checks(
        "yellow",
        ["vendorid", "trip_distance", "payment_type"],
        {"vendorid": "int", "trip_distance": "double", "payment_type": "bigint"},
        {"vendorid": True, "trip_distance": True, "payment_type": False},
    )
    assert all(passed for (passed, _, _) in checks)

def test_get_bronze_schema_field_checks_passes_with_compatible_types(monkeypatch) -> None:
    """Types in the same family pass the type-compatibility check."""
    monkeypatch.setattr("nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS", _STUB_YELLOW_FIELDS)
    checks = get_bronze_schema_field_checks(
        "yellow",
        ["VendorID", "trip_distance", "payment_type"],
        {"VendorID": "bigint", "trip_distance": "double", "payment_type": "bigint"},
        {"VendorID": True, "trip_distance": True, "payment_type": False},
    )
    assert all(passed for (passed, _, _) in checks)

def test_get_bronze_schema_field_checks_flags_missing_field_with_marker(monkeypatch) -> None:
    """A missing expected field surfaces the ``<missing>`` marker as the value."""
    monkeypatch.setattr("nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS", _STUB_YELLOW_FIELDS)
    checks = get_bronze_schema_field_checks(
        "yellow",
        ["VendorID", "trip_distance"],
        {"VendorID": "int", "trip_distance": "double"},
        {"VendorID": True, "trip_distance": True},
    )
    failed = [(name, value) for (passed, name, value) in checks if not passed]
    assert ("payment_type", "<missing>") in failed

def test_get_bronze_schema_field_checks_skips_subchecks_for_missing_fields(monkeypatch) -> None:
    """When a field is missing, no spark_type or nullable subcheck is emitted for it."""
    monkeypatch.setattr("nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS", _STUB_YELLOW_FIELDS)
    checks = get_bronze_schema_field_checks(
        "yellow",
        ["VendorID", "trip_distance"],
        {"VendorID": "int", "trip_distance": "double"},
        {"VendorID": True, "trip_distance": True},
    )
    names = [name for (_, name, _) in checks]
    assert "payment_type.spark_type" not in names
    assert "payment_type.nullable" not in names

def test_get_bronze_schema_field_checks_flags_incompatible_type(monkeypatch) -> None:
    """A type in a different family produces a ``<field>.spark_type`` violation."""
    monkeypatch.setattr("nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS", _STUB_YELLOW_FIELDS)
    checks = get_bronze_schema_field_checks(
        "yellow",
        ["VendorID", "trip_distance", "payment_type"],
        {"VendorID": "int", "trip_distance": "string", "payment_type": "bigint"},
        {"VendorID": True, "trip_distance": True, "payment_type": False},
    )
    failed = [(name, value) for (passed, name, value) in checks if not passed]
    assert ("trip_distance.spark_type", "string") in failed

def test_get_bronze_schema_field_checks_flags_wrong_nullable(monkeypatch) -> None:
    """A nullability mismatch produces a ``<field>.nullable`` violation."""
    monkeypatch.setattr("nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS", _STUB_YELLOW_FIELDS)
    checks = get_bronze_schema_field_checks(
        "yellow",
        ["VendorID", "trip_distance", "payment_type"],
        {"VendorID": "int", "trip_distance": "double", "payment_type": "bigint"},
        {"VendorID": True, "trip_distance": True, "payment_type": True},
    )
    failed = [(name, value) for (passed, name, value) in checks if not passed]
    assert ("payment_type.nullable", True) in failed

def test_get_bronze_schema_field_checks_raises_on_inconsistent_observation(monkeypatch) -> None:
    """Three observation arguments describing different column sets raise ``ValueError``."""
    monkeypatch.setattr("nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS", _STUB_YELLOW_FIELDS)
    with pytest.raises(ValueError):
        get_bronze_schema_field_checks(
            "yellow",
            ["VendorID", "trip_distance"],
            {"VendorID": "int"},
            {"VendorID": True, "trip_distance": True},
        )


# --- validate_bronze_column_set (monkeypatched) -----------------------------

def test_validate_bronze_column_set_silent_for_correct_columns(monkeypatch) -> None:
    """The validator returns silently when the column list matches the contract."""
    monkeypatch.setattr("nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS", _STUB_YELLOW_FIELDS)
    validate_bronze_column_set("yellow", ["VendorID", "trip_distance", "payment_type"])

def test_validate_bronze_column_set_silent_for_case_variant_columns(monkeypatch) -> None:
    """Column names that differ only in case pass the normalized check."""
    monkeypatch.setattr("nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS", _STUB_YELLOW_FIELDS)
    validate_bronze_column_set("yellow", ["vendorid", "trip_distance", "payment_type"])

def test_validate_bronze_column_set_silent_when_column_list_is_empty(monkeypatch) -> None:
    """An empty column list against an empty schema produces no violations."""
    monkeypatch.setattr("nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS", ())
    validate_bronze_column_set("yellow", [])

def test_validate_bronze_column_set_flags_duplicates(monkeypatch) -> None:
    """A duplicate column produces a ``<column>.duplicate_count`` violation."""
    monkeypatch.setattr("nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS", _STUB_YELLOW_FIELDS)
    with pytest.raises(InvalidRequestError) as info:
        validate_bronze_column_set("yellow", ["VendorID", "trip_distance", "trip_distance", "payment_type"])
    names = [v[0] for v in info.value.violations]
    assert "trip_distance.duplicate_count" in names

def test_validate_bronze_column_set_flags_normalized_duplicates(monkeypatch) -> None:
    """Two columns that normalize to the same key produce a normalized duplicate violation."""
    stub = (BronzeSchemaField("Airport_fee", "double", True),)
    monkeypatch.setattr("nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS", stub)
    with pytest.raises(InvalidRequestError) as info:
        validate_bronze_column_set("yellow", ["Airport_fee", "airport_fee"])
    names = [v[0] for v in info.value.violations]
    assert "airportfee.normalized_duplicate_count" in names

def test_validate_bronze_column_set_flags_extras(monkeypatch) -> None:
    """An unexpected extra column produces an ``<key>.unexpected`` violation."""
    monkeypatch.setattr("nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS", _STUB_YELLOW_FIELDS)
    with pytest.raises(InvalidRequestError) as info:
        validate_bronze_column_set("yellow", ["VendorID", "trip_distance", "payment_type", "rogue_field"])
    names = [v[0] for v in info.value.violations]
    assert "roguefield.unexpected" in names

def test_validate_bronze_column_set_flags_missing(monkeypatch) -> None:
    """A missing expected column produces a ``<key>.missing`` violation."""
    monkeypatch.setattr("nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS", _STUB_YELLOW_FIELDS)
    with pytest.raises(InvalidRequestError) as info:
        validate_bronze_column_set("yellow", ["VendorID", "trip_distance"])
    names = [v[0] for v in info.value.violations]
    assert "paymenttype.missing" in names

def test_validate_bronze_column_set_aggregates_duplicates_and_extras(monkeypatch) -> None:
    """Duplicates and extras both surface in the same exception."""
    monkeypatch.setattr("nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS", _STUB_YELLOW_FIELDS)
    with pytest.raises(InvalidRequestError) as info:
        validate_bronze_column_set("yellow", ["VendorID", "VendorID", "trip_distance", "payment_type", "rogue"])
    names = [v[0] for v in info.value.violations]
    assert "VendorID.duplicate_count" in names
    assert "rogue.unexpected" in names


# --- validate_against_bronze_schema (monkeypatched) -------------------------

def test_validate_against_bronze_schema_silent_for_matching_observation(monkeypatch) -> None:
    """The combined validator returns silently for a fully-matching observation."""
    monkeypatch.setattr("nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS", _STUB_YELLOW_FIELDS)
    validate_against_bronze_schema(
        "yellow",
        ["VendorID", "trip_distance", "payment_type"],
        {"VendorID": "int", "trip_distance": "double", "payment_type": "bigint"},
        {"VendorID": True, "trip_distance": True, "payment_type": False},
    )

def test_validate_against_bronze_schema_raises_on_column_set_failure(monkeypatch) -> None:
    """Column-set failures raise from the column-set check before per-field checks run."""
    monkeypatch.setattr("nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS", _STUB_YELLOW_FIELDS)
    with pytest.raises(InvalidRequestError):
        validate_against_bronze_schema(
            "yellow",
            ["VendorID", "trip_distance", "payment_type", "rogue"],
            {"VendorID": "int", "trip_distance": "double", "payment_type": "bigint", "rogue": "string"},
            {"VendorID": True, "trip_distance": True, "payment_type": False, "rogue": True},
        )

def test_validate_against_bronze_schema_raises_on_field_failure(monkeypatch) -> None:
    """Per-field failures raise after the column-set check passes."""
    monkeypatch.setattr("nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS", _STUB_YELLOW_FIELDS)
    with pytest.raises(InvalidRequestError) as info:
        validate_against_bronze_schema(
            "yellow",
            ["VendorID", "trip_distance", "payment_type"],
            {"VendorID": "string", "trip_distance": "double", "payment_type": "bigint"},
            {"VendorID": True, "trip_distance": True, "payment_type": False},
        )
    assert ("VendorID.spark_type", "string") in info.value.violations


# --- Real schema verification -----------------------------------------------

def test_real_schema_has_19_fields() -> None:
    """The Bronze v1 Yellow cab schema has exactly 19 fields."""
    assert len(BRONZE_RAW_YELLOW_SCHEMA_FIELDS) == 19

def test_real_schema_field_names_match_tlc_columns() -> None:
    """The field names match the NYC TLC Yellow cab Parquet columns."""
    expected_names = [
        "VendorID", "tpep_pickup_datetime", "tpep_dropoff_datetime",
        "passenger_count", "trip_distance", "RatecodeID", "store_and_fwd_flag",
        "PULocationID", "DOLocationID", "payment_type", "fare_amount", "extra",
        "mta_tax", "tip_amount", "tolls_amount", "improvement_surcharge",
        "total_amount", "congestion_surcharge", "Airport_fee",
    ]
    actual_names = [f.name for f in BRONZE_RAW_YELLOW_SCHEMA_FIELDS]
    assert actual_names == expected_names

def test_real_schema_airport_fee_uses_capital_a() -> None:
    """The airport fee column uses 'Airport_fee' (capital A) as the canonical name."""
    airport = [f for f in BRONZE_RAW_YELLOW_SCHEMA_FIELDS if "airport" in f.name.lower()]
    assert len(airport) == 1
    assert airport[0].name == "Airport_fee"

def test_real_schema_timestamps_use_timestamp_ntz() -> None:
    """Datetime columns use ``timestamp_ntz`` matching PySpark 3.4+ defaults."""
    ts_fields = [f for f in BRONZE_RAW_YELLOW_SCHEMA_FIELDS if "datetime" in f.name]
    assert len(ts_fields) == 2
    for field in ts_fields:
        assert field.spark_type == "timestamp_ntz"

def test_real_schema_all_fields_are_nullable() -> None:
    """All fields in the TLC Yellow cab Parquet files are nullable."""
    for field in BRONZE_RAW_YELLOW_SCHEMA_FIELDS:
        assert field.nullable is True, f"{field.name} should be nullable"

def test_real_schema_self_validates() -> None:
    """The real schema passes its own validators when used as the observation."""
    names = [f.name for f in BRONZE_RAW_YELLOW_SCHEMA_FIELDS]
    types = {f.name: f.spark_type for f in BRONZE_RAW_YELLOW_SCHEMA_FIELDS}
    nullable = {f.name: f.nullable for f in BRONZE_RAW_YELLOW_SCHEMA_FIELDS}
    validate_against_bronze_schema("yellow", names, types, nullable)
