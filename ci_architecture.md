# CI Architecture Specification

This document describes the GitHub Actions CI architecture for the NYC Cab
Experiment Platform. It is written to serve as both a design record and a
code-generation prompt for producing `.github/workflows/ci.yml`.

## Project context

- Repository: `HouseElves/nyc_cab_experiment_platform`
- Package: `nyc_cab` (flat layout under `nyc_cab/`)
- Python version: 3.11 (single target, no matrix yet)
- Test framework: pytest with branch coverage via `pytest-cov`
- Lint: pylint, configured by `.pylintrc` at repo root, max-line-length=160
- Build system: setuptools via `pyproject.toml`
- Current state: Bronze and Silver layers complete, 423 tests, 100% branch
  coverage on listed modules. The test suite includes real Spark-backed module
  tests and Tier 1 integration tests; Java is required for the test job.

## Four-tier test architecture

As the Spark/JVM integration test surface has grown, the test suite must be
structured so it is trusted rather than avoided. Tests are grouped into four
tiers by cost and trigger frequency:

### Tier 1 — Fast path (every commit)

Pure Python. No JVM startup, no I/O, no network. Sub-second per file.

- Contract and schema tests (`nyc_cab/contracts/test/`)
- Request and result validation tests (structural checks, type enforcement,
  `_Validated` mix-in behaviour)
- Non-Spark helper tests (path resolution, config loading, env parsing)
- Target mark: `unit`

### Tier 2 — Spark path (PR / pre-push)

Fixture-backed PySpark tests. Requires JVM; module-scoped sessions keep
startup cost to one per file.

- Silver transform module tests (validators, normalizations)
- Tier 1 integration tests (`test/bronze_integration.py`,
  `test/silver_integration.py`)
- Partition layout and reconciliation assertions
- Target mark: `spark`

### Tier 3 — Live path (scheduled weekly / manual)

Real network I/O. Downloads TLC source files; exercises real Bronze → Silver
pipeline against live data.

- `test/bronze_live.py` — real TLC download, schema match, cache round trip
- `test/silver_live.py` — Bronze → Silver with live data, ratio plausibility
- Mark: `live` (already registered in `pytest.ini`)

### Tier 4 — Audit path (release / manual only)

Full correctness audit. Not part of routine CI.

- Full coverage report
- Sphinx docs build
- End-to-end local pipeline run
- Reproducibility check (run twice, diff outputs)

### Implementation status

`live` is the only mark currently registered. Tier 1 and Tier 2 separation
requires registering `unit` and `spark` marks in `pytest.ini` and annotating
the existing test files — this work lands in the CI milestone alongside the
workflow. Until marks are in place, the CI test job runs all Tier 1 and
Tier 2 tests together under `-m "not live"`.

## What CI must enforce

Four hard gates. A failed gate fails the build.

### Gate 1 — pylint

- Standard: 10.00/10 against `.pylintrc`, or a documented suppression at the
  point of occurrence
- Invocation: `pylint nyc_cab/` (package-directory, not file-by-file — the
  file-by-file invocation triggers spurious `invalid-name` warnings on
  `__init__.py` files because pylint cannot derive a module name from the
  bare basename)
- Failure condition: any non-zero exit code

### Gate 2 — pytest

- Standard: all non-live tests pass
- Invocation: `pytest -m "not live"` from the repo root
- Java is required: the test suite includes real Spark-backed module and
  integration tests. Install Java via `actions/setup-java@v4` before running
  pytest in CI.
- Failure condition: any test failure or collection error

### Gate 3 — coverage

- Current floor: ≥90% branch coverage
- Destination: 100% at alpha milestone. The stub-testing discipline (every
  `NotImplementedError` stub has a `pytest.raises` test) makes 100% achievable
  in steady state. The 90% floor is intentional wiggle room during active
  development, not the goal.
- Invocation: `pytest -m "not live" --cov=nyc_cab --cov-config=.coveragerc
  --cov-branch --cov-fail-under=90`
- Failure condition: branch coverage below 90%

### Gate 4 — import smoke test

- Standard: package imports cleanly from a runtime-only install
- Invocation: `pip install -e .` (no `[dev]` extras), then
  `python -c "import nyc_cab; print('OK')"`
- Catches missing runtime dependencies, broken `__init__.py` files, and
  circular imports that the test suite might mask because it installs dev
  extras
- Failure condition: any non-zero exit

## Workflow architecture

Single workflow file at `.github/workflows/ci.yml`.

### Trigger

```yaml
on:
  push:
  pull_request:
```

Every branch, every push, every PR. Do not restrict to `main` — the WIP
development model assumes active branch work and CI must run there.

### Job structure

Three independent jobs running in parallel: `lint`, `test`, `import`. Each
has its own checkout and install steps; no shared state between jobs.

As mark-based tier separation matures, `test` splits into `test-fast`
(Tier 1, no Java) and `test-spark` (Tier 2, Java required). The current
single `test` job runs all Tier 1 and Tier 2 tests together.

#### Job: lint

1. `actions/checkout@v4`
2. `actions/setup-python@v5` with Python 3.11
3. `actions/cache@v4` keyed on `pyproject.toml` + `requirements.lock.txt` hash
4. `pip install -e .[dev]` — requires `pylint` in dev extras (see prerequisites)
5. `pylint nyc_cab/`

#### Job: test

1. `actions/checkout@v4`
2. `actions/setup-python@v5` with Python 3.11
3. `actions/setup-java@v4` with Temurin JDK 17 — required for PySpark
4. `actions/cache@v4` (same key as lint)
5. `pip install -e .[dev]` — requires `pytest-cov` in dev extras (see prerequisites)
6. `pytest -m "not live" --cov=nyc_cab --cov-config=.coveragerc --cov-branch --cov-fail-under=90`

Do NOT install Apache Airflow. Airflow's constrained dependency set conflicts
with the core Spark environment and must remain in an isolated environment.

#### Job: import

1. `actions/checkout@v4`
2. `actions/setup-python@v5` with Python 3.11
3. `actions/cache@v4` (same key)
4. `pip install -e .` — **no `[dev]` extras** by design; this job verifies
   the runtime install is self-contained
5. `python -c "import nyc_cab; print('OK')"`

### Caching

`actions/cache@v4`, path `~/.cache/pip`, key
`pip-${{ hashFiles('pyproject.toml', 'requirements.lock.txt') }}`.

Do not cache the editable install or virtual environment directory — pip-cached
wheels reinstall cleanly in under five seconds.

## What CI must not do at this stage

- No mypy, ruff, bandit, or additional static analysis. Pylint-only.
- No Python version matrix. Single 3.11 target until alpha.
- No PyPI publishing, wheel building, or release artifacts.
- No Sphinx docs build job. Documentation CI lands at alpha alongside RTD.
- No Airflow DAG validation. Airflow has incompatible dependencies; defer
  to a separate workflow when DAGs become non-trivial.
- No README badges until the workflow has been observed green on a clean tree.

## Prerequisites (must land before workflow can succeed)

### `pyproject.toml` changes (prerequisite commit)

1. Add `pytest-cov` to `[project.optional-dependencies] dev`. The test job
   requires it; it is currently absent.
2. Add `pylint` to `[project.optional-dependencies] dev`. The lint job
   requires it; it is currently absent.
3. Remove `python-dotenv` from `[project] dependencies`. Design log decision 4
   explicitly excludes implicit `.env` loading; the dependency contradicts
   the design log and is unused.

### `pytest.ini` changes (prerequisite commit, same or adjacent)

4. Register `unit` and `spark` markers so `--strict-markers` does not reject
   them when test annotation begins:

   ```ini
   markers =
       live: marks tests that require network access to external services
       spark: marks tests that require a live PySpark session
       unit: marks pure-Python tests with no Spark or I/O dependency
   ```

### Documentation alignment (recommended alongside the workflow)

5. The README's Testing Standards section describes the tier structure in
   prose. The design log should record the four-tier architecture and the
   mark-based separation plan as a numbered decision, cross-referenced from
   the README.

## Style requirements for the generated YAML

- Double-quoted strings throughout
- Named jobs and named steps; no positional or unnamed steps
- Latest stable major versions: `actions/checkout@v4`, `actions/setup-python@v5`,
  `actions/cache@v4`, `actions/setup-java@v4`
- Comment any non-obvious step (why Java, why no dev extras in import job,
  why `-m "not live"`)
- Target: under 80 lines including comments

## Verification after deployment

1. Push to a feature branch; observe all three jobs run independently
2. Verify all three pass on a clean tree
3. Introduce a deliberate failure in each gate (lint warning, failing test,
   broken import) and verify the matching job catches it
4. Revert and verify gates clear
5. Add README badges only after all four failure modes have been confirmed
