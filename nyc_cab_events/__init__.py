# pylint: disable=line-too-long
# Allow long lines for mermaid, it does not like embedded newlines.
"""
Define the NYC Cab events package.

This package implements the batch-event bridge described in design log
decision 31. The Silver accepted layer is the source of truth; this package
generates deterministic events from it, ships them through Kafka, aggregates
them into a Postgres sink, and reconciles the aggregate against Silver's
own batch counts.

For v1 there is exactly one event type, ``trip.completed.v1``. Additional
event types (``trip.started.v1``, ``payment.recorded.v1``) can follow the
same shape once this one is end-to-end.

Subpackage layout:

- ``contracts``  — event dataclasses and topic routing. No I/O. No Spark.
                   Parallels ``nyc_cab.contracts``.
- ``producer``   — reads a Silver accepted partition and emits one event per
                   accepted row. Skipped rows are routed to a quarantine topic.
- ``consumer``   — reads ``trip.completed.v1``, aggregates by
                   ``(cab_type, year, month, hour)``, writes to Postgres.
                   Idempotent; safe to re-run.
- ``sink``       — Postgres DDL, upsert, and reconciliation queries.

Data flow at a glance:

.. mermaid::

    flowchart LR
        Silver[Silver accepted Parquet<br/>cab_type/year/month] --> Producer
        Producer -->|trip.completed.v1| Kafka[(Kafka)]
        Producer -.->|trip.completed.v1.invalid| Quarantine[(Quarantine topic)]
        Kafka --> Consumer
        Consumer --> Sink[(Postgres<br/>trip_completed_hourly)]
        Silver -. accepted_count .-> Reconcile{Reconcile}
        Sink -. sum of hourly counts .-> Reconcile

Architectural guardrails for this package:

- ``contracts/`` performs no I/O and imports no Spark, Kafka, or Postgres
  symbols. The same guardrail that protects ``nyc_cab.contracts`` applies
  here.
- Duplication between ``nyc_cab`` and ``nyc_cab_events`` is tolerated until
  design pressure forces a common vocabulary package. Do not prematurely
  abstract (design log decision 28 in spirit).
- The Kafka client is ``confluent-kafka-python`` (decision 32). Other Python
  Kafka libraries must not creep in.
- The aggregate sink is Postgres (decision 34). This is a deliberate tech
  variation from the Parquet-on-disk pattern used by Bronze and Silver, to
  exercise a second sink class in the platform.
"""
