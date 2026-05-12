# NYC Cab Experiment Platform

**Deterministic Experimentation Metrics on Event-Scale Public Data**

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
| **Silver** | Normalized and validated trip events with derived metrics | Scaffolded |
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
module-level tests. The platform currently meets this standard at 100% branch coverage
across 250+ tests on listed modules.

Root-level `test/` suites cover integration and functional concerns.
Bronze integration testing is split into two tiers:

- **Tier 1 (`test/bronze_integration.py`):** 11 tests, synthetic parquet
  pre-staged in a local cache fixture, zero mocks, CI-safe. Covers the
  full five-step ingestion orchestration path including schema validation,
  column canonicalization, partition overwrite idempotency (with read-back
  count assertion), and schema equivalence normalization.
- **Tier 2 (`test/bronze_live.py`):** 4 tests marked `@pytest.mark.live`,
  real TLC downloads, verifies schema match and end-to-end canonicalization
  against live source data. Excluded from standard CI runs.

Expected future root-level coverage includes:

- CLI invocation
- multi-module execution paths
- Airflow DAG import/invocation validation
  
### Running Tests

Execute the full test suite and measure branch coverage from the project root:

```bash
# Run tests with coverage
coverage run -m pytest

# Display the coverage report
coverage report -m
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

## Status

Bronze layer complete. All ingestion stubs replaced with tested implementations:

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
- Integration tests in two tiers: Tier 1 (CI-safe, synthetic fixtures),
  Tier 2 (`@pytest.mark.live`, real TLC downloads)
- Silver transformation scaffolded: contract module with two-phase domain
  constraint model (11 rejection reasons covering integrality, null policy,
  and domain rules), `transform/` package with typed request, normalization,
  validation, and orchestration modules, reconciliation invariant
  (`bronze_count == accepted_count + rejected_count`) enforced structurally
- 360+ tests, 100% branch coverage on listed modules
**Next milestone:** Silver layer implementation — fill transformation stubs,
integration tests, reconciliation tests.

## Roadmap

- Metric versioning
- Late-arriving data simulation
- Skew detection and mitigation
- Delta Lake time-travel
- Automated data quality framework

## License

AGPL-3.0 — see [LICENSE](LICENSE).
