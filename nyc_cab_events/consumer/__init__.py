# pylint: disable=line-too-long
# Allow long lines for mermaid, it does not like embedded newlines.
"""
Define the trip-completed event consumer.

The consumer reads ``trip.completed.v1`` from Kafka, aggregates events by
``(cab_type, year, month, hour)`` — the hour is event-time from
``tpep_pickup_datetime``, not processing time (design log decision 33) —
and writes upserts into the Postgres ``trip_completed_hourly`` table.

The consumer is idempotent. Two equally valid runs:

- Cold start: read from offset 0, fully aggregate, write upserts. Postgres
  upserts on the primary key ``(cab_type, year, month, hour)`` so writing
  the same aggregate twice converges to the same row state.
- Resume: read from the committed offset; the upsert semantics still hold.

Idempotency comes from the sink's ``ON CONFLICT DO UPDATE`` clause, not from
in-consumer deduplication. The consumer does not need to remember which
events it has seen.

This module exists at scaffolding stage; the heavy bits —
``confluent_kafka.Consumer`` polling loop, deserialization, the in-memory
aggregator, the Postgres upsert call — are ``NotImplementedError`` stubs
covered by stub tests per design log decision 25.

Consumer Flow
-------------

.. mermaid::

    sequenceDiagram
        autonumber
        participant Caller as CLI / Airflow
        participant Consumer as consumer.trip_completed
        participant Kafka as confluent_kafka.Consumer
        participant Aggregator as in-memory aggregator
        participant Sink as sink.postgres

        Caller->>Consumer: consume_and_aggregate(consumer_config, sink_config)
        Consumer->>Kafka: subscribe([trip.completed.v1])
        loop poll until idle (or max_messages reached)
            Consumer->>Kafka: poll(timeout)
            Kafka-->>Consumer: ConsumerRecord (event payload + key)
            Consumer->>Consumer: deserialize TripCompleted
            Consumer->>Aggregator: increment[(cab_type, year, month, hour)]
        end
        Consumer->>Sink: upsert_hourly_counts(buckets)
        Sink-->>Consumer: rows_affected
        Consumer->>Kafka: commit()
        Consumer-->>Caller: TripCompletedConsumerResult

Class Relationships
-------------------

.. mermaid::

    classDiagram

        dataclass <|-- TripCompletedConsumerConfig
        _Validated <|-- TripCompletedConsumerConfig

        dataclass <|-- TripCompletedConsumerResult
        _Validated <|-- TripCompletedConsumerResult

        class TripCompletedConsumerConfig {
            <<immutable>>
            string bootstrap_servers
            string group_id
            string topic
            integer poll_timeout_seconds
            integer max_idle_polls
        }

        class TripCompletedConsumerResult {
            <<immutable>>
            integer events_consumed
            integer hourly_buckets_written
        }

        TripCompletedConsumerResult --> TripCompletedConsumerConfig : run under
"""
