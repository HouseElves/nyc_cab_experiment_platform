# pylint: disable=line-too-long
# Allow long lines for mermaid, it does not like embedded newlines.
"""
Define the trip-completed event consumer.

The consumer implements bounded full-slice replay per design log
decision 42, not incremental resume. Each invocation processes one
``(cab_type, year, month)`` slice by:

1. Discovering the topic's partition set via
   :meth:`Consumer.list_topics`, then attaching to all partitions
   from offset 0 via :meth:`Consumer.assign` (design log decision
   44). ``subscribe`` is not used; bounded replay is a partition/
   offset operation, not a consumer-group operation, and the v1
   consumer does not commit offsets back to the broker.
2. Capturing Kafka end-of-partition offsets at start of run via
   :meth:`Consumer.get_watermark_offsets` — the deterministic replay
   window. Idle-timeout polling is rejected (decision 42) because a
   slow broker could produce different inputs from the same topic
   state across runs.
3. Polling forward until each partition reaches its captured end
   offset, then stopping. Empty partitions (high-water at offset 0)
   are skipped at window capture time.
4. Classifying each message via ``_classify_message``, which returns
   a ``_ConsumedMessage(disposition, event)`` discriminator (design
   log decision 45). The three dispositions —
   ``IN_SLICE``/``OUT_OF_SLICE``/``INVALID`` — partition
   ``events_read``.
5. Deduplicating in-slice events in memory on the deterministic
   ``event_id`` (decision 36). Producer reruns emit byte-identical
   events with identical ids; the seen-set collapses them in O(1)
   per event.
6. Accumulating hourly counts keyed on ``(cab_type, year, month,
   hour)``; the hour is event-time from ``tpep_pickup_datetime``,
   not processing time (decision 33).
7. Writing the complete slice through
   :func:`~nyc_cab_events.sink.postgres.upsert_hourly_counts`, whose
   overwrite-on-conflict semantics make the consumer's count the
   single source of truth for the row.

The contract is: read the complete slice or write nothing. Crashes are
recoverable by re-running the slice; mid-slice resume is not supported
and the consumer does not commit Kafka offsets back to the broker.

Counter invariants
------------------

Six run-level counters partition the polled message stream:

- ``events_read = events_invalid + events_out_of_slice + events_in_slice``
- ``events_in_slice = events_duplicate + events_unique``
- ``events_unique == sum(bucket.event_count for bucket in buckets)``
- ``hourly_buckets_written == len(buckets)``

Four are surfaced on :class:`TripCompletedConsumerResult` (the four
that compose the result's structural invariants); ``events_invalid``
and ``events_duplicate`` are driver locals, logged at INFO at run
end. Two of the four bullets above are structurally enforced on the
result dataclass; the other two are enforced by the driver at the
sink-call boundary with explicit ``raise RuntimeError`` — see design
log decision 45 for the rationale and promotion triggers.

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
        Consumer->>Kafka: list_topics(topic) -> partition set
        Consumer->>Kafka: assign([TopicPartition(topic, p, 0) for p in partitions])
        Consumer->>Kafka: get_watermark_offsets() per partition
        Kafka-->>Consumer: end-offset map (the replay window)
        loop poll until each partition reaches captured end offset
            Consumer->>Kafka: poll(poll_timeout_seconds)
            Kafka-->>Consumer: ConsumerRecord (event payload + key)
            Consumer->>Consumer: _classify_message -> _ConsumedMessage(disposition, event)
            alt disposition == IN_SLICE
                Consumer->>SeenSet: contains(event_id)?
                alt event_id new
                    SeenSet-->>Consumer: false
                    Consumer->>SeenSet: add(event_id)
                    Consumer->>Aggregator: increment[(cab_type, year, month, hour)]
                else event_id seen
                    SeenSet-->>Consumer: true (events_duplicate += 1)
                end
            else disposition == OUT_OF_SLICE
                Consumer->>Consumer: events_out_of_slice += 1
            else disposition == INVALID
                Consumer->>Consumer: events_invalid += 1
            end
        end
        Consumer->>Consumer: _aggregator_to_buckets, verify integrity invariants
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
