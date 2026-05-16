# pylint: disable=line-too-long
# Allow long lines for mermaid, it does not like embedded newlines.
"""
Define the trip-completed event consumer.

The consumer reads ``trip.completed.v1`` from Kafka, aggregates events by
``(cab_type, year, month, hour)`` — the hour is event-time from
``tpep_pickup_datetime``, not processing time (design log decision 33) —
and writes upserts into the Postgres ``trip_completed_hourly`` table.

The consumer is convergent within a single replay window: re-running the
batch consumer against the same Kafka topic state produces the same
final aggregate. Two equally valid runs:

- Cold start: read from offset 0, aggregate, write upserts. The upsert
  on the primary key ``(cab_type, year, month, hour)`` overwrites the
  row's count with the new full count.
- Resume: read from the committed offset; the partial new events get
  aggregated and merged with the existing row count via the same
  upsert.

End-to-end exactly-once is **not** automatic from the upsert alone. If
the producer reruns and emits duplicate events (deterministic
``event_id`` means the same event is reproduced byte-identical), a
fresh consumer group reading the topic from offset 0 will count each
duplicate, and the upsert will faithfully write the doubled count. The
consumer implementation is responsible for per-event deduplication
keyed on ``event_id`` when cross-replay safety matters; the mechanism
(in-memory seen-set, persistent dedup table, or bounded-window
replacement) is documented in design log decision 34.

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
