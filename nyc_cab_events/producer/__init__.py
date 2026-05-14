# pylint: disable=line-too-long
# Allow long lines for mermaid, it does not like embedded newlines.
"""
Define the trip-completed event producer.

The producer reads one Silver accepted Parquet partition for a
``(cab_type, year, month)`` slice, maps each accepted row to a
:class:`~nyc_cab_events.contracts.events.TripCompleted` event, and publishes
to Kafka. Rows that fail event-contract validation are routed to the
quarantine topic instead of crashing the run; the reconciliation invariant
``silver_read_count == events_emitted + events_quarantined`` is enforced
structurally on the producer result.

The producer is deterministic: the same Silver row always derives the same
``event_id``. This makes re-runs of the producer idempotent at the
key-equality level (Kafka itself does not deduplicate, but downstream
consumers can).

This module exists at scaffolding stage; the heavy bits — Spark reading,
``confluent_kafka.Producer`` calls, ``event_id`` hashing — are
``NotImplementedError`` stubs covered by stub tests per design log decision 25.

Producer Flow
-------------

.. mermaid::

    sequenceDiagram
        autonumber
        participant Caller as CLI / Airflow
        participant Producer as producer.trip_completed
        participant Contract as contracts.events
        participant Spark as SparkSession
        participant Kafka as confluent_kafka.Producer
        participant Topic as trip.completed.v1
        participant Quarantine as trip.completed.v1.invalid

        Caller->>Producer: produce_trip_completed_events(spark, silver_path, config)
        Producer->>Spark: read.parquet(silver_partition_path)
        Spark-->>Producer: DataFrame (accepted rows)

        loop one event per Silver row
            Producer->>Producer: derive_event_id(row)
            Producer->>Contract: TripCompleted.create_validated(...)
            alt event constructed cleanly
                Contract-->>Producer: TripCompleted instance
                Producer->>Kafka: produce(topic, key=event_id, value=event_payload)
                Kafka-->>Topic: emit
            else InvalidRequestError
                Contract-->>Producer: raise InvalidRequestError
                Producer->>Contract: quarantine_topic_for(INVALID_CONSTRUCTION)
                Contract-->>Producer: quarantine_topic
                Producer->>Kafka: produce(quarantine_topic, key=event_id, value=raw_payload)
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
            integer silver_read_count
            integer events_emitted
            integer events_quarantined
        }

        TripCompletedProducerResult --> TripCompletedProducerConfig : run under
"""
