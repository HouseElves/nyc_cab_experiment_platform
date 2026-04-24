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

| Layer | Description |
|---|---|
| **Bronze** | Raw monthly trip ingestion to partitioned Parquet |
| **Silver** | Normalized and validated trip events with derived metrics |
| **Experiment** | Stable cohort assignment using deterministic hashing |
| **Gold** | Cohort-based metric aggregation with windowed computation |

## Philosophy

Correctness is a feature.

All transformations are deterministic.
All partitions are recomputable.
All cohort assignments are stable.

## Build Methodology

The baseline scaffold was produced under strict architectural and linting constraints,
then validated through local Spark execution and deterministic recomputation checks.

### Testing Standards

The mirrored test structure is intentional. Module-level tests live in a
local `test/` directory, where `test/module.py` contains tests for
`module.py`.

These local `test/` directories are authoritative for module-level coverage.
The minimum standard for push to GitHub is **90% branch coverage** for
module-level tests.

Root-level `test/` suites are reserved for integration and functional
concerns. Expected future coverage here includes:

- workflow integration testing
- CLI invocation
- multi-module execution paths
- end-to-end Spark checks
- Airflow DAG import/invocation validation

### Code Hygiene

The standard for code hygiene is zero failures against the `.pylintrc` file in the project repository.
In practice, this means that:

1. Lint failures are fixed in code before push to GitHub, or
2. A lint suppression is applied at the point of occurrence and documented
   with an explicit justification in the containing function header.

## Installation

Python 3.11 is required.

### Core project

```bash
pip install -e .
```

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

Baseline scaffolding complete:

- Runtime packaging complete
- Local Spark execution verified
- Airflow environment isolated
- Dependency boundaries established
- Bronze ingestion implementation next

**Next milestone:** Bronze ingestion implementation.

## Roadmap

- Metric versioning
- Late-arriving data simulation
- Skew detection and mitigation
- Delta Lake time-travel
- Automated data quality framework

## License

AGPL-3.0 — see [LICENSE](LICENSE).
