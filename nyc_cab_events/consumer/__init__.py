# pylint: disable=line-too-long
# Allow long lines for mermaid, it does not like embedded newlines.
"""
Define the trip-completed event consumer.

The consumer implements bounded full-slice replay per design log
decision 42, not incremental resume. Each invocation processes one
``(cab_type, year, month)`` slice by:

1. Capturing Kafka end-of-partition offsets at start of run via
   :meth:`Consumer.get_watermark_offsets` — the deterministic replay
   window. Idle-timeout polling is rejected (decision 42) because a
   slow broker could produce different inputs from the same topic
   state across runs.
2. Polling forward until each partition reaches its captured end
   offset, then stopping.
3. Deserializing each message and filtering to the requested slice in
   application code.
4. Deduplicating in memory on the deterministic ``event_id`` (decision
   36). Producer reruns emit byte-identical events with identical ids;
   the seen-set collapses them in O(1) per event.
5. Accumulating hourly counts keyed on ``(cab_type, year, month,
   hour)``; the hour is event-time from ``tpep_pickup_datetime``, not
   processing time (decision 33).
6. Writing the complete slice through
   :func:`~nyc_cab_events.sink.postgres.upsert_hourly_counts`, whose
   overwrite-on-conflict semantics make the consumer's count the
   single source of truth for the row.

The contract is: read the complete slice or write nothing. Crashes are
recoverable by re-running the slice; mid-slice resume is not supported
and the consumer does not commit Kafka offsets back to the broker.

This module is at scaffolding stage; ``consume_and_aggregate`` is a
``NotImplementedError`` stub covered by a stub test per decision 25,
and the heavy imports (:mod:`confluent_kafka`, :mod:`psycopg`) live
inside the stub. They move to module level when the stub is filled in.

Consumer Flow
-------------

.. mermaid::

    sequenceDiagram
        autonumber
        participant Caller as CLI / Airflow
        participant Consumer as consumer.trip_completed
        participant Kafka as confluent_kafka.Consumer
        participant SeenSet as in-memory seen-set
        participant Aggregator as in-memory aggregator
        participant Sink as sink.postgres

        Caller->>Consumer: consume_and_aggregate(consumer_config, sink_config, cab_type, year, month)
        Consumer->>Kafka: subscribe([trip.completed.v1])
        Consumer->>Kafka: get_watermark_offsets() per partition
        Kafka-->>Consumer: end-offset map (the replay window)
        loop poll until each partition reaches captured end offset
            Consumer->>Kafka: poll(poll_timeout_seconds)
            Kafka-->>Consumer: ConsumerRecord (event payload + key)
            Consumer->>Consumer: deserialize TripCompleted, filter to slice
            Consumer->>SeenSet: contains(event_id)?
            alt event_id new
                SeenSet-->>Consumer: false
                Consumer->>SeenSet: add(event_id)
                Consumer->>Aggregator: increment[(cab_type, year, month, hour)]
            else event_id seen
                SeenSet-->>Consumer: true (skip)
            end
        end
        Consumer->>Sink: upsert_hourly_counts(buckets)
        Sink-->>Consumer: rows_affected
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
        }

        class TripCompletedConsumerResult {
            <<immutable>>
            string cab_type
            integer year
            integer month
            integer events_read
            integer events_in_slice
            integer events_unique
            integer hourly_buckets_written
        }

        TripCompletedConsumerResult --> TripCompletedConsumerConfig : run under
"""
