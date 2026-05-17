# pylint: disable=line-too-long
# Allow long lines for mermaid, it does not like embedded newlines.
"""
Define the trip-completed event producer.

The producer reads one Silver accepted Parquet partition for a
``(cab_type, year, month)`` slice, maps each accepted row to a
:class:`~nyc_cab_events.contracts.events.TripCompleted` event, and
publishes to Kafka. Rows that fail event-contract validation are routed
to the quarantine topic instead of crashing the run; the reconciliation
invariant ``silver_read_count == events_emitted + events_quarantined``
is enforced structurally on the producer result.

The producer is deterministic: the same Silver row always derives the
same ``event_id`` (design log decision 36, owned by
:mod:`nyc_cab_events.contracts.events`). This makes re-runs of the
producer convergent at the consumer side, where bounded full-slice
replay deduplicates on ``event_id`` (decision 42).

This module exposes:

- :class:`TripCompletedProducerConfig` — validated config dataclass
- :class:`TripCompletedProducerResult` — validated result dataclass
  with the reconciliation invariant
- :func:`ensure_topics` — create the primary and quarantine topics
  idempotently on the broker (auto-topic-creation is intentionally off
  in the docker-compose setup; this is the explicit replacement)
- :func:`produce_trip_completed_events` — the driver

Producer Flow
-------------

.. mermaid::

    sequenceDiagram
        autonumber
        participant Caller as CLI / Airflow
        participant Producer as producer.trip_completed
        participant Admin as confluent_kafka.AdminClient
        participant Contract as contracts.events
        participant Spark as SparkSession
        participant Kafka as confluent_kafka.Producer
        participant Topic as trip.completed.v1
        participant Quarantine as trip.completed.v1.invalid

        Caller->>Producer: ensure_topics(producer_config)
        Producer->>Admin: create_topics([primary, quarantine])
        Admin-->>Producer: ok (or "already exists")

        Caller->>Producer: produce_trip_completed_events(spark, silver_path, config, cab_type, year, month)
        Producer->>Spark: read.parquet(silver_partition_path)
        Spark-->>Producer: DataFrame (accepted rows)

        loop one event per Silver row (toLocalIterator)
            Producer->>Contract: derive_event_id(augmented_row)
            Contract-->>Producer: deterministic event_id
            Producer->>Contract: TripCompleted.create_validated(...)
            alt event constructed cleanly
                Contract-->>Producer: TripCompleted instance
                Producer->>Contract: event_key(event)
                Contract-->>Producer: cab_type/YYYY/MM/HH
                Producer->>Kafka: produce(config.topic, key=event_key, value=to_json(event), headers={schema_version})
                Kafka-->>Topic: emit
            else KeyError or InvalidRequestError
                Producer->>Producer: build quarantine envelope (raw row + slice metadata)
                Producer->>Kafka: produce(config.quarantine_topic, key=cab_type/YYYY/MM, value=raw_json, headers={rejection_reason, quarantined_at, violations})
                Kafka-->>Quarantine: emit
            end
        end

        Producer->>Kafka: flush()
        Producer-->>Caller: TripCompletedProducerResult (counts, paths)

Class Relationships
-------------------

.. mermaid::

    classDiagram

        dataclass <|-- TripCompletedProducerConfig
        _Validated <|-- TripCompletedProducerConfig

        dataclass <|-- TripCompletedProducerResult
        _Validated <|-- TripCompletedProducerResult

        class TripCompletedProducerConfig {
            <<immutable>>
            string bootstrap_servers
            string topic
            string quarantine_topic
        }

        class TripCompletedProducerResult {
            <<immutable>>
            string cab_type
            integer year
            integer month
            Path silver_partition_path
            integer silver_read_count
            integer events_emitted
            integer events_quarantined
        }

        TripCompletedProducerResult --> TripCompletedProducerConfig : run under
"""
