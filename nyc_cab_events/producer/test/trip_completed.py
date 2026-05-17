# pylint: disable=redefined-outer-name,duplicate-code
# duplicate-code: the silver_row fixture is intentionally repeated across
# the events test files (decision 28: tolerate duplication until shared
# vocabulary is justified).
"""Tests for :mod:`nyc_cab_events.producer.trip_completed`."""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from nyc_cab.exceptions import InvalidRequestError
from nyc_cab_events.contracts.events import (
    SCHEMA_VERSION,
    TRIP_COMPLETED_QUARANTINE_TOPIC,
    TRIP_COMPLETED_TOPIC,
    EventRejectionReason,
    TripCompleted,
    from_json,
)
from nyc_cab_events.producer.trip_completed import (
    TripCompletedProducerConfig,
    TripCompletedProducerResult,
    _build_event_from_silver_row,
    _hour_of,
    _route_silver_row,
    produce_trip_completed_events,
)


# Module-level ``pytestmark = pytest.mark.unit`` is intentionally absent.
# Unit-tier tests are marked individually with ``@pytest.mark.unit`` so the
# ``@pytest.mark.spark`` driver tests further down do not inherit the
# ``unit`` mark; a module-level mark would make ``pytest -m unit`` try to
# run the Spark tests.


# --- Fixtures ---------------------------------------------------------------


@pytest.fixture
def silver_row() -> dict[str, Any]:
    """A Silver-shaped accepted row sufficient for event construction."""
    return {
        "VendorID": 1,
        "tpep_pickup_datetime": datetime(2023, 1, 15, 14, 30, 0),
        "tpep_dropoff_datetime": datetime(2023, 1, 15, 14, 55, 0),
        "PULocationID": 161,
        "DOLocationID": 236,
        "passenger_count": 2,
        "trip_distance": 3.5,
        "fare_amount": 18.0,
        "total_amount": 22.0,
    }


@pytest.fixture
def produced_at() -> datetime:
    """A timezone-aware UTC instant for ``produced_at``."""
    return datetime(2025, 5, 14, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def producer_config() -> TripCompletedProducerConfig:
    """A producer config suitable for tests."""
    return TripCompletedProducerConfig.create_validated(
        "localhost:9092", TRIP_COMPLETED_TOPIC, TRIP_COMPLETED_QUARANTINE_TOPIC,
    )


# --- TripCompletedProducerConfig: happy paths -------------------------------


@pytest.mark.unit
def test_config_happy_path() -> None:
    """A well-formed config constructs cleanly."""
    config = TripCompletedProducerConfig.create_validated(
        "localhost:9092", TRIP_COMPLETED_TOPIC, TRIP_COMPLETED_QUARANTINE_TOPIC,
    )
    assert config.bootstrap_servers == "localhost:9092"
    assert config.topic == TRIP_COMPLETED_TOPIC
    assert config.quarantine_topic == TRIP_COMPLETED_QUARANTINE_TOPIC


@pytest.mark.unit
def test_config_defaults_to_v1_topics() -> None:
    """Direct construction with defaults uses the v1 contract topic names."""
    config = TripCompletedProducerConfig(bootstrap_servers="localhost:9092")
    assert config.topic == TRIP_COMPLETED_TOPIC
    assert config.quarantine_topic == TRIP_COMPLETED_QUARANTINE_TOPIC


# --- TripCompletedProducerConfig: type rejections ---------------------------


@pytest.mark.unit
def test_config_rejects_non_string_bootstrap() -> None:
    """``bootstrap_servers`` must be a string."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedProducerConfig.create_validated(9092, "t", "q")
    names = [v[0] for v in info.value.violations]
    assert "bootstrap_servers" in names


@pytest.mark.unit
def test_config_rejects_non_string_topic() -> None:
    """``topic`` must be a string."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedProducerConfig.create_validated("localhost:9092", 0, "q")
    names = [v[0] for v in info.value.violations]
    assert "topic" in names


@pytest.mark.unit
def test_config_rejects_non_string_quarantine_topic() -> None:
    """``quarantine_topic`` must be a string."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedProducerConfig.create_validated("localhost:9092", "t", None)
    names = [v[0] for v in info.value.violations]
    assert "quarantine_topic" in names


# --- TripCompletedProducerConfig: structural rejections ---------------------


@pytest.mark.unit
def test_config_rejects_blank_bootstrap_servers() -> None:
    """Blank bootstrap_servers violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedProducerConfig.create_validated("   ", "t", "q")
    assert ("bootstrap_servers", "   ") in info.value.violations


@pytest.mark.unit
def test_config_rejects_blank_topic() -> None:
    """Blank topic violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedProducerConfig.create_validated("localhost:9092", "", "q")
    assert ("topic", "") in info.value.violations


@pytest.mark.unit
def test_config_rejects_blank_quarantine_topic() -> None:
    """Blank quarantine_topic violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedProducerConfig.create_validated("localhost:9092", "t", "")
    assert ("quarantine_topic", "") in info.value.violations


@pytest.mark.unit
def test_config_rejects_topic_equals_quarantine_topic() -> None:
    """The two topic fields must differ — otherwise quarantine routing collapses."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedProducerConfig.create_validated("localhost:9092", "same", "same")
    names = [v[0] for v in info.value.violations]
    assert "quarantine_topic" in names


@pytest.mark.unit
def test_config_is_frozen() -> None:
    """``TripCompletedProducerConfig`` rejects attribute mutation."""
    config = TripCompletedProducerConfig.create_validated("localhost:9092", "t", "q")
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.topic = "other"  # type: ignore[misc]


# --- TripCompletedProducerResult: happy paths -------------------------------


@pytest.fixture
def silver_path(tmp_path: Path) -> Path:
    """A directory path that survives the silver_partition_path structural check."""
    partition = tmp_path / "cab_type=yellow" / "year=2023" / "month=1"
    partition.mkdir(parents=True)
    return partition


@pytest.mark.unit
def test_result_happy_path(silver_path: Path) -> None:
    """A well-formed result satisfies the reconciliation invariant."""
    result = TripCompletedProducerResult.create_validated(
        "yellow", 2023, 1, silver_path, 1000, 950, 50,
    )
    assert result.silver_read_count == 1000
    assert result.events_emitted == 950
    assert result.events_quarantined == 50


@pytest.mark.unit
def test_result_happy_path_zero_quarantine(silver_path: Path) -> None:
    """A run with no quarantined events is valid."""
    result = TripCompletedProducerResult.create_validated(
        "yellow", 2023, 1, silver_path, 1000, 1000, 0,
    )
    assert result.events_quarantined == 0


@pytest.mark.unit
def test_result_happy_path_empty_partition(silver_path: Path) -> None:
    """A zero-row partition produces a zero-event result."""
    result = TripCompletedProducerResult.create_validated(
        "yellow", 2023, 1, silver_path, 0, 0, 0,
    )
    assert result.silver_read_count == 0


# --- TripCompletedProducerResult: type rejections ---------------------------


@pytest.mark.unit
def test_result_rejects_bool_year(silver_path: Path) -> None:
    """``year`` rejects bool despite int compatibility."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedProducerResult.create_validated(
            "yellow", True, 1, silver_path, 1, 1, 0,
        )
    assert ("year", True) in info.value.violations


@pytest.mark.unit
def test_result_rejects_bool_month(silver_path: Path) -> None:
    """``month`` rejects bool despite int compatibility."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedProducerResult.create_validated(
            "yellow", 2023, True, silver_path, 1, 1, 0,
        )
    assert ("month", True) in info.value.violations


@pytest.mark.unit
def test_result_rejects_non_path_silver_partition_path() -> None:
    """``silver_partition_path`` must be a :class:`Path` instance."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedProducerResult.create_validated(
            "yellow", 2023, 1, "/tmp/x", 0, 0, 0,
        )
    names = [v[0] for v in info.value.violations]
    assert "silver_partition_path" in names


# --- TripCompletedProducerResult: structural rejections ---------------------


@pytest.mark.unit
def test_result_rejects_negative_emitted(silver_path: Path) -> None:
    """Negative ``events_emitted`` violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedProducerResult.create_validated(
            "yellow", 2023, 1, silver_path, 0, -1, 1,
        )
    assert ("events_emitted", -1) in info.value.violations


@pytest.mark.unit
def test_result_rejects_reconciliation_mismatch(silver_path: Path) -> None:
    """The reconciliation invariant is enforced at construction time."""
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedProducerResult.create_validated(
            "yellow", 2023, 1, silver_path, 100, 50, 49,
        )
    names = [v[0] for v in info.value.violations]
    assert "reconciliation" in names


@pytest.mark.unit
def test_result_rejects_silver_partition_path_pointing_at_file(tmp_path: Path) -> None:
    """``silver_partition_path`` must not refer to a regular file."""
    file_path = tmp_path / "not_a_dir"
    file_path.write_text("placeholder")
    with pytest.raises(InvalidRequestError) as info:
        TripCompletedProducerResult.create_validated(
            "yellow", 2023, 1, file_path, 0, 0, 0,
        )
    names = [v[0] for v in info.value.violations]
    assert "silver_partition_path" in names


@pytest.mark.unit
def test_result_accepts_nonexistent_silver_partition_path(tmp_path: Path) -> None:
    """A not-yet-materialized partition path is allowed (mirrors SilverTransformResult)."""
    missing = tmp_path / "does" / "not" / "exist"
    result = TripCompletedProducerResult.create_validated(
        "yellow", 2023, 1, missing, 0, 0, 0,
    )
    assert result.silver_partition_path == missing


@pytest.mark.unit
def test_result_is_frozen(silver_path: Path) -> None:
    """``TripCompletedProducerResult`` rejects attribute mutation."""
    result = TripCompletedProducerResult.create_validated(
        "yellow", 2023, 1, silver_path, 0, 0, 0,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.events_emitted = 1  # type: ignore[misc]


# --- _hour_of ---------------------------------------------------------------


@pytest.mark.unit
def test_hour_of_extracts_local_hour() -> None:
    """The local hour-of-day is the field's ``.hour`` attribute (NYC local time)."""
    assert _hour_of(datetime(2023, 1, 15, 14, 30)) == 14


@pytest.mark.unit
def test_hour_of_midnight() -> None:
    """Midnight renders as hour 0."""
    assert _hour_of(datetime(2023, 1, 15, 0, 0)) == 0


@pytest.mark.unit
def test_hour_of_eleven_pm() -> None:
    """11 PM renders as hour 23."""
    assert _hour_of(datetime(2023, 1, 15, 23, 59)) == 23


# --- _build_event_from_silver_row -------------------------------------------


@pytest.mark.unit
def test_build_event_happy_path(silver_row: dict[str, Any], produced_at: datetime) -> None:
    """A well-formed Silver row builds a valid event."""
    event = _build_event_from_silver_row(silver_row, "yellow", 2023, 1, produced_at)
    assert isinstance(event, TripCompleted)
    assert event.cab_type == "yellow"
    assert event.year == 2023
    assert event.month == 1
    assert event.hour == 14
    assert event.trip_distance == 3.5
    assert event.fare_amount == 18.0
    assert event.passenger_count == 2
    assert event.produced_at == produced_at
    assert event.schema_version == SCHEMA_VERSION


@pytest.mark.unit
def test_build_event_id_is_deterministic(silver_row: dict[str, Any], produced_at: datetime) -> None:
    """Same Silver row builds the same event_id every time, regardless of produced_at."""
    later = datetime(2099, 1, 1, tzinfo=timezone.utc)
    e1 = _build_event_from_silver_row(silver_row, "yellow", 2023, 1, produced_at)
    e2 = _build_event_from_silver_row(silver_row, "yellow", 2023, 1, later)
    assert e1.event_id == e2.event_id


@pytest.mark.unit
def test_build_event_id_differs_across_slices(silver_row: dict[str, Any], produced_at: datetime) -> None:
    """Same Silver row under different slice metadata yields different event_ids."""
    e_jan = _build_event_from_silver_row(silver_row, "yellow", 2023, 1, produced_at)
    e_feb = _build_event_from_silver_row(silver_row, "yellow", 2023, 2, produced_at)
    assert e_jan.event_id != e_feb.event_id


@pytest.mark.unit
def test_build_event_raises_on_missing_pickup_datetime(
    silver_row: dict[str, Any], produced_at: datetime,
) -> None:
    """A missing required Silver field raises ``KeyError``."""
    incomplete = {k: v for k, v in silver_row.items() if k != "tpep_pickup_datetime"}
    with pytest.raises(KeyError):
        _build_event_from_silver_row(incomplete, "yellow", 2023, 1, produced_at)


@pytest.mark.unit
def test_build_event_rejects_invalid_passenger_count(
    silver_row: dict[str, Any], produced_at: datetime,
) -> None:
    """A passenger_count outside [0, 9] is rejected at contract construction."""
    bad_row = {**silver_row, "passenger_count": 11}
    with pytest.raises(InvalidRequestError):
        _build_event_from_silver_row(bad_row, "yellow", 2023, 1, produced_at)


@pytest.mark.unit
def test_build_event_rejects_negative_fare_amount(
    silver_row: dict[str, Any], produced_at: datetime,
) -> None:
    """A negative fare_amount is rejected at contract construction."""
    bad_row = {**silver_row, "fare_amount": -1.0}
    with pytest.raises(InvalidRequestError):
        _build_event_from_silver_row(bad_row, "yellow", 2023, 1, produced_at)


# --- _route_silver_row -----------------------------------------------------


@pytest.mark.unit
def test_route_clean_row_to_primary_topic(
    silver_row: dict[str, Any], produced_at: datetime,
    producer_config: TripCompletedProducerConfig,
) -> None:
    """A clean row routes to the configured primary topic with the event_key."""
    routed = _route_silver_row(silver_row, "yellow", 2023, 1, producer_config, produced_at)
    assert routed.is_quarantine is False
    assert routed.topic == TRIP_COMPLETED_TOPIC
    assert routed.key == "yellow/2023/01/14"


@pytest.mark.unit
def test_route_clean_row_payload_round_trips(
    silver_row: dict[str, Any], produced_at: datetime,
    producer_config: TripCompletedProducerConfig,
) -> None:
    """The primary-topic value round-trips through ``from_json``."""
    routed = _route_silver_row(silver_row, "yellow", 2023, 1, producer_config, produced_at)
    event = from_json(routed.value)
    assert event.cab_type == "yellow"
    assert event.year == 2023
    assert event.month == 1
    assert event.hour == 14


@pytest.mark.unit
def test_route_clean_row_headers_carry_schema_version(
    silver_row: dict[str, Any], produced_at: datetime,
    producer_config: TripCompletedProducerConfig,
) -> None:
    """The primary-topic message carries the schema_version as a Kafka header."""
    routed = _route_silver_row(silver_row, "yellow", 2023, 1, producer_config, produced_at)
    header_map = dict(routed.headers)
    assert header_map["schema_version"] == SCHEMA_VERSION.encode("utf-8")


@pytest.mark.unit
def test_route_bad_row_to_quarantine(
    silver_row: dict[str, Any], produced_at: datetime,
    producer_config: TripCompletedProducerConfig,
) -> None:
    """A row that fails contract validation routes to the quarantine topic."""
    bad_row = {**silver_row, "fare_amount": -1.0}
    routed = _route_silver_row(bad_row, "yellow", 2023, 1, producer_config, produced_at)
    assert routed.is_quarantine is True
    assert routed.topic == TRIP_COMPLETED_QUARANTINE_TOPIC


@pytest.mark.unit
def test_route_missing_field_to_quarantine(
    silver_row: dict[str, Any], produced_at: datetime,
    producer_config: TripCompletedProducerConfig,
) -> None:
    """A row missing a required source field routes to the quarantine topic."""
    incomplete = {k: v for k, v in silver_row.items() if k != "VendorID"}
    routed = _route_silver_row(incomplete, "yellow", 2023, 1, producer_config, produced_at)
    assert routed.is_quarantine is True
    assert routed.topic == TRIP_COMPLETED_QUARANTINE_TOPIC


@pytest.mark.unit
def test_route_quarantine_headers_name_rejection_reason(
    silver_row: dict[str, Any], produced_at: datetime,
    producer_config: TripCompletedProducerConfig,
) -> None:
    """Quarantine messages carry the rejection reason in Kafka headers."""
    bad_row = {**silver_row, "fare_amount": -1.0}
    routed = _route_silver_row(bad_row, "yellow", 2023, 1, producer_config, produced_at)
    header_map = dict(routed.headers)
    assert header_map["rejection_reason"] == EventRejectionReason.INVALID_CONSTRUCTION.value.encode("utf-8")
    assert "quarantined_at" in header_map
    assert "violations" in header_map


@pytest.mark.unit
def test_route_quarantine_key_has_monthly_grain(
    silver_row: dict[str, Any], produced_at: datetime,
    producer_config: TripCompletedProducerConfig,
) -> None:
    """Quarantine keys use monthly grain (no hour available for a malformed row)."""
    bad_row = {**silver_row, "fare_amount": -1.0}
    routed = _route_silver_row(bad_row, "yellow", 2023, 1, producer_config, produced_at)
    assert routed.key == "yellow/2023/01"


@pytest.mark.unit
def test_route_quarantine_body_is_json_object(
    silver_row: dict[str, Any], produced_at: datetime,
    producer_config: TripCompletedProducerConfig,
) -> None:
    """The quarantine payload body is a JSON object carrying the raw source row plus slice metadata."""
    bad_row = {**silver_row, "fare_amount": -1.0}
    routed = _route_silver_row(bad_row, "yellow", 2023, 1, producer_config, produced_at)
    body = json.loads(routed.value)
    assert isinstance(body, dict)
    assert body["cab_type"] == "yellow"
    assert body["year"] == 2023
    assert body["month"] == 1
    assert body["fare_amount"] == -1.0


@pytest.mark.unit
def test_route_uses_configured_quarantine_topic(
    silver_row: dict[str, Any], produced_at: datetime,
) -> None:
    """The configured quarantine_topic on the producer config is honored
    (regression test: prior implementation called ``quarantine_topic_for``
    directly and silently ignored the config field)."""
    override_config = TripCompletedProducerConfig.create_validated(
        "localhost:9092", TRIP_COMPLETED_TOPIC, "scratch.quarantine.topic",
    )
    bad_row = {**silver_row, "fare_amount": -1.0}
    routed = _route_silver_row(bad_row, "yellow", 2023, 1, override_config, produced_at)
    assert routed.is_quarantine is True
    assert routed.topic == "scratch.quarantine.topic"


# --- Driver test with a fake Kafka producer ---------------------------------


class _FakeProducer:
    """Minimal stand-in for ``confluent_kafka.Producer`` capturing produced messages."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    def produce(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        topic: str,
        key: str | None = None,
        value: bytes | None = None,
        headers: list[tuple[str, bytes]] | None = None,
        on_delivery: Any = None,
    ) -> None:
        """Record the produce call; invoke the delivery callback synchronously with success."""
        self.messages.append({
            "topic": topic, "key": key, "value": value, "headers": headers or [],
        })
        if on_delivery is not None:
            on_delivery(None, None)  # err is None == success

    def poll(self, _timeout: float) -> None:
        """No-op; fake has no async delivery queue."""

    def flush(self, timeout: float = 0) -> int:
        """All messages are delivered synchronously; nothing remains."""
        _ = timeout
        return 0


@pytest.fixture
def fake_producer(monkeypatch: pytest.MonkeyPatch) -> _FakeProducer:
    """Monkeypatch the Kafka producer factory to return a recording fake."""
    fake = _FakeProducer()
    monkeypatch.setattr(
        "nyc_cab_events.producer.trip_completed._make_kafka_producer",
        lambda _config: fake,
    )
    return fake


class _FakeAdminFuture:
    """Stand-in for the futures returned by ``AdminClient.create_topics``."""

    # pylint: disable=too-few-public-methods
    # One method is the entire contract of an admin future.

    def __init__(self, raise_already_exists: bool = False) -> None:
        self._raise = raise_already_exists

    def result(self) -> None:
        """Either return cleanly or raise a properly-coded KafkaException.

        When the test wants to simulate the "topic already exists" path,
        we raise ``KafkaException(KafkaError(TOPIC_ALREADY_EXISTS))`` so
        the production code's error-code inspection path is the one
        actually exercised.
        """
        if self._raise:
            # pylint: disable=import-outside-toplevel
            from confluent_kafka import KafkaError as _KafkaError
            from confluent_kafka import KafkaException as _KafkaException
            raise _KafkaException(_KafkaError(_KafkaError.TOPIC_ALREADY_EXISTS))


class _FakeAdminClient:
    """Recording stand-in for ``confluent_kafka.admin.AdminClient``.

    Captures every ``create_topics`` call and returns a dict of fake
    futures keyed by the topic name (matching the real API shape).
    """

    # pylint: disable=too-few-public-methods
    # One method is the entire contract this fake needs to implement
    # for ensure_topics' production code path.

    def __init__(self, *, raise_already_exists: bool = False) -> None:
        self.create_topics_calls: list[list] = []
        self._raise_already_exists = raise_already_exists

    def create_topics(self, new_topics: list) -> dict:
        """Record the call and return per-topic futures."""
        self.create_topics_calls.append(new_topics)
        return {
            nt.topic: _FakeAdminFuture(self._raise_already_exists)
            for nt in new_topics
        }


@pytest.fixture
def fake_admin(monkeypatch: pytest.MonkeyPatch) -> _FakeAdminClient:
    """Monkeypatch the AdminClient factory to return a recording fake."""
    fake = _FakeAdminClient()
    monkeypatch.setattr(
        "nyc_cab_events.producer.trip_completed._make_admin_client",
        lambda _config: fake,
    )
    return fake


@pytest.fixture
def fake_admin_topics_exist(monkeypatch: pytest.MonkeyPatch) -> _FakeAdminClient:
    """Monkeypatch the AdminClient factory to return a fake that reports
    every topic as already existing."""
    fake = _FakeAdminClient(raise_already_exists=True)
    monkeypatch.setattr(
        "nyc_cab_events.producer.trip_completed._make_admin_client",
        lambda _config: fake,
    )
    return fake


# --- ensure_topics ----------------------------------------------------------


@pytest.mark.unit
def test_ensure_topics_creates_primary_and_quarantine(
    producer_config: TripCompletedProducerConfig, fake_admin: _FakeAdminClient,
) -> None:
    """``ensure_topics`` calls AdminClient.create_topics with both topic names."""
    # pylint: disable=import-outside-toplevel
    from nyc_cab_events.producer.trip_completed import ensure_topics
    ensure_topics(producer_config)
    assert len(fake_admin.create_topics_calls) == 1
    topic_names = sorted(nt.topic for nt in fake_admin.create_topics_calls[0])
    assert topic_names == sorted([TRIP_COMPLETED_TOPIC, TRIP_COMPLETED_QUARANTINE_TOPIC])


@pytest.mark.unit
def test_ensure_topics_uses_configured_topic_names(
    fake_admin: _FakeAdminClient,
) -> None:
    """``ensure_topics`` honors the producer config's topic and quarantine_topic fields."""
    # pylint: disable=import-outside-toplevel
    from nyc_cab_events.producer.trip_completed import ensure_topics
    override_config = TripCompletedProducerConfig.create_validated(
        "localhost:9092", "scratch.primary", "scratch.quarantine",
    )
    ensure_topics(override_config)
    topic_names = sorted(nt.topic for nt in fake_admin.create_topics_calls[0])
    assert topic_names == ["scratch.primary", "scratch.quarantine"]


@pytest.mark.unit
def test_ensure_topics_is_idempotent_on_already_exists(
    producer_config: TripCompletedProducerConfig, fake_admin_topics_exist: _FakeAdminClient,
) -> None:
    """``ensure_topics`` swallows the "topic already exists" error from the broker."""
    # pylint: disable=import-outside-toplevel
    from nyc_cab_events.producer.trip_completed import ensure_topics
    ensure_topics(producer_config)  # must not raise
    assert len(fake_admin_topics_exist.create_topics_calls) == 1


@pytest.mark.unit
def test_ensure_topics_propagates_other_kafka_errors(
    monkeypatch: pytest.MonkeyPatch, producer_config: TripCompletedProducerConfig,
) -> None:
    """A KafkaException with a code other than TOPIC_ALREADY_EXISTS propagates.

    Regression guardrail: the previous implementation matched on the
    string ``"already exists"`` in the exception message, which could
    silently swallow other failures (auth errors, network errors) whose
    rendered text happened to contain that substring. The current
    implementation inspects ``KafkaError.code()`` so unrelated errors
    propagate.
    """
    # pylint: disable=import-outside-toplevel
    from confluent_kafka import KafkaError as _KafkaError
    from confluent_kafka import KafkaException as _KafkaException
    from nyc_cab_events.producer.trip_completed import ensure_topics

    class _AuthFailureFuture:
        # pylint: disable=too-few-public-methods
        def result(self) -> None:
            """Raise an authentication-failure KafkaException unconditionally."""
            raise _KafkaException(_KafkaError(_KafkaError._AUTHENTICATION))  # pylint: disable=protected-access

    class _AuthFailureAdmin:
        # pylint: disable=too-few-public-methods
        def create_topics(self, new_topics: list) -> dict:
            """Return a fake future per topic; each raises an auth error on .result()."""
            return {nt.topic: _AuthFailureFuture() for nt in new_topics}

    monkeypatch.setattr(
        "nyc_cab_events.producer.trip_completed._make_admin_client",
        lambda _config: _AuthFailureAdmin(),
    )
    with pytest.raises(_KafkaException):
        ensure_topics(producer_config)


@pytest.fixture(scope="module")
def spark():
    """A local SparkSession for driver-level producer tests."""
    # pylint: disable=import-outside-toplevel
    from pyspark.sql import SparkSession as _SparkSession
    session = (
        _SparkSession.builder
        .appName("nyc_cab_events_producer_tests")
        .master("local[1]")
        .config("spark.sql.shuffle.partitions", "1")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )
    yield session
    session.stop()


def _write_silver_partition(spark, partition_path: Path, rows: list[dict[str, Any]]) -> None:
    """Write ``rows`` as a Silver-shaped parquet partition under ``partition_path``."""
    partition_path.mkdir(parents=True, exist_ok=True)
    df = spark.createDataFrame(rows)
    df.write.mode("overwrite").parquet(str(partition_path))


@pytest.mark.spark
def test_driver_emits_one_event_per_clean_row(
    spark, tmp_path: Path, silver_row: dict[str, Any],
    producer_config: TripCompletedProducerConfig, fake_producer: _FakeProducer,
) -> None:
    """The driver emits one primary-topic message per row and returns a reconciled result."""
    partition_path = tmp_path / "silver"
    _write_silver_partition(spark, partition_path, [silver_row, silver_row, silver_row])

    result = produce_trip_completed_events(
        spark, partition_path, producer_config, "yellow", 2023, 1,
    )

    assert result.silver_read_count == 3
    assert result.events_emitted == 3
    assert result.events_quarantined == 0
    assert len(fake_producer.messages) == 3
    assert all(m["topic"] == TRIP_COMPLETED_TOPIC for m in fake_producer.messages)


@pytest.mark.spark
def test_driver_quarantines_invalid_row(
    spark, tmp_path: Path, silver_row: dict[str, Any],
    producer_config: TripCompletedProducerConfig, fake_producer: _FakeProducer,
) -> None:
    """The driver routes contract-violating rows to the quarantine topic."""
    partition_path = tmp_path / "silver"
    bad = {**silver_row, "fare_amount": -1.0}
    _write_silver_partition(spark, partition_path, [silver_row, bad])

    result = produce_trip_completed_events(
        spark, partition_path, producer_config, "yellow", 2023, 1,
    )

    assert result.silver_read_count == 2
    assert result.events_emitted == 1
    assert result.events_quarantined == 1
    topics = sorted(m["topic"] for m in fake_producer.messages)
    assert topics == [TRIP_COMPLETED_TOPIC, TRIP_COMPLETED_QUARANTINE_TOPIC]


@pytest.mark.spark
def test_driver_reconciliation_invariant_holds(
    spark, tmp_path: Path, silver_row: dict[str, Any],
    producer_config: TripCompletedProducerConfig, fake_producer: _FakeProducer,
) -> None:
    """``silver_read_count == events_emitted + events_quarantined`` holds end-to-end."""
    partition_path = tmp_path / "silver"
    rows = [silver_row, silver_row, {**silver_row, "passenger_count": 99}]
    _write_silver_partition(spark, partition_path, rows)

    result = produce_trip_completed_events(
        spark, partition_path, producer_config, "yellow", 2023, 1,
    )

    assert result.silver_read_count == result.events_emitted + result.events_quarantined
    _ = fake_producer  # silence unused-var lint; fixture is required for monkeypatch
