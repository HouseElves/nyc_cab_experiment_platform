# NYC Cab Experiment Platform

**Deterministic Experimentation Metrics on Enterprise-Scale Public Data**

## Overview

The NYC Cab Experiment Platform implements a distributed experimentation
metrics pipeline using Apache Spark and Apache Airflow on publicly available
NYC Taxi trip records.

This system models key properties of modern experimentation platforms:

- Deterministic cohort assignment
- Partition-aware ingestion
- Versioned metric computation
- Reproducible recomputation
- Idempotent Airflow backfills
- Explicit data contract validation

## Architecture

| Layer | Description | Status |
| --- | --- | --- |
| **Bronze** | Raw monthly trip ingestion to partitioned Parquet | Complete |
| **Silver** | Normalized and validated trip records with derived metrics | Complete |
| **Events** | Batch-event bridge: deterministic event emission from Silver, hourly aggregates in Postgres, reconciliation back against Silver counts | Scaffolded |
| **Experiment** | Stable cohort assignment using deterministic hashing | Planned |
| **Gold** | Cohort-based metric aggregation with windowed computation | Planned |

## Philosophy

Correctness is a feature.

All transformations are deterministic.
All partitions are recomputable.
All cohort assignments are stable.

## Build Methodology

The baseline scaffold was produced under strict architectural and linting constraints,
then validated through local Spark execution and deterministic recomputation checks.

### Code Hygiene

The standard for code hygiene is zero failures against the `.pylintrc` file in the project repository.
In practice, this means that:

1. Lint failures are fixed in code before push to GitHub, or
2. A lint suppression is applied at the point of occurrence and documented
   with an explicit justification in the containing function header.

### Testing Standards

The mirrored test structure is intentional. Module-level tests live in a
local `test/` directory, where `test/module.py` contains tests for
`module.py`.

These local `test/` directories are authoritative for module-level coverage.
The minimum standard for push to GitHub is **90% branch coverage** for
module-level tests. The platform currently meets this standard at 100% branch
coverage across 519 tests on listed modules.

Root-level `test/` suites cover integration and functional concerns.
Bronze and Silver integration testing is each split into two tiers:

- **Tier 1 (`test/bronze_integration.py`):** 11 tests, synthetic parquet
  pre-staged in a local cache fixture, zero mocks, CI-safe. Covers the
  full five-step ingestion orchestration path including schema validation,
  column canonicalization, partition overwrite idempotency (with read-back
  count assertion), and schema equivalence normalization.
- **Tier 2 (`test/bronze_live.py`):** 4 tests marked `@pytest.mark.live`,
  real TLC downloads, verifies schema match and end-to-end canonicalization
  against live source data. Excluded from standard CI runs.
- **Tier 1 (`test/silver_integration.py`):** 10 tests, synthetic Bronze
  partition pre-staged in the expected Hive directory layout, zero mocks,
  CI-safe. Covers the full 11-step transformation orchestration path
  including two-phase constraint validation, type normalization, accepted
  and rejected partition writes, reconciliation invariant, and idempotent
  re-execution with read-back count assertion.
- **Tier 2 (`test/silver_live.py`):** 6 tests marked `@pytest.mark.live`,
  real TLC downloads for January and February 2023, verifies plausible
  accepted/rejected ratios, reconciliation, partition output, and
  February schema-drift compatibility against live source data.
- **Tier 1 (`test/events_integration.py`):** 4 tests marked
  `@pytest.mark.kafka` and `@pytest.mark.postgres`, currently scaffolded
  against the `NotImplementedError` stubs in the events package. Will drive
  the full Silver → producer → Kafka → consumer → Postgres → reconcile loop
  once the stubs land. Requires the project `docker-compose.yml`.

Expected future root-level coverage includes:

- CLI invocation
- multi-module execution paths
- Airflow DAG import/invocation validation

### Test Suite Discipline

As Spark/JVM integration tests have grown, the project must split validation
into fast local unit checks, fixture-backed Spark integration checks,
live-source tests, and scheduled/manual end-to-end checks so the test suite
remains trusted rather than avoided. The intended tier structure is:

- **Fast path (every commit):** pure-Python contract, request, and validation
  tests — no JVM, no I/O. Target mark: `unit`.
- **Spark path (PR / pre-push):** fixture-backed PySpark module tests and
  Tier 1 integration tests. Target mark: `spark`.
- **Live path (scheduled / manual):** real TLC downloads, Bronze → Silver
  smoke test. Mark: `live` (already registered).
- **Audit path (release / manual):** coverage report, Sphinx docs build,
  full local pipeline run, reproducibility check.

All 543 tests carry a `unit`, `spark`, `live`, `kafka`, or `postgres` mark
(or some combination). The `kafka` and `postgres` marks are orthogonal:
a Tier 1 events integration test typically carries both, since it exercises
the producer-through-sink path. Mixed-responsibility files (result types
alongside orchestration tests) use per-function marks; homogeneous files
use a module-level `pytestmark`. CI runs fast-path and Spark-path tests
together under `-m "not live and not kafka and not postgres"` until the
GitHub Actions jobs are split by tier; the marks are in place for that
split to be mechanical when it lands.

### Running Tests

Execute the full test suite and measure branch coverage from the project root:

```bash
# Run tests with coverage
coverage run -m pytest

# Display the coverage report
coverage report -m

# Fast path only — no JVM, no I/O
pytest -m unit

# Spark path only — fixture-backed PySpark tests
pytest -m spark

# Fast + Spark (excludes live TLC downloads)
pytest -m "not live"

# Live tests only — requires network access
pytest -m live

# Events integration tests — requires docker-compose up
pytest -m "kafka and postgres"
```

## Installation

Python 3.11 is required.

### Core project

For local development, install the locked core environment and then install
the package in editable mode:

```bash
pip install -r requirements.lock.txt
pip install -e .
```

This installs the pinned runtime, testing, and documentation dependencies
used by the project.

Spark is executed locally using PySpark. No external cluster is required
for baseline execution.

### Airflow

Airflow is not a package dependency and must be installed separately using
the official constraints file.

Airflow must be installed in a dedicated Python environment separate from
the core Spark/docs/test environment. This separation is required because
Airflow's constrained dependency set causes conflicts with the Sphinx-based
documentation toolchain used by the core project.

The recommended Airflow installation is:

```bash
pip install "apache-airflow[postgres]==2.9.1" \
  --constraint https://raw.githubusercontent.com/apache/airflow/constraints-2.9.1/constraints-3.11.txt

export AIRFLOW_HOME=~/portfolio/nyc_experiment_platform/.airflow
airflow db migrate
```

Pinned Airflow provider versions are in `requirements-airflow.lock.txt`.

### Events (Kafka + Postgres)

The events package is an optional integration. Install the locked event
dependencies alongside the core environment:

```bash
pip install -r requirements.lock.txt
pip install -r requirements-events.lock.txt
pip install -e '.[events]'
```

Local Kafka and Postgres are provisioned via the project `docker-compose.yml`:

```bash
docker compose up -d zookeeper kafka postgres
```

Container names are deterministic (`nyc_cab_events_zookeeper`,
`nyc_cab_events_kafka`, `nyc_cab_events_postgres`) and all three services
have health checks. Kafka auto-topic-creation is intentionally off; the
producer creates `trip.completed.v1` and `trip.completed.v1.invalid`
explicitly so the test bed mirrors the deployed surface.

## Status

Silver layer complete. All transformation stubs replaced with tested implementations:

- Runtime packaging complete
- Local Spark execution verified
- Airflow environment isolated
- Dependency boundaries established
- Validation protocol established (`_Validated` mix-in with type-check
  specifications, structural-tier and semantic-tier separation, `validity_check`
  composition for nested validated objects)
- Bronze contract module complete (slice support, source naming, partition
  layout, Spark-free schema-field representation, two-form validators,
  schema equivalence layer)
- Bronze ingestion implemented: `BronzeIngestionConfig`, `BronzeIngestionRequest`,
  `BronzeResolvedPaths`, cache-aware file acquisition with `_BronzeSourceCache`,
  five-step orchestration entry point, column canonicalization
- Schema equivalence layer absorbs TLC source instability: case/underscore
  normalization, numeric type family (`bigint` ↔ `double`), timestamp family
  (`timestamp` ↔ `timestamp_ntz`), validated against live February 2023 data
- Silver contract module complete (19-field normalized schema, 11 rejection
  reasons in two phases, `TypeNormalization` specs, `DomainConstraint`
  metadata, slice validation)
- Silver transformation implemented: two-phase validation sandwich
  (pre-normalization constraint tagging → type normalization → post-normalization
  domain checks), accepted/rejected partition split, reconciliation invariant
  (`bronze_count == accepted_count + rejected_count`) enforced structurally
  at result construction time
- Integration tests in two tiers per layer: Tier 1 (CI-safe, synthetic
  fixtures, zero mocks), Tier 2 (`@pytest.mark.live`, real source data)
- All 543 tests annotated with `unit`, `spark`, `live`, `kafka`, or
  `postgres` pytest marks; mixed-responsibility files carry per-function marks
- GitHub Actions CI live: three independent jobs (lint, test, import smoke);
  pylint 10/10 gate, 90% branch coverage gate, clean-install import gate
- 543 tests, 100% branch coverage on listed modules
- `nyc_cab_events` package scaffolded parallel to `nyc_cab` in the same wheel:
  four subpackages (`contracts`, `producer`, `consumer`, `sink`) implementing
  the batch-event bridge described in design log decision 31
- `TripCompleted` event contract complete: nine-field validated dataclass,
  topic constants (`trip.completed.v1`, `trip.completed.v1.invalid`),
  quarantine routing function
- Producer, consumer, and sink modules ship validated config and result
  dataclasses; the producer's reconciliation invariant
  (`silver_read_count == events_emitted + events_quarantined`) and the sink's
  derivation-consistency check on `ReconciliationResult` are both enforced
  structurally at construction time
- Heavy operations (`derive_event_id`, `produce_trip_completed_events`,
  `consume_and_aggregate`, `ensure_table`, `upsert_hourly_counts`,
  `reconcile_against_silver`) are `NotImplementedError` stubs, each covered
  by a matching `pytest.raises` test per design log decision 25
- `docker-compose.yml` brings up Kafka 7.6 + Zookeeper + Postgres 16 with
  deterministic container names and health checks; topic auto-creation is
  intentionally off
- Optional dependencies group `events` added to `pyproject.toml`
  (`confluent-kafka`, `psycopg[binary]>=3.2`); companion
  `requirements-events.lock.txt` pins the four-package transitive closure
- New pytest markers `kafka` and `postgres` registered as orthogonal markers;
  testpaths expanded for the four events subpackages
- 114 new tests, all currently `unit`-marked, pylint 10/10 on the new package,
  100% branch coverage on `nyc_cab_events`
- Design log decisions 32, 33, 34 record the Kafka client choice, the
  event-time bucketing rule, and the Postgres-as-sink decision

**Next milestone:** complete the `TripCompleted` wire-format contract.
Deterministic `event_id` derivation (SHA-256 over the source-row tuple
already committed in the producer stub docstring), stable `event_key`
for Kafka partition routing, explicit `schema_version` field for wire-format
versioning, JSON serialize/deserialize pair, and tests covering
determinism across re-runs and rejection of malformed payloads on
deserialization. Sets up the subsequent milestone: a deterministic
trip-completed producer working solely off the Silver layer.

## Roadmap

- Metric versioning
- Late-arriving data simulation
- Skew detection and mitigation
- Delta Lake time-travel
- Automated data quality framework

## License

AGPL-3.0 — see [LICENSE](LICENSE).
