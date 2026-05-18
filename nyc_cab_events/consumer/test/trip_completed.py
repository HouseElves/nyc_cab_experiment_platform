# pylint: disable=redefined-outer-name,duplicate-code,too-many-lines
# duplicate-code: the silver-flavored fixtures echo their producer-side
# cousins (decision 28: tolerate duplication until shared vocabulary
# is justified).
# too-many-lines: comprehensive dataclass + helper + driver coverage
# clusters naturally in one module; splitting along arbitrary lines
# obscures the lint-as-documentation property of co-located tests.
"""Tests for :mod:`nyc_cab_events.consumer.trip_completed`.

This file mixes unit-tier tests (the bulk) with no integration-tier
tests; there is intentionally no module-level
``pytestmark = pytest.mark.unit``. Each test is marked individually so
that future integration-tier additions in this module do not inherit
the unit mark and pollute ``pytest -m unit``. See the matching pattern
in :mod:`nyc_cab_events.producer.test.trip_completed`.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from datetime import datetime, timezone
from typing import Any, Iterable

import pytest

from nyc_cab.exceptions import InvalidRequestError
from nyc_cab_events.contracts.events import (
    SCHEMA_VERSION,
    TRIP_COMPLETED_TOPIC,
    TripCompleted,
    to_json,
)
from nyc_cab_events.sink.postgres import HourlyBucket, PostgresSinkConfig
from nyc_cab_events.consumer.trip_completed import (
    TripCompletedConsumerConfig,
    TripCompletedConsumerResult,
    _ConsumedMessage,
    _Disposition,
    _aggregator_to_buckets,
    _classify_message,
    consume_and_aggregate,
)


# --- TripCompletedConsumerConfig: happy paths -------------------------------


@pytest.mark.unit
def test_config_happy_path() -> None:
    """A well-formed config constructs cleanly."""
    config = TripCompletedConsumerConfig.create_validated(
        "localhost:9092", "nyc-cab-events-test", TRIP_COMPLETED_TOPIC, 5,
    )
    assert config.bootstrap_servers == "localhost:9092"
    assert config.group_id == "nyc-cab-events-test"
    assert config.topic == TRIP_COMPLETED_TOPIC
    assert config.poll_timeout_seconds == 5


@pytest.mark.unit
def test_config_defaults() -> None:
    """Direct construction with defaults targets the v1 topic with a sane poll timeout."""
    config = TripCompletedConsumerConfig(
        bootstrap_servers="localhost:9092",
        group_id="g",
    )
    assert config.topic == TRIP_COMPLETED_TOPIC
    assert config.poll_timeout_seconds == 5


# --- TripCompletedConsumerConfig: type rejections ---------------------------


@pytest.mark.unit
def test_config_rejects_non_string_bootstrap() -> None:
    """``bootstrap_servers`` must be a string."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerConfig.create_validated(0, "g", "t", 5)
    names = [v[0] for v in info.value.violations]
    assert "bootstrap_servers" in names


@pytest.mark.unit
def test_config_rejects_non_string_group_id() -> None:
    """``group_id`` must be a string."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerConfig.create_validated("localhost:9092", 0, "t", 5)
    names = [v[0] for v in info.value.violations]
    assert "group_id" in names


@pytest.mark.unit
def test_config_rejects_bool_poll_timeout() -> None:
    """``poll_timeout_seconds`` rejects bool despite int compatibility."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerConfig.create_validated("localhost:9092", "g", "t", True)
    assert ("poll_timeout_seconds", True) in info.value.violations


# --- TripCompletedConsumerConfig: structural rejections ---------------------


@pytest.mark.unit
def test_config_rejects_blank_bootstrap_servers() -> None:
    """Blank bootstrap_servers violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerConfig.create_validated("   ", "g", "t", 5)
    assert ("bootstrap_servers", "   ") in info.value.violations


@pytest.mark.unit
def test_config_rejects_blank_group_id() -> None:
    """Blank group_id violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerConfig.create_validated("localhost:9092", "", "t", 5)
    assert ("group_id", "") in info.value.violations


@pytest.mark.unit
def test_config_rejects_blank_topic() -> None:
    """Blank topic violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerConfig.create_validated("localhost:9092", "g", "", 5)
    assert ("topic", "") in info.value.violations


@pytest.mark.unit
def test_config_rejects_zero_poll_timeout() -> None:
    """``poll_timeout_seconds`` must be positive."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerConfig.create_validated("localhost:9092", "g", "t", 0)
    assert ("poll_timeout_seconds", 0) in info.value.violations


@pytest.mark.unit
def test_config_is_frozen() -> None:
    """``TripCompletedConsumerConfig`` rejects attribute mutation."""
    config = TripCompletedConsumerConfig.create_validated("localhost:9092", "g", "t", 5)
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.group_id = "other"  # type: ignore[misc]


# --- TripCompletedConsumerResult: happy paths -------------------------------


@pytest.mark.unit
def test_result_happy_path() -> None:
    """A well-formed result constructs cleanly with the monotonic counters in order."""
    result = TripCompletedConsumerResult.create_validated(
        "yellow", 2023, 1, 1000, 950, 900, 24,
    )
    assert result.cab_type == "yellow"
    assert result.year == 2023
    assert result.month == 1
    assert result.events_read == 1000
    assert result.events_in_slice == 950
    assert result.events_unique == 900
    assert result.hourly_buckets_written == 24


@pytest.mark.unit
def test_result_zero_run() -> None:
    """A run that consumed no in-slice events is valid (empty replay window)."""
    result = TripCompletedConsumerResult.create_validated(
        "yellow", 2023, 1, 0, 0, 0, 0,
    )
    assert result.events_read == 0


@pytest.mark.unit
def test_result_allows_all_events_unique_and_one_per_bucket() -> None:
    """A run where every unique event maps to its own hour bucket is valid."""
    result = TripCompletedConsumerResult.create_validated(
        "yellow", 2023, 1, 24, 24, 24, 24,
    )
    assert result.hourly_buckets_written == result.events_unique


# --- TripCompletedConsumerResult: type rejections ---------------------------


@pytest.mark.unit
def test_result_rejects_bool_year() -> None:
    """``year`` rejects bool despite int compatibility."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerResult.create_validated(
            "yellow", True, 1, 0, 0, 0, 0,
        )
    assert ("year", True) in info.value.violations


@pytest.mark.unit
def test_result_rejects_bool_events_read() -> None:
    """``events_read`` rejects bool despite int compatibility."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerResult.create_validated(
            "yellow", 2023, 1, True, 0, 0, 0,
        )
    assert ("events_read", True) in info.value.violations


# --- TripCompletedConsumerResult: structural rejections ---------------------


@pytest.mark.unit
def test_result_rejects_blank_cab_type() -> None:
    """Blank ``cab_type`` violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerResult.create_validated(
            "  ", 2023, 1, 0, 0, 0, 0,
        )
    names = [v[0] for v in info.value.violations]
    assert "cab_type" in names


@pytest.mark.unit
def test_result_rejects_negative_events_read() -> None:
    """Negative ``events_read`` violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerResult.create_validated(
            "yellow", 2023, 1, -1, 0, 0, 0,
        )
    assert ("events_read", -1) in info.value.violations


@pytest.mark.unit
def test_result_rejects_in_slice_exceeding_read() -> None:
    """``events_in_slice`` cannot exceed ``events_read`` — filtering is monotonic."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerResult.create_validated(
            "yellow", 2023, 1, 10, 11, 0, 0,
        )
    names = [v[0] for v in info.value.violations]
    assert "events_in_slice" in names


@pytest.mark.unit
def test_result_rejects_unique_exceeding_in_slice() -> None:
    """``events_unique`` cannot exceed ``events_in_slice`` — dedup is monotonic."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerResult.create_validated(
            "yellow", 2023, 1, 100, 50, 51, 0,
        )
    names = [v[0] for v in info.value.violations]
    assert "events_unique" in names


@pytest.mark.unit
def test_result_rejects_buckets_exceeding_unique() -> None:
    """``hourly_buckets_written`` cannot exceed ``events_unique``."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedConsumerResult.create_validated(
            "yellow", 2023, 1, 100, 100, 5, 6,
        )
    names = [v[0] for v in info.value.violations]
    assert "hourly_buckets_written" in names


@pytest.mark.unit
def test_result_is_frozen() -> None:
    """``TripCompletedConsumerResult`` rejects attribute mutation."""
    result = TripCompletedConsumerResult.create_validated(
        "yellow", 2023, 1, 0, 0, 0, 0,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.events_read = 1  # type: ignore[misc]


# --- Helper fixtures and a minimal Kafka message stand-in -------------------


def _make_event(
    # pylint: disable=too-many-arguments
    # Six keyword-only arguments correspond directly to the six event
    # fields that tests need to vary (slice metadata plus event_id and
    # produced_at). Bundling them would force per-test boilerplate
    # without reducing real complexity.
    *,
    cab_type: str = "yellow",
    year: int = 2023,
    month: int = 1,
    hour: int = 14,
    event_id: str = "abcdef0123456789",
    produced_at: datetime | None = None,
) -> TripCompleted:
    """Build a valid ``TripCompleted`` for tests with overridable slice fields."""
    return TripCompleted.create_validated(
        event_id,
        SCHEMA_VERSION,
        cab_type,
        year,
        month,
        hour,
        3.5,
        18.0,
        2,
        produced_at or datetime(2025, 5, 14, 12, 0, 0, tzinfo=timezone.utc),
    )


class _FakeMessage:
    """Minimal stand-in for ``confluent_kafka.Message``.

    Implements the four methods :func:`_classify_message` and the
    driver loop touch: :meth:`value`, :meth:`error`, :meth:`partition`,
    :meth:`offset`. Production messages also expose key/headers but the
    consumer does not read them — the partition routing has already
    happened by the time we see the message.
    """

    def __init__(
        self,
        value: bytes | None,
        *,
        error: Any = None,
        partition: int = 0,
        offset: int = 0,
    ) -> None:
        self._value = value
        self._error = error
        self._partition = partition
        self._offset = offset

    def value(self) -> bytes | None:
        """Return the JSON payload bytes, or None for malformed messages."""
        return self._value

    def error(self) -> Any:
        """Return the message-level error (None for normal records)."""
        return self._error

    def partition(self) -> int:
        """Return the partition this record was polled from."""
        return self._partition

    def offset(self) -> int:
        """Return the offset within the partition."""
        return self._offset


def _msg_for(event: TripCompleted, *, partition: int = 0, offset: int = 0) -> _FakeMessage:
    """Serialize ``event`` and wrap it in a ``_FakeMessage``."""
    return _FakeMessage(to_json(event).encode("utf-8"), partition=partition, offset=offset)


# --- _classify_message ------------------------------------------------------


@pytest.mark.unit
def test_classify_message_in_slice_returns_event() -> None:
    """A valid in-slice payload is classified IN_SLICE with the event attached."""
    event = _make_event()
    msg = _msg_for(event)
    consumed = _classify_message(msg, "yellow", 2023, 1)
    assert consumed.disposition is _Disposition.IN_SLICE
    assert consumed.event is not None
    assert consumed.event.event_id == event.event_id


@pytest.mark.unit
def test_classify_message_wrong_cab_type_is_out_of_slice() -> None:
    """A payload with a different cab_type is OUT_OF_SLICE."""
    msg = _msg_for(_make_event(cab_type="green"))
    consumed = _classify_message(msg, "yellow", 2023, 1)
    assert consumed.disposition is _Disposition.OUT_OF_SLICE
    assert consumed.event is None


@pytest.mark.unit
def test_classify_message_wrong_year_is_out_of_slice() -> None:
    """A payload with a different year is OUT_OF_SLICE."""
    msg = _msg_for(_make_event(year=2024))
    consumed = _classify_message(msg, "yellow", 2023, 1)
    assert consumed.disposition is _Disposition.OUT_OF_SLICE


@pytest.mark.unit
def test_classify_message_wrong_month_is_out_of_slice() -> None:
    """A payload with a different month is OUT_OF_SLICE."""
    msg = _msg_for(_make_event(month=2))
    consumed = _classify_message(msg, "yellow", 2023, 1)
    assert consumed.disposition is _Disposition.OUT_OF_SLICE


@pytest.mark.unit
def test_classify_message_invalid_json_is_invalid() -> None:
    """A non-JSON payload is INVALID."""
    msg = _FakeMessage(b"not json at all")
    consumed = _classify_message(msg, "yellow", 2023, 1)
    assert consumed.disposition is _Disposition.INVALID
    assert consumed.event is None


@pytest.mark.unit
def test_classify_message_missing_field_is_invalid() -> None:
    """A payload missing required fields is INVALID."""
    payload = json.dumps({"event_id": "x", "schema_version": SCHEMA_VERSION}).encode("utf-8")
    msg = _FakeMessage(payload)
    consumed = _classify_message(msg, "yellow", 2023, 1)
    assert consumed.disposition is _Disposition.INVALID


@pytest.mark.unit
def test_classify_message_none_payload_is_invalid() -> None:
    """A tombstone (None value) is INVALID rather than crashing the driver."""
    msg = _FakeMessage(None)
    consumed = _classify_message(msg, "yellow", 2023, 1)
    assert consumed.disposition is _Disposition.INVALID
    assert consumed.event is None


@pytest.mark.unit
def test_consumed_message_is_frozen() -> None:
    """``_ConsumedMessage`` rejects attribute mutation."""
    consumed = _ConsumedMessage(_Disposition.IN_SLICE, _make_event())
    with pytest.raises(dataclasses.FrozenInstanceError):
        consumed.disposition = _Disposition.OUT_OF_SLICE  # type: ignore[misc]


# --- _aggregator_to_buckets -------------------------------------------------


@pytest.mark.unit
def test_aggregator_to_buckets_empty() -> None:
    """An empty aggregator produces an empty bucket list."""
    assert not _aggregator_to_buckets({})


@pytest.mark.unit
def test_aggregator_to_buckets_preserves_counts_and_keys() -> None:
    """Each aggregator entry produces one ``HourlyBucket`` with matching fields."""
    aggregator = {
        ("yellow", 2023, 1, 8): 42,
        ("yellow", 2023, 1, 17): 99,
    }
    buckets = _aggregator_to_buckets(aggregator)
    assert len(buckets) == 2
    by_hour = {b.hour: b for b in buckets}
    assert by_hour[8].event_count == 42
    assert by_hour[17].event_count == 99
    for bucket in buckets:
        assert bucket.cab_type == "yellow"
        assert bucket.year == 2023
        assert bucket.month == 1


@pytest.mark.unit
def test_aggregator_to_buckets_rejects_corrupt_count() -> None:
    """A negative aggregator count is rejected at the bucket boundary, not silently passed through."""
    with pytest.raises(InvalidRequestError):
        _aggregator_to_buckets({("yellow", 2023, 1, 8): -1})


@pytest.mark.unit
def test_aggregator_to_buckets_rejects_corrupt_hour() -> None:
    """An out-of-range hour is rejected at the bucket boundary."""
    with pytest.raises(InvalidRequestError):
        _aggregator_to_buckets({("yellow", 2023, 1, 24): 1})


# --- Test fakes for the driver ----------------------------------------------


class _FakeConsumer:
    """Recording stand-in for ``confluent_kafka.Consumer``.

    Implements the minimal surface :func:`consume_and_aggregate`
    touches: :meth:`list_topics`, :meth:`assign`, :meth:`assignment`,
    :meth:`get_watermark_offsets`, :meth:`poll`, :meth:`close`.

    The constructor takes the pre-baked messages (an iterable of
    :class:`_FakeMessage`) and the watermark map. ``poll`` drains
    messages in insertion order; once exhausted it returns ``None``,
    matching the real client's timeout behavior. The watermark map is
    consulted from inside :meth:`get_watermark_offsets`.
    """

    def __init__(
        self,
        messages: Iterable[_FakeMessage],
        watermarks: dict[int, int],
    ) -> None:
        self._messages: list[_FakeMessage] = list(messages)
        self._watermarks = watermarks
        self.assigned: list[Any] = []
        self.list_topics_calls: list[str] = []
        self.closed: bool = False
        self.poll_calls: int = 0

    def list_topics(self, topic: str, timeout: float = 0.0) -> Any:
        """Return a fake ``ClusterMetadata`` exposing the topic's partition ids."""
        _ = timeout
        self.list_topics_calls.append(topic)
        return _FakeClusterMetadata(topic, sorted(self._watermarks.keys()))

    def assign(self, partitions: list[Any]) -> None:
        """Record the assignment for assertion."""
        self.assigned = list(partitions)

    def assignment(self) -> list[Any]:
        """Return the previously-assigned partition list."""
        return list(self.assigned)

    def get_watermark_offsets(self, partition: Any, timeout: float = 0.0) -> tuple[int, int]:
        """Return ``(low=0, high=watermarks[partition.partition])``."""
        _ = timeout
        return (0, self._watermarks.get(partition.partition, 0))

    def poll(self, timeout: float) -> _FakeMessage | None:
        """Pop the next pre-baked message, or return ``None`` when drained."""
        _ = timeout
        self.poll_calls += 1
        if not self._messages:
            return None
        return self._messages.pop(0)

    def close(self) -> None:
        """Record the close call so tests can assert cleanup."""
        self.closed = True


class _FakeTopicMetadata:
    # pylint: disable=too-few-public-methods
    # Two attributes are the entire contract the consumer reads.
    """Stand-in for ``confluent_kafka.admin.TopicMetadata``."""

    def __init__(self, partition_ids: list[int]) -> None:
        self.partitions: dict[int, Any] = {p: object() for p in partition_ids}
        self.error: Any = None


class _FakeClusterMetadata:
    # pylint: disable=too-few-public-methods
    # One attribute (``topics``) is the entire contract the consumer reads.
    """Stand-in for ``confluent_kafka.admin.ClusterMetadata``."""

    def __init__(self, topic: str, partition_ids: list[int]) -> None:
        self.topics: dict[str, _FakeTopicMetadata] = {
            topic: _FakeTopicMetadata(partition_ids),
        }


class _RecordingSink:
    # pylint: disable=too-few-public-methods
    # The recorder is a callable; ``__call__`` is the entire contract
    # the production code needs, matching the shape of
    # ``upsert_hourly_counts``.
    """Recording stand-in for the sink's ``upsert_hourly_counts`` call."""

    def __init__(self) -> None:
        self.received_buckets: list[HourlyBucket] = []

    def __call__(self, _config: PostgresSinkConfig, buckets: Iterable[HourlyBucket]) -> int:
        """Capture the buckets handed to the sink; return rowcount == len."""
        self.received_buckets = list(buckets)
        return len(self.received_buckets)


@pytest.fixture
def consumer_config() -> TripCompletedConsumerConfig:
    """A consumer config suitable for tests."""
    return TripCompletedConsumerConfig.create_validated(
        "localhost:9092", "test-group", TRIP_COMPLETED_TOPIC, 1,
    )


@pytest.fixture
def sink_config() -> PostgresSinkConfig:
    """A sink config suitable for tests; never reached by a real connection in unit tests."""
    return PostgresSinkConfig.create_validated(
        "localhost", 5432, "nyc_cab_events", "nyc_cab", "nyc_cab",
    )


@pytest.fixture
def recording_sink(monkeypatch: pytest.MonkeyPatch) -> _RecordingSink:
    """Patch the sink call to a recording fake; return the recorder."""
    sink = _RecordingSink()
    monkeypatch.setattr(
        "nyc_cab_events.consumer.trip_completed.upsert_hourly_counts",
        sink,
    )
    return sink


def _install_fake_consumer(
    monkeypatch: pytest.MonkeyPatch,
    messages: Iterable[_FakeMessage],
    watermarks: dict[int, int],
) -> _FakeConsumer:
    """Install a ``_FakeConsumer`` via the module's factory monkeypatch."""
    fake = _FakeConsumer(messages, watermarks)
    monkeypatch.setattr(
        "nyc_cab_events.consumer.trip_completed._make_kafka_consumer",
        lambda _config: fake,
    )
    return fake


# --- Driver: empty topic / no-op runs ---------------------------------------


@pytest.mark.unit
def test_driver_empty_topic_returns_zero_result(
    monkeypatch: pytest.MonkeyPatch,
    consumer_config: TripCompletedConsumerConfig,
    sink_config: PostgresSinkConfig,
    recording_sink: _RecordingSink,
) -> None:
    """A topic with all-zero watermarks short-circuits to a zero result."""
    fake = _install_fake_consumer(monkeypatch, [], {0: 0, 1: 0, 2: 0})

    result = consume_and_aggregate(consumer_config, sink_config, "yellow", 2023, 1)

    assert result.events_read == 0
    assert result.events_in_slice == 0
    assert result.events_unique == 0
    assert result.hourly_buckets_written == 0
    assert recording_sink.received_buckets == []
    assert fake.closed is True


@pytest.mark.unit
def test_driver_closes_consumer_even_on_exception(
    monkeypatch: pytest.MonkeyPatch,
    consumer_config: TripCompletedConsumerConfig,
    sink_config: PostgresSinkConfig,
    recording_sink: _RecordingSink,
) -> None:
    """The try/finally around the poll loop runs ``close`` on the way out."""
    _ = recording_sink  # fixture is required for the monkeypatch

    class _ExplodingConsumer(_FakeConsumer):
        def list_topics(self, topic: str, timeout: float = 0.0) -> Any:
            raise RuntimeError("kafka unreachable")

    fake = _ExplodingConsumer([], {})
    monkeypatch.setattr(
        "nyc_cab_events.consumer.trip_completed._make_kafka_consumer",
        lambda _config: fake,
    )

    with pytest.raises(RuntimeError, match="kafka unreachable"):
        consume_and_aggregate(consumer_config, sink_config, "yellow", 2023, 1)
    assert fake.closed is True


# --- Driver: disposition partition (decision 45) ----------------------------


@pytest.mark.unit
def test_driver_partitions_events_read(
    monkeypatch: pytest.MonkeyPatch,
    consumer_config: TripCompletedConsumerConfig,
    sink_config: PostgresSinkConfig,
    recording_sink: _RecordingSink,
) -> None:
    """``events_read`` partitions cleanly across the three dispositions.

    Feeds 2 invalid + 3 out-of-slice + 4 in-slice messages (one
    duplicate among the in-slice events) and asserts the observable
    counters via the result fields. The unobservable counters
    (``events_invalid``, ``events_out_of_slice``, ``events_duplicate``)
    are checked indirectly by arithmetic on the result fields.
    """
    in_slice_events = [
        _make_event(event_id="0000000000000001", hour=8),
        _make_event(event_id="0000000000000002", hour=9),
        _make_event(event_id="0000000000000001", hour=8),  # duplicate of #1
        _make_event(event_id="0000000000000003", hour=10),
    ]
    out_of_slice_events = [
        _make_event(event_id="ff00000000000001", year=2024),
        _make_event(event_id="ff00000000000002", cab_type="green"),
        _make_event(event_id="ff00000000000003", month=2),
    ]
    invalid_payloads = [
        _FakeMessage(b"not json"),
        _FakeMessage(None),
    ]
    messages = (
        [_msg_for(e) for e in in_slice_events]
        + [_msg_for(e) for e in out_of_slice_events]
        + invalid_payloads
    )
    # Place a watermark for one partition large enough that the loop
    # only stops after draining all messages; the fake consumer's
    # offset accounting walks the partition forward each message.
    for i, msg in enumerate(messages):
        msg._offset = i  # pylint: disable=protected-access
    _install_fake_consumer(monkeypatch, messages, {0: len(messages)})

    result = consume_and_aggregate(consumer_config, sink_config, "yellow", 2023, 1)

    assert result.events_read == 9
    assert result.events_in_slice == 4
    assert result.events_unique == 3  # 4 in-slice minus 1 duplicate
    assert result.hourly_buckets_written == 3  # three distinct hours
    # Observable consequence of the partition identity:
    #   events_invalid + events_out_of_slice == events_read - events_in_slice
    assert result.events_read - result.events_in_slice == 5  # 2 + 3
    assert sum(b.event_count for b in recording_sink.received_buckets) == result.events_unique


@pytest.mark.unit
def test_driver_logs_full_counter_partition(
    monkeypatch: pytest.MonkeyPatch,
    consumer_config: TripCompletedConsumerConfig,
    sink_config: PostgresSinkConfig,
    recording_sink: _RecordingSink,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The ``consumer.done`` INFO log line surfaces all six counters.

    This is the direct test of the additive identity
    ``events_read == events_invalid + events_out_of_slice +
    events_in_slice``: the log line is the only place the unobservable
    counters are reported, so the test parses the structured line and
    asserts the identity holds end-to-end.
    """
    _ = recording_sink  # fixture is required for the monkeypatch
    messages = [
        _msg_for(_make_event(event_id="0000000000000001", hour=8)),
        _msg_for(_make_event(event_id="0000000000000002", hour=9)),
        _msg_for(_make_event(event_id="0000000000000001", hour=8)),  # duplicate
        _msg_for(_make_event(event_id="ff00000000000001", year=2024)),  # out-of-slice
        _FakeMessage(b"garbage"),  # invalid
    ]
    for i, msg in enumerate(messages):
        msg._offset = i  # pylint: disable=protected-access
    _install_fake_consumer(monkeypatch, messages, {0: len(messages)})

    with caplog.at_level(logging.INFO, logger="nyc_cab_events.consumer.trip_completed"):
        consume_and_aggregate(consumer_config, sink_config, "yellow", 2023, 1)

    done_lines = [r.getMessage() for r in caplog.records if "consumer.done" in r.getMessage()]
    assert len(done_lines) == 1
    line = done_lines[0]
    # Parse the structured key=value fragments.
    fragments = dict(
        token.split("=", 1) for token in line.split() if "=" in token
    )
    counters = {k: int(v) for k, v in fragments.items() if v.lstrip("-").isdigit()}
    assert counters["read"] == 5
    assert counters["invalid"] == 1
    assert counters["out_of_slice"] == 1
    assert counters["in_slice"] == 3
    assert counters["duplicate"] == 1
    assert counters["unique"] == 2
    # The additive identity, asserted directly:
    assert counters["read"] == (
        counters["invalid"] + counters["out_of_slice"] + counters["in_slice"]
    )
    # And the seen-set identity:
    assert counters["in_slice"] == counters["duplicate"] + counters["unique"]


# --- Driver: integrity invariants (decision 45) -----------------------------


@pytest.mark.unit
def test_driver_bucket_sum_equals_events_unique(
    monkeypatch: pytest.MonkeyPatch,
    consumer_config: TripCompletedConsumerConfig,
    sink_config: PostgresSinkConfig,
    recording_sink: _RecordingSink,
) -> None:
    """``sum(bucket.event_count) == events_unique`` holds end-to-end."""
    events = [
        _make_event(event_id=f"{i:016x}", hour=i % 4)
        for i in range(1, 11)
    ]
    messages = [_msg_for(e) for e in events]
    for i, msg in enumerate(messages):
        msg._offset = i  # pylint: disable=protected-access
    _install_fake_consumer(monkeypatch, messages, {0: len(messages)})

    result = consume_and_aggregate(consumer_config, sink_config, "yellow", 2023, 1)

    assert result.events_unique == 10
    assert sum(b.event_count for b in recording_sink.received_buckets) == result.events_unique


@pytest.mark.unit
def test_driver_hourly_buckets_written_matches_bucket_count(
    monkeypatch: pytest.MonkeyPatch,
    consumer_config: TripCompletedConsumerConfig,
    sink_config: PostgresSinkConfig,
    recording_sink: _RecordingSink,
) -> None:
    """``hourly_buckets_written == len(buckets)`` holds end-to-end."""
    events = [
        _make_event(event_id=f"{i:016x}", hour=h)
        for i, h in enumerate([0, 1, 2, 3, 4, 5], start=1)
    ]
    messages = [_msg_for(e) for e in events]
    for i, msg in enumerate(messages):
        msg._offset = i  # pylint: disable=protected-access
    _install_fake_consumer(monkeypatch, messages, {0: len(messages)})

    result = consume_and_aggregate(consumer_config, sink_config, "yellow", 2023, 1)

    assert result.hourly_buckets_written == 6
    assert len(recording_sink.received_buckets) == result.hourly_buckets_written


@pytest.mark.unit
def test_driver_raises_on_aggregator_sum_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    consumer_config: TripCompletedConsumerConfig,
    sink_config: PostgresSinkConfig,
    recording_sink: _RecordingSink,
) -> None:
    """A corrupted aggregator-to-buckets helper trips the integrity check.

    Replaces ``_aggregator_to_buckets`` with a function that returns
    buckets whose summed event counts no longer match
    ``events_unique``. The driver must raise ``RuntimeError`` at the
    integrity boundary rather than silently propagating bad data to
    the sink.
    """
    _ = recording_sink  # fixture is required for the monkeypatch

    def _bad_aggregator(_aggregator: dict) -> list[HourlyBucket]:
        return [HourlyBucket.create_validated("yellow", 2023, 1, 0, 99)]

    messages = [_msg_for(_make_event(event_id="0000000000000001", hour=8))]
    messages[0]._offset = 0  # pylint: disable=protected-access
    _install_fake_consumer(monkeypatch, messages, {0: 1})
    monkeypatch.setattr(
        "nyc_cab_events.consumer.trip_completed._aggregator_to_buckets",
        _bad_aggregator,
    )

    with pytest.raises(RuntimeError, match="bucket sum"):
        consume_and_aggregate(consumer_config, sink_config, "yellow", 2023, 1)


@pytest.mark.unit
def test_driver_raises_on_bucket_count_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    consumer_config: TripCompletedConsumerConfig,
    sink_config: PostgresSinkConfig,
    recording_sink: _RecordingSink,
) -> None:
    """A bucket count that diverges from the aggregator size trips the integrity check.

    Crafts a faulty helper that preserves the bucket sum but inflates
    the bucket count (e.g. by emitting a zero-count bucket the
    aggregator does not contain). The second integrity guard catches
    this case independently of the sum check.
    """
    _ = recording_sink

    def _padded_aggregator(_aggregator: dict) -> list[HourlyBucket]:
        return [
            HourlyBucket.create_validated("yellow", 2023, 1, 0, 1),
            HourlyBucket.create_validated("yellow", 2023, 1, 1, 0),  # phantom bucket
        ]

    messages = [_msg_for(_make_event(event_id="0000000000000001", hour=0))]
    messages[0]._offset = 0  # pylint: disable=protected-access
    _install_fake_consumer(monkeypatch, messages, {0: 1})
    monkeypatch.setattr(
        "nyc_cab_events.consumer.trip_completed._aggregator_to_buckets",
        _padded_aggregator,
    )

    with pytest.raises(RuntimeError, match="len.buckets."):
        consume_and_aggregate(consumer_config, sink_config, "yellow", 2023, 1)


# --- Driver: decision-44 mechanics (assign, not subscribe) ------------------


@pytest.mark.unit
def test_driver_assigns_partitions_at_offset_zero(
    monkeypatch: pytest.MonkeyPatch,
    consumer_config: TripCompletedConsumerConfig,
    sink_config: PostgresSinkConfig,
    recording_sink: _RecordingSink,
) -> None:
    """The driver calls ``assign`` (not ``subscribe``) with all partitions at offset 0."""
    _ = recording_sink
    fake = _install_fake_consumer(monkeypatch, [], {0: 0, 1: 0, 2: 0})

    consume_and_aggregate(consumer_config, sink_config, "yellow", 2023, 1)

    assigned_partitions = sorted(tp.partition for tp in fake.assigned)
    assigned_offsets = {tp.offset for tp in fake.assigned}
    assert assigned_partitions == [0, 1, 2]
    assert assigned_offsets == {0}
    # And confirm list_topics was actually used for partition discovery:
    assert fake.list_topics_calls == [consumer_config.topic]


@pytest.mark.unit
def test_driver_skips_empty_partitions_at_window_capture(
    monkeypatch: pytest.MonkeyPatch,
    consumer_config: TripCompletedConsumerConfig,
    sink_config: PostgresSinkConfig,
    recording_sink: _RecordingSink,
) -> None:
    """Partitions with ``high == 0`` are dropped from the replay window before polling.

    The driver should not poll forever waiting for messages that will
    never arrive on an empty partition. With three partitions where
    only partition 0 has data, the loop terminates cleanly.
    """
    _ = recording_sink
    msg = _msg_for(_make_event(event_id="0000000000000001", hour=8), partition=0, offset=0)
    fake = _install_fake_consumer(monkeypatch, [msg], {0: 1, 1: 0, 2: 0})

    result = consume_and_aggregate(consumer_config, sink_config, "yellow", 2023, 1)

    assert result.events_read == 1
    assert result.events_unique == 1
    # The fake polled only enough times to consume the one message
    # plus terminate; an unbounded loop on empty partitions would
    # produce far more poll calls than messages.
    assert fake.poll_calls <= 3


# --- Driver: replay window guard (decision 42) ------------------------------


@pytest.mark.unit
def test_driver_ignores_mid_run_writes_to_empty_partition(
    monkeypatch: pytest.MonkeyPatch,
    consumer_config: TripCompletedConsumerConfig,
    sink_config: PostgresSinkConfig,
    recording_sink: _RecordingSink,
) -> None:
    """A message from an initially-empty partition arriving mid-run is ignored.

    Decision 42 commits to "events arriving mid-run fall into the next
    replay." A partition with ``high == 0`` at window capture is excluded
    from the replay window; if the broker subsequently delivers a
    message from that partition (a mid-run producer write), the driver
    must reject it, not count it.
    """
    in_window_msg = _msg_for(
        _make_event(event_id="0000000000000001", hour=8),
        partition=1, offset=0,
    )
    mid_run_msg = _msg_for(
        _make_event(event_id="0000000000000002", hour=9),
        partition=0, offset=0,
    )
    # partition 0 was empty at capture (high=0); partition 1 has one
    # message. Feeding mid_run_msg first ensures the guard is exercised
    # before the in-window message terminates the loop.
    _install_fake_consumer(
        monkeypatch, [mid_run_msg, in_window_msg], {0: 0, 1: 1},
    )

    result = consume_and_aggregate(consumer_config, sink_config, "yellow", 2023, 1)

    assert result.events_read == 1
    assert result.events_unique == 1
    assert result.hourly_buckets_written == 1
    bucket_hours = {b.hour for b in recording_sink.received_buckets}
    assert bucket_hours == {8}


@pytest.mark.unit
def test_driver_ignores_offsets_beyond_captured_window(
    monkeypatch: pytest.MonkeyPatch,
    consumer_config: TripCompletedConsumerConfig,
    sink_config: PostgresSinkConfig,
    recording_sink: _RecordingSink,
) -> None:
    """A message whose offset is >= the captured high-water mark is ignored.

    Decision 42's deterministic replay window is ``[0, captured_high)``.
    A mid-run producer write to a non-empty partition produces a message
    at offset >= captured_high; the driver must reject it.
    """
    in_window_msg = _msg_for(
        _make_event(event_id="0000000000000001", hour=8),
        partition=0, offset=0,
    )
    beyond_window_msg = _msg_for(
        _make_event(event_id="0000000000000002", hour=9),
        partition=0, offset=1,
    )
    # captured_high = 1, so only offset 0 is in-window. The fake
    # delivers the in-window message first to terminate the loop;
    # the beyond-window message is then drained and rejected.
    _install_fake_consumer(
        monkeypatch, [beyond_window_msg, in_window_msg], {0: 1},
    )

    result = consume_and_aggregate(consumer_config, sink_config, "yellow", 2023, 1)

    assert result.events_read == 1
    assert result.events_unique == 1
    assert result.hourly_buckets_written == 1
    bucket_hours = {b.hour for b in recording_sink.received_buckets}
    assert bucket_hours == {8}


@pytest.mark.unit
def test_driver_events_read_excludes_beyond_window_messages(
    monkeypatch: pytest.MonkeyPatch,
    consumer_config: TripCompletedConsumerConfig,
    sink_config: PostgresSinkConfig,
    recording_sink: _RecordingSink,
) -> None:
    """``events_read`` counts only messages within the captured window.

    Combines both rejection paths — empty-partition writes and beyond-
    high-watermark writes — and asserts the ``events_read`` counter
    matches only the in-window message count. Verifies the guard fires
    independently of the order in which rejected messages arrive
    relative to in-window messages.
    """
    in_window_msgs = [
        _msg_for(
            _make_event(event_id="0000000000000001", hour=8),
            partition=0, offset=0,
        ),
        _msg_for(
            _make_event(event_id="0000000000000002", hour=9),
            partition=0, offset=1,
        ),
    ]
    beyond_window_msgs = [
        # partition=0, offset=2: at the captured high (high=2) — first
        # beyond-window record.
        _msg_for(
            _make_event(event_id="0000000000000003", hour=10),
            partition=0, offset=2,
        ),
        # partition=1: empty at capture; any delivery is rejected.
        _msg_for(
            _make_event(event_id="0000000000000004", hour=11),
            partition=1, offset=0,
        ),
    ]
    # Interleave: a rejected message first, then in-window, then more
    # rejected. The driver must keep events_read at 2 throughout.
    poll_order = [
        beyond_window_msgs[0],
        beyond_window_msgs[1],
        in_window_msgs[0],
        in_window_msgs[1],
    ]
    _install_fake_consumer(monkeypatch, poll_order, {0: 2, 1: 0})

    result = consume_and_aggregate(consumer_config, sink_config, "yellow", 2023, 1)

    assert result.events_read == 2
    assert result.events_unique == 2
    assert result.hourly_buckets_written == 2
    bucket_hours = {b.hour for b in recording_sink.received_buckets}
    assert bucket_hours == {8, 9}
