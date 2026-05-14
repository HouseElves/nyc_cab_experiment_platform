# pylint: disable=line-too-long
# Allow long lines for mermaid, it does not like embedded newlines.
"""
Define the Postgres aggregate sink for trip-completed events.

The sink owns:

- DDL for the ``trip_completed_hourly`` table.
- Upsert semantics on the natural key ``(cab_type, year, month, hour)``.
- The reconciliation query that compares Postgres hourly counts (summed by
  month) against Silver ``accepted_count`` for the same partition.

Postgres was chosen as the aggregate sink (design log decision 34) as a
deliberate tech variation from the Parquet-on-disk pattern used by Bronze
and Silver, so the platform exercises a second sink class. Decision 34 also
records the rejection of alternatives (writing aggregates back to Parquet,
using SQLite for the sink).

trip_completed_hourly Table
---------------------------

.. mermaid::

    erDiagram
        trip_completed_hourly {
            text cab_type PK
            int  year PK
            int  month PK
            int  hour PK
            bigint event_count
            timestamptz last_updated_at
        }

The primary key matches the event-time aggregation key. ``last_updated_at``
records the wall-clock instant of the most recent upsert for the row, which
makes it possible to reason about reconciliation lag without consulting
Kafka offsets.
"""
