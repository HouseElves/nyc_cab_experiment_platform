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

#### Rationale

This hierarchy provides the necessary granularity for operational error
handling, such as distinguishing between a missing variable and a malformed
value, without speculative branching. Concrete exceptions include structured
metadata such as `variable` and `value` to support precise CLI and
orchestration diagnostics.

### 3. Principled Configuration Factoring

The platform separates configuration into distinct layers based on the
lifecycle and stability of the data:

- `RuntimeConfig` in `nyc_cab/config.py` owns stable, application-wide runtime
  context such as deployment environment, filesystem layout, and
  application-wide log level
- `SparkConfig` in `nyc_cab/spark_config.py` owns engine-specific execution
  settings such as Spark master and application name
- A future typed ingestion request will own invocation-specific parameters such
  as dataset selection, time period, and source path

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

## Architectural Guardrails

To prevent the architecture from drifting into monolithic design:

- Subsystems must not inject their settings into `RuntimeConfig`. If a
  subsystem requires unique configuration, it must be factored into its own
  module, as with `SparkConfig`.
- Modules must not reach into `RuntimeConfig` to find settings that belong to a
  typed ingestion request or an engine-specific configuration object.
- Orchestration layers such as the CLI and Airflow are responsible for
  composing the necessary configuration objects at the edge, so application
  code remains agnostic to how configuration was sourced.
- The name `RuntimeConfig` is intentional. It signals that the object captures
  runtime context rather than every possible configuration concern in the
  platform.
  