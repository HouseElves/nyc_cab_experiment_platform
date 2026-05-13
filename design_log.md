# Design Log

This document records architectural decisions for the NYC Cab Experiment
Platform. These notes explain the rationale behind core module boundaries and
configuration strategies.

## Decision Summary

### 1. Centralized Platform Constants

The platform uses a single constants module for stable, low-churn symbols such
as package identity, environment-variable naming conventions, medallion layer
identifiers, and platform-wide defaults.

#### Rationale

Early in the project, centralized constants avoid premature fragmentation. A
unified module provides a single source of truth for platform-stable
identifiers and simplifies discovery. This module is restricted to pure
constant definitions; it contains no logic, I/O, or internal platform imports.

### 2. Shallow Exception Hierarchy

The platform employs a shallow, YAGNI-compliant exception hierarchy:

- `NYCCabError` as the package-level base exception
- `ConfigurationError` as the category for runtime configuration failures
- `MissingConfigError` and `InvalidConfigError` as concrete configuration
  failure types
- `ValidationError` as the category for typed object validation failures
- `InvalidRequestError` as the concrete failure type for invalid typed request
  data

#### Rationale

This hierarchy provides the necessary granularity for operational error
handling, such as distinguishing between a missing variable, a malformed
configuration value, and an invalid typed request object, without speculative
branching. Concrete exceptions include structured metadata such as
`variable`, `value`, or `violations` to support precise CLI and orchestration
diagnostics.

### 3. Principled Configuration Factoring

The platform separates configuration into distinct layers based on the
lifecycle and stability of the data:

- `RuntimeConfig` in `nyc_cab/config.py` owns stable, application-wide runtime
  context such as deployment environment, filesystem layout, and
  application-wide log level
- `SparkConfig` in `nyc_cab/spark_config.py` owns engine-specific execution
  settings such as Spark master and application name
- `BronzeIngestionConfig` will own Bronze-specific execution settings such as
  source cache behavior and download timeouts
- `BronzeIngestionRequest` will own invocation-specific request parameters such
  as cab type and time period

#### Rationale

This factoring prevents the creation of a kitchen-sink configuration object. By
decoupling runtime context from execution-engine concerns and run-specific
request parameters, the platform avoids turning a single module into the
default destination for future subsystem growth.

### 4. Explicit and Deterministic Configuration Loading

Configuration loaders require an explicit mapping input and fall back to
`os.environ` only when no mapping is provided.

#### Rationale

This promotes deterministic behavior and direct testability. The platform does
not perform implicit configuration discovery such as automatic `.env` loading.
Workflows must be explicit about the configuration they operate under.

### 5. Syntax-Only Configuration Validation

Configuration loaders validate the presence, shape, and syntax of values, such
as type, range, and format, but intentionally avoid external-state validation.

#### Rationale

Validation of external state is a runtime operational concern, not a
configuration concern. By keeping the configuration layer pure, the platform
avoids hidden side effects and keeps configuration objects safe to instantiate
during static analysis, testing, and orchestration dry runs.

### 6. Local-First Development

The platform defaults to a local environment where the filesystem layout is
rooted at `./data`. In all other environments, explicit filesystem
configuration is mandatory.

#### Rationale

This supports a low-friction local developer workflow while enforcing explicit
operational discipline in development and production deployments.

### 7. Immutable Configuration Objects

Configuration is represented using frozen dataclasses and `pathlib.Path`
objects.

#### Rationale

Immutability ensures that configuration cannot be altered as it passes through
the system. `Path` objects provide a robust typed interface for filesystem
manipulation and avoid the risks associated with stringly typed paths.

### 8. Shared Environment Lookup Primitives

The platform centralizes low-level environment-mapping behavior in the private
helper module `nyc_cab._env`.

This module owns only the shared primitives for:

- required value lookup
- optional value lookup
- consistent handling of blank and whitespace-only values

#### Rationale

This helper ensures that runtime and Spark configuration loading share one
exact interpretation of missing and optional inputs. The helper remains
intentionally narrow. It does not assemble higher-level configuration objects
and does not replace module-specific validation logic.

### 9. Bronze Ingestion Uses a Focused Four-Module Split

Bronze ingestion is intentionally split across four focused modules within
`nyc_cab.ingestion`:

- `bronze_request.py` owns typed Bronze request and Bronze-specific config
  dataclasses
- `source_resolver.py` owns deterministic URL, filename, and target path
  derivation
- `bronze_io.py` owns cache-aware source-file acquisition
- `bronze_entrypoint.py` owns the top-level ingestion orchestration entry
  point and the Bronze ingestion result type

Spark session creation remains outside the ingestion package in
`nyc_cab.orchestration.spark`. Bronze contract concerns also remain outside
the ingestion package; see decisions 10 and 17.

#### Rationale

This split keeps request modeling, pure path derivation, file acquisition, and
Spark-driven orchestration isolated from one another. The result is narrower
module responsibility, lower test setup cost, and a thin external execution
surface for CLI and Airflow orchestration. The `_entrypoint` suffix on the
orchestration module disambiguates it from `contracts/bronze.py`, which owns
contract-level Bronze facts and would otherwise share the same basename.

### 10. Bronze Contract Concerns Remain Local Until They Gain Independent Weight

> **Status:** superseded by decision 17.

Bronze v1 keeps contract concerns local to `nyc_cab.ingestion.bronze` rather
than introducing a separate `contracts` package immediately.

These local concerns include:

- supported cab types
- supported Bronze periods
- the canonical source URL base
- the canonical source filename template
- the explicit raw input schema

#### Rationale

A separate contracts package is likely to become useful later, especially when
schema reuse, contract versioning, or multiple dataset families introduce real
independent substance. At the current stage, extracting a dedicated contracts
package would create a ceremonial namespace without sufficient architectural
weight behind it. The platform therefore defers that factoring until the
abstraction earns its existence.

### 11. Bronze Requests and Bronze Configs Remain Explicitly Passed, Not Global

The platform does not use Singleton configuration objects for Bronze ingestion
or for runtime configuration more generally. Typed config and request objects
remain explicit function inputs.

#### Rationale

Explicit parameter passing preserves determinism, testability, and orchestration
clarity. A Singleton would introduce hidden global state, weaken test isolation,
and blur the boundary between configuration composition at the edge and
application logic in the package. If repeated function signatures later create
real friction, the platform may introduce a narrow context object, but only in
response to demonstrated need.

### 12. Validation Is Split Between Type Construction and Structural Checks

Typed request/config dataclasses follow a two-stage validation model:

- `create_validated(...)` acts as the safe constructor used by orchestration
  code and validates runtime input types before returning an instance
- `validate()` checks structural correctness of an already-constructed typed
  object and raises a typed validation exception on failure
- the raw dataclass constructor remains available for tests that need to create
  intentionally invalid instances

For Bronze requests specifically, structural validation is distinct from
Bronze-contract support policy. For example, a request can be structurally valid
while still being unsupported by Bronze v1.

#### Rationale

This split keeps type enforcement, structural validation, and subsystem policy
validation conceptually separate. It reduces ambiguity in test design and
prevents request/config dataclasses from turning into best-effort parsers.

### 13. Raw Input Parsing and Typed Object Validation Are Separate Concerns

Typed dataclass factories are validators, not coercion engines. They expect
correct runtime types at their boundary. Parsing or coercion of raw external
inputs belongs to edge adapters such as CLI parsing, environment loaders, or
future Airflow parameter adapters.

#### Rationale

This preserves a clean distinction between untyped external data and typed
application objects. Broad coercion inside typed object factories would blur
responsibility, complicate tests, and make accepted input semantics harder to
reason about. The platform therefore prefers explicit parsing at the edge and
strict type validation at typed-object construction time.

### 14. Shared Validation Boilerplate Is Centralized in a Private Helper

> **Status:** extended by decision 18.

The platform centralizes repeated request/config validation boilerplate in a
private helper module, `nyc_cab._validation`.

This helper owns narrow shared behavior for:

- evaluating a sequence of validation checks
- collecting failing variable/value pairs as violations
- raising `InvalidRequestError` when violations are present

The current helper naming convention is `raise_on_violations(...)`.

#### Rationale

This removes repeated list-comprehension and exception-raising boilerplate from
typed dataclasses without introducing a generalized validation framework. The
helper remains intentionally narrow and private. Future expansion, such as
custom exception types or metadata-driven validation, is deferred until a real
second use case justifies it.

### 15. Bronze Naming Conventions Distinguish Pure Derivation from I/O

Bronze ingestion adopts explicit verb conventions to signal behavior:

- `derive_*` for pure deterministic value derivation
- `resolve_*` for aggregate composition of multiple derived values
- `acquire_*` for cache/network/file acquisition
- `ingest_*` for orchestration entry points

#### Rationale

This naming convention makes side effects easier to spot in code review and
keeps pure functions distinguishable from operational functions. In particular,
the platform avoids names that may imply filesystem or network behavior for
logic that is intentionally pure.

### 16. Bronze Cache Behavior Is Encapsulated Behind the Acquisition Function

Bronze source caching is required for Bronze ingestion, but the cache itself is
treated as an internal implementation detail of `bronze_io.py`. External code
interacts with cache-aware acquisition through the public function
`acquire_bronze_source_file(...)`, not through a cache object API.

#### Rationale

This keeps the public Bronze I/O surface small and avoids exposing a cache
abstraction before broader reuse justifies it. The implementation can evolve
internally while orchestration code remains concerned only with acquiring a
local source file and observing cache-hit metadata.

### 17. Bronze Contract Concerns Promoted to a Dedicated `contracts` Package

> **Supersedes decision 10.**

Bronze contract concerns now live in `nyc_cab/contracts/bronze.py` rather than
being mixed into the ingestion entry-point module.

The contract module owns:

- supported cab types and supported Bronze periods
- the canonical source URL base and filename template
- partition column layout
- the no-Spark schema-field representation (see decision 21)
- pure derivation helpers for period identifiers, source filenames, and source
  URLs
- both the check-list and raising forms of slice-support and schema validators
  (see decision 20)

The module performs no I/O and imports no Spark symbols.

#### Rationale

Decision 10 deferred the contracts package on YAGNI grounds, and that deferral
held until the API shape of the validation framework demanded otherwise. Two
forms of pressure emerged simultaneously:

- The Tier 2/Tier 3 split between structural and semantic validation (see
  decision 19) required a single source of truth for layer-specific facts that
  was separately addressable from the typed request object's universal
  invariants.
- The schema check needed to be expressible without coupling the contract to
  PySpark, since the contract is data the validator consumes rather than code
  the validator depends on (see decision 21).

Both pressures were absent when decision 10 was made and present by the time
decision 17 was made. The deferral was correct; the supersession is correct.
This pattern - defer until the abstraction earns its existence, then commit
when it does - is the platform's general approach to factoring decisions.

### 18. Validation Protocol via the `_Validated` Mix-in

> **Extends decision 14.**

The platform exposes a validation protocol for typed application objects
through the `_Validated` mix-in in `nyc_cab._validation`. The mix-in owns:

- the `_type_check_specs` class attribute, declaring constructor-argument
  type-check specifications as either two-tuples `(field_name, required_type)`
  or three-tuples `(field_name, required_type, excluded_type)` for cases like
  rejecting `True`/`False` from an `int` field
- `create_validated(*args)` as the safe constructor that runs constructor
  type checks, builds the instance, and runs structural checks
- `validate()` as the raise-on-failure validator
- `is_valid()` as the non-raising boolean form
- `validity_check(field_name)` as the composition helper that returns a check
  tuple delegating validation to a composed `_Validated` instance

The bare dataclass constructor remains available for tests that need to
construct intentionally invalid instances.

#### Rationale

Decision 14's `raise_on_violations` helper was the seed of this protocol but
not the protocol itself. As more typed objects adopted the same validation
shape (`BronzeIngestionConfig`, `BronzeIngestionRequest`, `AcquiredSourceFile`,
`_BronzeSourceCache`, `BronzeResolvedPaths`, `BronzeIngestionResult`), the
duplication of construction-time type-check loops, structural-check methods,
and exception aggregation became real boilerplate worth centralizing.

The `validity_check` operation is the load-bearing addition. When one
`_Validated` object composes another - as `BronzeIngestionResult` composes
`BronzeIngestionRequest` and `AcquiredSourceFile` - the outer object's
structural checks must delegate to the inner objects without short-circuiting
on the first inner failure. Calling the inner object's `validate()` directly
would raise immediately and prevent the outer object from aggregating
violations across all members. `validity_check` produces a `(passed,
field_name, self)` tuple that the outer object's check list aggregates
through the same `raise_on_violations` path as any other check.

This protocol remains intentionally minimal. Domain rules do not belong here;
the mix-in owns vocabulary, and domain modules own rules.

### 19. Tier 2 Structural Validation vs Tier 3 Semantic Validation

The platform separates validation into two tiers based on the source of truth
for the rule:

- **Tier 2, structural:** the rule reflects a universal invariant of the
  field's type or shape. A month must always be 1-12. A year must be
  reasonable. A non-empty path must not be the empty string. These rules live
  on the typed object's `_structural_checks` and persist across contract
  versions.

- **Tier 3, semantic:** the rule reflects what the current contract version
  supports. Bronze v1 supports yellow cabs and 2023-01 / 2023-02 periods.
  These rules live in `contracts/bronze.py` and change when the contract
  version advances.

A check's tier is determined by what knowledge the check depends on, not by
where the check happens to be invoked. Both tiers use the same `CheckTuple`
vocabulary and aggregate through the same `raise_on_violations` path.

#### Rationale

The two tiers have different rejection semantics. Structural failures mean
the data is malformed under invariants that will hold for the next decade.
Semantic failures mean the data is well-formed but unsupported under a
versioned contract that will change. Surfacing them through the same exception
type with the same violation shape is correct - both are user-visible "your
input was rejected" - but conflating their *source of truth* is not.

Concretely, this discipline rules out a tempting failure mode: putting all
checks for `BronzeIngestionRequest` into a single `_structural_checks` list
that mixes `1 <= month <= 12` (structural, eternal) with
`(year, month) in BRONZE_SUPPORTED_PERIODS` (semantic, contract-dependent).
That would visually flatten the distinction in code and create cognitive load
for every reviewer who has to ask "is this rule something we control or
something the language controls?" when reading a check tuple. The platform
keeps the two tiers in separate modules instead, with composition at the call
site if both are needed.

### 20. Two-Function Validator Pattern: Check List and Raising Form

Each contract validation surface exposes both a check-list builder and a
raising validator:

- `get_*_checks(...)` returns `tuple[CheckTuple, ...]` for composition into
  a `_Validated` object's check list
- `validate_*(...)` calls the check-list builder and passes the result to
  `raise_on_violations`, raising on any failure

The two functions share their underlying logic. The split exists at the
calling-convention layer.

#### Rationale

The check-list form lets contract checks compose into typed objects via
decisions 18 and 19. The raising form serves direct callers like the CLI, ad
hoc scripts, or future Airflow operators that want a fail-loud entry point
without going through a typed object.

This is the same shape as `_Validated.validate()` and `_Validated.is_valid()`
on the mix-in - one raises, one doesn't, both are useful in different
contexts. Standardizing the convention across the framework and the contract
modules means future readers always know to look for the pair when they want
either form.

### 21. Schema Representation Is Spark-Free

Bronze schema fields are represented as a frozen dataclass `BronzeSchemaField`
with the shape `(name: str, spark_type: str, nullable: bool)` rather than as
a `pyspark.sql.types.StructType`.

The `spark_type` value matches the output of `DataType.simpleString()`. The
canonical examples are `"string"`, `"int"`, `"bigint"`, `"double"`,
`"timestamp"`, and `"date"`. Future schema authors must use `simpleString()`
rather than `typeName()` or `jsonValue()`, which produce different
stringifications.

#### Rationale

The contract module is data the validator consumes, not code the validator
depends on. Importing `pyspark.sql.types` into `contracts/bronze.py` would
make the contract module Spark-coupled and prevent non-Spark consumers
(documentation generators, schema-diff tools, lightweight validators) from
reading it. The string-encoded type carries the same information as a
`StructType` for comparison purposes, and the bridge to Spark types happens
at the validator's call site rather than at the contract's definition site.

This is the contract module's hardest constraint. The validator cannot do its
job without comparing against the contract; the contract must remain
freestanding for the architecture to hold.

### 22. Module Header Docstrings Are Public API Documentation

Module-level docstrings describe the public API surface only. Private
implementations - leading-underscore classes, leading-underscore functions,
internal helpers - appear in the source code for maintainers but not in the
module header.

The `_BronzeSourceCache` class in `nyc_cab.ingestion.bronze_io` is the
canonical example. The class is fully implemented and publicly accessible
within the module, but the module header only diagrams `AcquiredSourceFile`
and the public acquisition function.

#### Rationale

Module docstrings are picked up by Sphinx and rendered into the published
ReadTheDocs surface. What lands there is what external consumers learn the
package does. Including private implementation details in the docstring would
expose abstractions the platform reserves the right to change without notice.

This rule is a documentation-discipline counterpart to the leading-underscore
naming convention. The naming says "do not import this from outside the
module"; the docstring discipline says "do not document this for outside
audiences." Both reinforce the same boundary.

### 23. Cache Identity Uses Filename Keying With a Known Upgrade Path

The Bronze source cache currently identifies cached files by their source
filename alone (e.g., `yellow_tripdata_2023-01.parquet`). The on-disk cache
key is the filename; a cache hit is a file-exists check at the deterministic
path returned by `_derive_cache_file_path`.

This is sufficient as long as the content behind a given filename never
changes. For NYC TLC data this is mostly true — published historical datasets
are stable — but TLC has been known to silently revise files without changing
the URL or filename.

The known upgrade path is HTTP conditional-GET keyed on the `ETag` or
`Last-Modified` header returned by the CloudFront CDN. At download time, the
cache would record the response `ETag` in a sidecar metadata file alongside
the cached parquet. At cache-hit time, the cache would compare the stored
`ETag` against the remote `ETag` (via an `If-None-Match` / `304 Not Modified`
round trip) before declaring a hit. This avoids re-downloading unchanged files
and detects silent upstream revisions.

The implementation seam is narrow: `_derive_cache_file_path` stays unchanged
(filename is still the on-disk key), and the cache-hit check in
`acquire_bronze_source_file` gains an ETag comparison before returning. The
retrofit is localized to two functions in `bronze_io.py`.

#### Rationale

Filename keying is correct for single-month manual runs and for the current
Bronze v1 scope. The pressure to upgrade appears when backfills span multiple
months across runs separated by days or weeks, and the operator needs
confidence that a cached file still matches what the source is serving. That
pressure is absent today and present at the Airflow-orchestrated-backfill
milestone. The platform defers the upgrade until that milestone, consistent
with the general factoring discipline established in decisions 10 and 17.

### 24. Schema Definitions Are Hardcoded Until Multi-Version Pressure Justifies a Table

Bronze v1 defines its expected schema as a hardcoded tuple of
``BronzeSchemaField`` instances in ``contracts/bronze.py``. The tuple is
version-controlled alongside the code and loaded at import time with no I/O.

The known upgrade path is a time-sliced schema table keyed by
``(cab_type, schema_version, effective_from, effective_to)``, with one row
per field per version. The ``BronzeSchemaField`` dataclass is already the
row shape of this table; the migration from tuple literal to table rows is a
format change, not a schema change. The accessor function
``get_bronze_raw_schema_fields(cab_type)`` would change from returning a
hardcoded tuple to querying the table filtered by cab type and effective
period. The consumer API does not change.

Three resolution options exist for the table's storage, ordered by
increasing operational weight:

- A bundled JSON or CSV file read at import time. No Spark, minimal I/O,
  compatible with the contract module's no-I/O constraint.
- A thin adapter that loads the table from an external store (Snowflake,
  S3) at startup. The contract module stays I/O-free; the adapter is the
  seam where I/O enters.
- A Python dict keyed by ``(cab_type, schema_version)`` instead of a single
  tuple. Still hardcoded, still no I/O, but structured for multi-version
  lookup.

#### Rationale

The inflection point for this upgrade is the moment the platform supports
more than one distinct schema version across its period range. For Bronze v1
with two months of one cab type, a single hardcoded tuple is correct. The
pressure to upgrade appears when the platform expands to cover period ranges
where the TLC changed column sets (``congestion_surcharge`` added 2019,
``Airport_fee`` added ~2022, ``cbd_congestion_fee`` added January 2025,
``passenger_count``/``RatecodeID`` type migration from integer to double),
or when multiple cab types with different schemas enter scope. The platform
defers the table until that pressure is present, consistent with decisions
10, 17, and 23.

### 25. Stub-Testing Discipline: Every `NotImplementedError` Gets a Test

Every `NotImplementedError` stub in the codebase has a corresponding test that
asserts that exception using `pytest.raises(NotImplementedError)`. This test
acts as a forwarding declaration: it marks the stub as an explicit open work
item, ensures the stub is not silently missed in coverage accounting, and
transfers ownership to the next implementer by failing loudly if the stub is
replaced without a real test.

The CI coverage gate is set at 90% (wiggle room during active development);
the destination is 100% branch coverage at alpha. Stub tests are what make
100% branch coverage sustainable during incremental development — they
contribute real coverage to the stub branch while signaling incomplete
implementation rather than hiding it.

#### Rationale

Without stub tests, a `NotImplementedError` stub can reach a CI-green state
in two bad ways: it is excluded from coverage (invisible), or it is reachable
but untested (silently uncovered). Both outcomes weaken the coverage gate as a
signal. A stub test makes the stub's incomplete status explicit, keeps the
gate meaningful, and documents intent in a place the next implementer cannot
miss — the failing test itself.

### 26. Schema Equivalence Layer Absorbs TLC Schema Instability at Bronze

TLC source files are not schema-stable across months. The Bronze layer absorbs
this instability through a normalization and equivalence layer in
`contracts/bronze.py` rather than by weakening the schema validator or
hard-coding month-specific patches.

The layer has three components:

- `normalize_bronze_column_name` — case-insensitive, underscore-insensitive
  column name matching (`Airport_fee` ↔ `airport_fee`). This handles TLC
  capitalization drift without treating differently-cased names as distinct
  columns.
- `BRONZE_TYPE_FAMILIES` — groups Spark types into compatibility families.
  Integer and floating-point types are merged into a single `"numeric"` family
  (`bigint` ↔ `double`). Timestamp variants are grouped (`timestamp` ↔
  `timestamp_ntz`). The numeric super-family was added after February 2023
  data showed `passenger_count` and `RatecodeID` arriving as `bigint` rather
  than `double`, indicating a cross-type drift at the TLC source.
- `bronze_types_are_compatible` — returns true on exact type match or same
  family match. Two unknown types are not compatible (explicit `None` edge
  case).

Column canonicalization is applied in `bronze_entrypoint.py` via
`_canonicalize_bronze_columns` before writing Bronze output. This ensures
that partitioned Parquet written by the platform always carries canonical
column names regardless of source variation.

#### Rationale

The alternative — weakening the schema validator to accept any observed
column name or type — would allow silent schema drift to reach Bronze output
undetected. The equivalence layer preserves the validator's strictness while
correctly distinguishing cosmetic variation (capitalization, underscore
convention) from genuine schema change (new column, removed column, type
incompatibility across families). The decision to merge integer and floating
types into a single numeric family was empirically justified by the February
2023 data, not by speculation: the validator failed on real TLC data, the
family was added in response, and the live integration test now asserts the
end-to-end case.

### 27. Silver Rejected Records Use Dedicated Partitions (Option A)

Silver v1 persists rejected records in a `silver_rejected` partition
alongside the `silver` accepted partition. Both share the base schema;
rejected records carry additional rejection metadata. This guarantees
Bronze count = Silver accepted + Silver rejected, provably and
inspectably.

In a production system, Option C (sidecar table with primary key/row
hash and rejection reason, rejected records remain in Bronze only) would
be preferred for storage efficiency. Option A is chosen here for
portfolio debugging sanity: any reviewer can inspect rejected records
directly without re-running the pipeline or joining back to Bronze.

### 28. Monthly Slice Is Emerging as Shared Vocabulary

Bronze and Silver currently define parallel request types representing
the same logical work unit: a (cab_type, year, month) monthly slice.

At present, the duplication is intentional. The Silver API is still
evolving, and the shared abstraction is not yet stable enough to
justify promotion into a common contract type.

If the shape remains stable after Silver implementation, the common
fields and structural validation should move into a shared monthly
slice vocabulary, while layer-specific semantic checks remain owned by
their respective contracts and orchestrators.

Layer-specific extensions should be modeled through specialization of
the shared type rather than redefinition of the common fields.

#### Review Note

Over-separation of concerns is a recurring pattern to watch for. The
signal is two types with identical fields and identical structural
checks living in different packages. The instinct to separate is
correct when the types are *likely to diverge*; it is premature when
the divergence is speculative. When in doubt, start shared and
specialize when the pressure justifies it — the same factoring
discipline applied in decisions 10, 17, and 24.

### 29. Silver Constraints Use Two-Phase Validation Around Normalization

Silver domain constraints are split into pre-normalization and
post-normalization phases. Both phases write to the same
``_rejection_reasons`` array column. Type normalization (double → int
casts) runs between the two phases.

Pre-normalization constraints fire on Bronze-typed columns and catch
two categories of problem that would be invisible after casting:

- **Non-integral doubles in normalization targets.** A
  ``passenger_count`` of ``1.5`` would be silently truncated to ``1``
  by ``cast("int")``. The integrality check tags the violation while
  the original double value is still available.
- **Nulls in constraint-checked fields.** Spark's three-valued logic
  means ``NULL >= 0`` evaluates to ``NULL``, not ``True`` or ``False``.
  Without an explicit null check, a row with ``fare_amount = NULL``
  would not trigger ``NEGATIVE_FARE`` and would be silently accepted
  despite having no fare. The null policy rejects NULLs in all five
  fields that downstream constraints depend on: ``fare_amount``,
  ``trip_distance``, ``passenger_count``, ``tpep_pickup_datetime``,
  ``tpep_dropoff_datetime``.

Post-normalization constraints fire on Silver-typed (normalized)
columns and enforce domain business rules: non-negative fares and
distances, valid passenger count range, pickup-before-dropoff temporal
ordering.

Normalization is applied to ALL rows including those already tagged for
rejection. This preserves the "correctable corral" property from
decision 27: rejected rows are in normalized form and can be fixed and
re-submitted without re-normalizing.

#### Flow

    apply_pre_normalization_constraints(df)
    → apply_type_normalizations(df)
    → apply_post_normalization_constraints(df)
    → split_accepted_rejected(df)

#### Rationale

The two-phase split is driven by a data integrity constraint, not an
aesthetic preference. The integrality check cannot run after
normalization because the information it needs (the fractional part of
the double) is destroyed by the cast. Grouping null checks with the
integrality phase keeps all "must fire before cast" constraints
together and avoids a third phase.

### 30. Silver Transformation Uses a Focused Four-Module Split

Silver transformation is split across four focused modules within
``nyc_cab.transform``:

- ``silver_request.py`` owns the typed Silver transform request
- ``silver_transforms.py`` owns pure column normalizations (type casts)
- ``silver_validators.py`` owns domain constraint tagging and the
  accepted/rejected split
- ``silver_entrypoint.py`` owns the top-level transformation
  orchestration and the Silver result type

The package is named ``transform`` (not ``transformation``) for
brevity and parallel construction with ``ingestion``.

#### Rationale

This mirrors the factoring discipline from decision 9. Each module
has a single responsibility, narrow test setup, and no cross-module
coupling beyond shared contract types. The validators are further
split into pre-normalization and post-normalization phases (see
decision 29), but both phases live in the same module since they
share the rejection-tagging machinery.

### 31. Create a Batch-Event Bridge

The `nyc_cab_events` package implements a batch-event bridge pattern:
source-of-truth batch records from the Silver accepted layer drive
deterministic event generation, and streaming aggregates reconcile back
against batch counts.

#### Rationale

This mirrors prior production work translating governed batch analytics into
event-shaped processing streams, while using public data and project-owned
models. The platform and domain differ; the architectural pattern is the same.

## Architectural Guardrails

To prevent the architecture from drifting into monolithic design:

- Subsystems must not inject their settings into `RuntimeConfig`. If a
  subsystem requires unique configuration, it must be factored into its own
  module, as with `SparkConfig` and `BronzeIngestionConfig`.
- Modules must not reach into `RuntimeConfig` to find settings that belong to a
  typed ingestion request or an engine-specific configuration object.
- Orchestration layers such as the CLI and Airflow are responsible for
  composing the necessary configuration objects at the edge, so application
  code remains agnostic to how configuration was sourced.
- The name `RuntimeConfig` is intentional. It signals that the object captures
  runtime context rather than every possible configuration concern in the
  platform.
- Bronze request/config objects must remain explicit inputs. The platform must
  not introduce ambient process-level configuration state.
- Bronze contract validation must remain separate from request-level structural
  validation. A typed request object must not silently absorb subsystem policy
  rules that belong to the Bronze contract.
- Private helper modules such as `_env.py` and `_validation.py` must remain
  narrow. They exist to centralize repeated mechanics, not to become generic
  internal frameworks.
- The `_Validated` mix-in owns vocabulary; domain modules own rules. The mix-in
  must not grow domain-specific helpers (`validate_email`,
  `validate_iso_date`, etc.). New shared mechanics earn entry to
  `_validation.py` only when at least two domain modules already need them.
- Structural and semantic validation rules live in different modules and must
  not be flattened into a single check list. A check's tier is determined by
  what knowledge it depends on, not by where it is most convenient to invoke.
- The `contracts/` package must perform no I/O and import no Spark symbols.
  Schema fields are represented in their own typed structure, not as
  Spark-native types.
- Module header docstrings describe public API only. Private classes and
  functions are visible to maintainers in source but not to external readers
  on ReadTheDocs.
- Pre-normalization constraints must fire before type normalization casts.
  The ordering is load-bearing, not aesthetic: integrality checks depend on
  the original double values, which are destroyed by the cast.
