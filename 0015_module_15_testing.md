# Module 15 — Testing Kafka Applications

## Overview

Testing Kafka applications requires thinking at three levels:

1. **Unit tests** — test your business logic without Kafka. Mock the producer and consumer so tests are fast and deterministic.
2. **Integration tests** — test the full produce/consume flow with a real Kafka broker running in Docker.
3. **End-to-end tests** — test an entire pipeline (multiple services, topics, consumers) together.

This module focuses on levels 1 and 2, which cover the vast majority of practical testing needs.

```bash
pip install pytest pytest-asyncio testcontainers confluent-kafka
```

---

## 15.1 What to Test and What Not to Test

### Test this:
- Your message processing logic (the function that receives a decoded message and does something with it)
- Serialization and deserialization of your specific message types
- Error handling branches (deserialization failure, processing failure, DLQ routing)
- Consumer offset commit strategy (when and what gets committed)
- Producer retry and callback behaviour

### Do not test this:
- That Kafka delivers messages (Kafka is a proven library — trust it)
- Internal Kafka mechanics like partition assignment or rebalancing
- The confluent-kafka library API itself

---

## 15.2 Unit Testing — Business Logic in Isolation

The key principle: **extract your processing logic into a plain Python function**. That function takes a decoded message (a dict, dataclass, etc.) and returns a result. Test that function directly — no Kafka involved.

### Example Application Structure

```python
# order_processor.py

from dataclasses import dataclass
from typing import Optional

@dataclass
class Order:
    order_id: str
    user_id: str
    amount: float
    currency: str = "USD"

@dataclass
class EnrichedOrder:
    order_id: str
    user_id: str
    amount: float
    currency: str
    amount_usd: float
    tier: str

# Exchange rates (in production: fetched from an external service)
EXCHANGE_RATES = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27}

# User tiers
def get_user_tier(amount_usd: float) -> str:
    if amount_usd >= 500:
        return "premium"
    elif amount_usd >= 100:
        return "standard"
    return "basic"

def enrich_order(order: Order) -> EnrichedOrder:
    """
    Convert an Order to an EnrichedOrder.
    This is the function we test. No Kafka, no I/O.
    """
    rate = EXCHANGE_RATES.get(order.currency)
    if rate is None:
        raise ValueError(f"Unknown currency: {order.currency}")

    amount_usd = round(order.amount * rate, 2)
    return EnrichedOrder(
        order_id=order.order_id,
        user_id=order.user_id,
        amount=order.amount,
        currency=order.currency,
        amount_usd=amount_usd,
        tier=get_user_tier(amount_usd),
    )
```

### Unit Tests for Processing Logic

```python
# test_order_processor.py

import pytest
from order_processor import Order, EnrichedOrder, enrich_order

class TestEnrichOrder:

    def test_usd_order_no_conversion(self):
        order = Order(order_id="ord-1", user_id="u-1", amount=150.0, currency="USD")
        result = enrich_order(order)
        assert result.amount_usd == 150.0

    def test_eur_order_converted(self):
        order = Order(order_id="ord-2", user_id="u-2", amount=100.0, currency="EUR")
        result = enrich_order(order)
        assert result.amount_usd == 108.0  # 100 * 1.08

    def test_gbp_order_converted(self):
        order = Order(order_id="ord-3", user_id="u-3", amount=100.0, currency="GBP")
        result = enrich_order(order)
        assert result.amount_usd == 127.0

    def test_unknown_currency_raises(self):
        order = Order(order_id="ord-4", user_id="u-4", amount=50.0, currency="JPY")
        with pytest.raises(ValueError, match="Unknown currency: JPY"):
            enrich_order(order)

    def test_tier_basic(self):
        order = Order(order_id="ord-5", user_id="u-5", amount=50.0, currency="USD")
        result = enrich_order(order)
        assert result.tier == "basic"

    def test_tier_standard(self):
        order = Order(order_id="ord-6", user_id="u-6", amount=200.0, currency="USD")
        result = enrich_order(order)
        assert result.tier == "standard"

    def test_tier_premium(self):
        order = Order(order_id="ord-7", user_id="u-7", amount=600.0, currency="USD")
        result = enrich_order(order)
        assert result.tier == "premium"

    def test_output_fields_are_present(self):
        order = Order(order_id="ord-8", user_id="u-8", amount=99.99, currency="USD")
        result = enrich_order(order)
        assert result.order_id == "ord-8"
        assert result.user_id == "u-8"
        assert result.currency == "USD"
```

Run:
```bash
pytest test_order_processor.py -v
```

---

## 15.3 Unit Testing — Mocking the Producer

When testing code that calls `producer.produce()`, use `unittest.mock.MagicMock` to replace the producer. You can then assert on the calls that were made.

### Application Code

```python
# order_publisher.py

import json
from dataclasses import asdict
from confluent_kafka import Producer
from order_processor import Order, enrich_order

def publish_enriched_order(producer: Producer, topic: str, order: Order):
    """Enrich an order and publish it. Accepts producer as a dependency."""
    try:
        enriched = enrich_order(order)
        producer.produce(
            topic=topic,
            key=enriched.order_id,
            value=json.dumps(asdict(enriched)).encode("utf-8"),
        )
    except ValueError as e:
        # Route bad orders to a DLQ
        producer.produce(
            topic=f"{topic}.dlq",
            key=order.order_id,
            value=json.dumps({"error": str(e), "original": asdict(order)}).encode("utf-8"),
        )
```

### Tests with Mocked Producer

```python
# test_order_publisher.py

import json
import pytest
from unittest.mock import MagicMock, call
from order_processor import Order
from order_publisher import publish_enriched_order

class TestPublishEnrichedOrder:

    def setup_method(self):
        # Fresh mock producer for every test
        self.mock_producer = MagicMock()
        self.topic = "orders"

    def test_publishes_to_correct_topic(self):
        order = Order("ord-1", "u-1", 200.0, "USD")
        publish_enriched_order(self.mock_producer, self.topic, order)

        self.mock_producer.produce.assert_called_once()
        call_kwargs = self.mock_producer.produce.call_args.kwargs
        assert call_kwargs["topic"] == "orders"

    def test_publishes_with_order_id_as_key(self):
        order = Order("ord-1", "u-1", 200.0, "USD")
        publish_enriched_order(self.mock_producer, self.topic, order)

        call_kwargs = self.mock_producer.produce.call_args.kwargs
        assert call_kwargs["key"] == "ord-1"

    def test_payload_contains_enriched_fields(self):
        order = Order("ord-1", "u-1", 200.0, "USD")
        publish_enriched_order(self.mock_producer, self.topic, order)

        call_kwargs = self.mock_producer.produce.call_args.kwargs
        payload = json.loads(call_kwargs["value"].decode("utf-8"))
        assert payload["amount_usd"] == 200.0
        assert payload["tier"] == "standard"
        assert payload["currency"] == "USD"

    def test_invalid_currency_routes_to_dlq(self):
        order = Order("ord-bad", "u-1", 50.0, "JPY")
        publish_enriched_order(self.mock_producer, self.topic, order)

        # Should have been called once, with the DLQ topic
        self.mock_producer.produce.assert_called_once()
        call_kwargs = self.mock_producer.produce.call_args.kwargs
        assert call_kwargs["topic"] == "orders.dlq"

    def test_dlq_payload_contains_error_and_original(self):
        order = Order("ord-bad", "u-1", 50.0, "JPY")
        publish_enriched_order(self.mock_producer, self.topic, order)

        call_kwargs = self.mock_producer.produce.call_args.kwargs
        payload = json.loads(call_kwargs["value"].decode("utf-8"))
        assert "error" in payload
        assert "original" in payload
        assert payload["original"]["order_id"] == "ord-bad"

    def test_never_publishes_twice_for_single_order(self):
        order = Order("ord-1", "u-1", 200.0, "USD")
        publish_enriched_order(self.mock_producer, self.topic, order)

        assert self.mock_producer.produce.call_count == 1
```

---

## 15.4 Unit Testing — Mocking the Consumer

Testing consumer loop logic requires simulating what `consumer.poll()` returns. This is done by configuring a mock to return a sequence of messages followed by `None`.

### Application Code

```python
# order_consumer_loop.py

import json
import signal
from confluent_kafka import Consumer, Message

class OrderConsumerLoop:
    def __init__(self, consumer: Consumer, processor, topic: str):
        self.consumer = consumer
        self.processor = processor  # callable: takes a dict, returns None
        self.topic = topic
        self.running = True
        self.processed_count = 0
        self.error_count = 0

    def run(self, max_empty_polls: int = 3):
        self.consumer.subscribe([self.topic])
        empty_polls = 0

        while self.running and empty_polls < max_empty_polls:
            msg = self.consumer.poll(timeout=1.0)

            if msg is None:
                empty_polls += 1
                continue

            empty_polls = 0

            if msg.error():
                self.error_count += 1
                continue

            try:
                data = json.loads(msg.value().decode("utf-8"))
                self.processor(data)
                self.processed_count += 1
                self.consumer.commit(message=msg, asynchronous=False)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                self.error_count += 1
            except Exception as e:
                self.error_count += 1

        self.consumer.close()
```

### Fake Message Factory

```python
# test_helpers.py

from unittest.mock import MagicMock
import json

def make_fake_message(
    value: dict = None,
    key: str = None,
    topic: str = "test-topic",
    partition: int = 0,
    offset: int = 0,
    error=None,
):
    """Create a MagicMock that behaves like a confluent_kafka.Message."""
    msg = MagicMock()
    msg.error.return_value = error
    msg.value.return_value = json.dumps(value).encode("utf-8") if value else None
    msg.key.return_value = key.encode("utf-8") if key else None
    msg.topic.return_value = topic
    msg.partition.return_value = partition
    msg.offset.return_value = offset
    return msg
```

### Tests for Consumer Loop

```python
# test_order_consumer_loop.py

import pytest
from unittest.mock import MagicMock, patch, call
from order_consumer_loop import OrderConsumerLoop
from test_helpers import make_fake_message

class TestOrderConsumerLoop:

    def setup_method(self):
        self.mock_consumer = MagicMock()
        self.mock_processor = MagicMock()
        self.topic = "orders"

    def _make_loop(self):
        return OrderConsumerLoop(self.mock_consumer, self.mock_processor, self.topic)

    def test_subscribes_to_correct_topic(self):
        self.mock_consumer.poll.return_value = None
        loop = self._make_loop()
        loop.run(max_empty_polls=1)
        self.mock_consumer.subscribe.assert_called_once_with(["orders"])

    def test_processes_single_message(self):
        msg = make_fake_message(value={"order_id": "ord-1", "amount": 99.0})
        self.mock_consumer.poll.side_effect = [msg, None, None, None]
        loop = self._make_loop()
        loop.run(max_empty_polls=3)

        self.mock_processor.assert_called_once_with({"order_id": "ord-1", "amount": 99.0})
        assert loop.processed_count == 1
        assert loop.error_count == 0

    def test_processes_multiple_messages(self):
        messages = [
            make_fake_message(value={"order_id": f"ord-{i}"}, offset=i)
            for i in range(5)
        ]
        self.mock_consumer.poll.side_effect = messages + [None, None, None]
        loop = self._make_loop()
        loop.run(max_empty_polls=3)

        assert loop.processed_count == 5
        assert self.mock_processor.call_count == 5

    def test_commits_after_each_message(self):
        msg = make_fake_message(value={"order_id": "ord-1"})
        self.mock_consumer.poll.side_effect = [msg, None, None, None]
        loop = self._make_loop()
        loop.run(max_empty_polls=3)

        self.mock_consumer.commit.assert_called_once_with(message=msg, asynchronous=False)

    def test_invalid_json_increments_error_count(self):
        bad_msg = MagicMock()
        bad_msg.error.return_value = None
        bad_msg.value.return_value = b"not valid json {"
        self.mock_consumer.poll.side_effect = [bad_msg, None, None, None]
        loop = self._make_loop()
        loop.run(max_empty_polls=3)

        assert loop.error_count == 1
        assert loop.processed_count == 0
        self.mock_processor.assert_not_called()

    def test_broker_error_increments_error_count(self):
        from confluent_kafka import KafkaError
        mock_error = MagicMock()
        mock_error.code.return_value = KafkaError.UNKNOWN_TOPIC_OR_PART
        error_msg = make_fake_message(error=mock_error)
        self.mock_consumer.poll.side_effect = [error_msg, None, None, None]
        loop = self._make_loop()
        loop.run(max_empty_polls=3)

        assert loop.error_count == 1
        assert loop.processed_count == 0

    def test_processor_exception_increments_error_count(self):
        msg = make_fake_message(value={"order_id": "ord-bad"})
        self.mock_processor.side_effect = RuntimeError("database unavailable")
        self.mock_consumer.poll.side_effect = [msg, None, None, None]
        loop = self._make_loop()
        loop.run(max_empty_polls=3)

        assert loop.error_count == 1
        assert loop.processed_count == 0

    def test_closes_consumer_when_done(self):
        self.mock_consumer.poll.return_value = None
        loop = self._make_loop()
        loop.run(max_empty_polls=1)
        self.mock_consumer.close.assert_called_once()
```

---

## 15.5 Unit Testing — Serialization and Deserialization

Test your serialization round-trip without Kafka:

```python
# test_serialization.py

import json
import struct
import pytest
from dataclasses import dataclass, asdict

@dataclass
class SensorReading:
    sensor_id: str
    temperature: float
    humidity: float
    timestamp: int

def serialize_sensor(reading: SensorReading) -> bytes:
    return json.dumps(asdict(reading)).encode("utf-8")

def deserialize_sensor(data: bytes) -> SensorReading:
    d = json.loads(data.decode("utf-8"))
    return SensorReading(**d)

class TestSensorSerialization:

    def test_roundtrip(self):
        original = SensorReading(
            sensor_id="sensor-42",
            temperature=23.5,
            humidity=60.0,
            timestamp=1700000000,
        )
        serialized = serialize_sensor(original)
        recovered = deserialize_sensor(serialized)
        assert recovered == original

    def test_serializes_to_bytes(self):
        reading = SensorReading("s-1", 22.0, 55.0, 1700000000)
        result = serialize_sensor(reading)
        assert isinstance(result, bytes)

    def test_invalid_bytes_raises(self):
        with pytest.raises((json.JSONDecodeError, KeyError, TypeError)):
            deserialize_sensor(b"garbage")

    def test_missing_field_raises(self):
        data = json.dumps({"sensor_id": "s-1", "temperature": 22.0}).encode("utf-8")
        with pytest.raises(TypeError):
            deserialize_sensor(data)  # missing humidity and timestamp
```

---

## 15.6 Integration Testing with testcontainers

`testcontainers` spins up a real Docker container (Kafka) for your tests and tears it down automatically. Tests run against a real broker — no mocking.

```bash
pip install testcontainers
```

### Kafka Container Fixture

```python
# conftest.py

import pytest
from testcontainers.kafka import KafkaContainer

@pytest.fixture(scope="session")
def kafka_container():
    """
    Starts a Kafka container once per test session.
    'scope=session' means all tests in the session share one container,
    which is faster than starting/stopping a container per test.
    """
    with KafkaContainer("apache/kafka:latest") as kafka:
        yield kafka

@pytest.fixture(scope="session")
def kafka_bootstrap_servers(kafka_container):
    """Returns the bootstrap server address for the running container."""
    return kafka_container.get_bootstrap_server()
```

### Integration Test: Produce and Consume

```python
# test_integration.py

import json
import time
import pytest
from confluent_kafka import Producer, Consumer
from confluent_kafka.admin import AdminClient, NewTopic

def create_topic(bootstrap_servers: str, topic: str, partitions: int = 1):
    admin = AdminClient({"bootstrap.servers": bootstrap_servers})
    future = admin.create_topics([NewTopic(topic, partitions, 1)])
    future[topic].result()

def produce_messages(bootstrap_servers: str, topic: str, messages: list[dict]) -> None:
    producer = Producer({"bootstrap.servers": bootstrap_servers})
    for msg in messages:
        producer.produce(topic, value=json.dumps(msg).encode("utf-8"))
    producer.flush()

def consume_all(bootstrap_servers: str, topic: str, timeout: float = 10.0) -> list[dict]:
    """Consume all available messages from a topic, waiting up to `timeout` seconds."""
    consumer = Consumer({
        "bootstrap.servers": bootstrap_servers,
        "group.id": f"test-consumer-{time.time()}",  # unique group per call
        "auto.offset.reset": "earliest",
        "enable.auto.commit": True,
    })
    consumer.subscribe([topic])

    collected = []
    deadline = time.time() + timeout
    empty_count = 0

    while time.time() < deadline:
        msg = consumer.poll(timeout=1.0)
        if msg is None:
            empty_count += 1
            if empty_count >= 3:
                break  # 3 consecutive empty polls = done
            continue
        empty_count = 0
        if not msg.error():
            collected.append(json.loads(msg.value().decode("utf-8")))

    consumer.close()
    return collected


class TestKafkaIntegration:

    def test_produce_and_consume_single_message(self, kafka_bootstrap_servers):
        topic = "test-single"
        create_topic(kafka_bootstrap_servers, topic)
        produce_messages(kafka_bootstrap_servers, topic, [{"msg": "hello"}])
        messages = consume_all(kafka_bootstrap_servers, topic)
        assert len(messages) == 1
        assert messages[0]["msg"] == "hello"

    def test_produce_and_consume_multiple_messages(self, kafka_bootstrap_servers):
        topic = "test-multiple"
        create_topic(kafka_bootstrap_servers, topic)
        sent = [{"id": i, "value": f"item-{i}"} for i in range(10)]
        produce_messages(kafka_bootstrap_servers, topic, sent)
        received = consume_all(kafka_bootstrap_servers, topic)
        assert len(received) == 10
        received_ids = {m["id"] for m in received}
        assert received_ids == set(range(10))

    def test_consumer_group_offset_tracking(self, kafka_bootstrap_servers):
        """Second consume with same group.id should return no messages."""
        topic = "test-offset-tracking"
        group_id = "tracking-group"
        create_topic(kafka_bootstrap_servers, topic)
        produce_messages(kafka_bootstrap_servers, topic, [{"n": 1}, {"n": 2}])

        # First consumer — reads everything
        consumer1 = Consumer({
            "bootstrap.servers": kafka_bootstrap_servers,
            "group.id": group_id,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": True,
        })
        consumer1.subscribe([topic])
        first_batch = []
        empty = 0
        while empty < 3:
            msg = consumer1.poll(timeout=1.0)
            if msg is None:
                empty += 1
                continue
            empty = 0
            if not msg.error():
                first_batch.append(json.loads(msg.value().decode()))
        consumer1.close()
        assert len(first_batch) == 2

        # Second consumer — same group, should see no new messages
        consumer2 = Consumer({
            "bootstrap.servers": kafka_bootstrap_servers,
            "group.id": group_id,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": True,
        })
        consumer2.subscribe([topic])
        second_batch = []
        empty = 0
        while empty < 3:
            msg = consumer2.poll(timeout=1.0)
            if msg is None:
                empty += 1
                continue
            empty = 0
            if not msg.error():
                second_batch.append(json.loads(msg.value().decode()))
        consumer2.close()
        assert len(second_batch) == 0  # offsets were committed by consumer1
```

Run integration tests:
```bash
pytest test_integration.py -v
# Note: first run downloads the Docker image — takes a minute
```

---

## 15.7 Integration Testing — End-to-End Pipeline

Test an entire pipeline: produce to topic A → pipeline processes → assert on topic B.

```python
# test_pipeline_integration.py

import json
import time
import threading
import pytest
from confluent_kafka import Producer, Consumer
from confluent_kafka.admin import AdminClient, NewTopic
from order_processor import Order, enrich_order

# Minimal pipeline consumer that enriches and republishes
def run_pipeline(bootstrap_servers: str, input_topic: str, output_topic: str, stop_event):
    from confluent_kafka import Consumer, Producer
    consumer = Consumer({
        "bootstrap.servers": bootstrap_servers,
        "group.id": "pipeline-group",
        "auto.offset.reset": "earliest",
    })
    producer = Producer({"bootstrap.servers": bootstrap_servers})
    consumer.subscribe([input_topic])

    while not stop_event.is_set():
        msg = consumer.poll(timeout=0.5)
        if msg is None or msg.error():
            continue
        raw = json.loads(msg.value().decode())
        try:
            order = Order(**raw)
            enriched = enrich_order(order)
            from dataclasses import asdict
            producer.produce(
                output_topic,
                key=enriched.order_id,
                value=json.dumps(asdict(enriched)).encode(),
            )
            producer.poll(0)
            consumer.commit(message=msg, asynchronous=False)
        except Exception:
            pass

    producer.flush()
    consumer.close()


class TestPipelineIntegration:

    def test_enrichment_pipeline(self, kafka_bootstrap_servers):
        input_topic = "raw-orders-test"
        output_topic = "enriched-orders-test"

        admin = AdminClient({"bootstrap.servers": kafka_bootstrap_servers})
        for t in [input_topic, output_topic]:
            admin.create_topics([NewTopic(t, 1, 1)])[t].result()

        # Start the pipeline in a background thread
        stop_event = threading.Event()
        pipeline_thread = threading.Thread(
            target=run_pipeline,
            args=(kafka_bootstrap_servers, input_topic, output_topic, stop_event),
            daemon=True,
        )
        pipeline_thread.start()

        # Produce test orders
        producer = Producer({"bootstrap.servers": kafka_bootstrap_servers})
        test_orders = [
            {"order_id": "o1", "user_id": "u1", "amount": 600.0, "currency": "USD"},
            {"order_id": "o2", "user_id": "u2", "amount": 50.0, "currency": "EUR"},
        ]
        for order in test_orders:
            producer.produce(input_topic, value=json.dumps(order).encode())
        producer.flush()

        # Wait for the pipeline to process and produce to output_topic
        time.sleep(3)
        stop_event.set()
        pipeline_thread.join(timeout=5)

        # Consume from the output topic and assert
        consumer = Consumer({
            "bootstrap.servers": kafka_bootstrap_servers,
            "group.id": "verifier",
            "auto.offset.reset": "earliest",
        })
        consumer.subscribe([output_topic])
        results = {}
        empty = 0
        while empty < 3:
            msg = consumer.poll(timeout=1.0)
            if msg is None:
                empty += 1
                continue
            empty = 0
            if not msg.error():
                data = json.loads(msg.value().decode())
                results[data["order_id"]] = data
        consumer.close()

        # Assertions
        assert "o1" in results, "Order o1 not found in output"
        assert "o2" in results, "Order o2 not found in output"
        assert results["o1"]["tier"] == "premium"   # 600 USD >= 500
        assert results["o1"]["amount_usd"] == 600.0
        assert results["o2"]["tier"] == "basic"     # 50 EUR = 54 USD < 100
        assert results["o2"]["currency"] == "EUR"
```

---

## 15.8 pytest Configuration and Markers

### conftest.py (complete)

```python
# conftest.py

import pytest
from testcontainers.kafka import KafkaContainer

# Mark integration tests so they can be skipped during fast unit test runs
def pytest_configure(config):
    config.addinivalue_line("markers", "integration: mark test as requiring a Kafka container")
    config.addinivalue_line("markers", "slow: mark test as slow")

@pytest.fixture(scope="session")
def kafka_container():
    with KafkaContainer("apache/kafka:latest") as kafka:
        yield kafka

@pytest.fixture(scope="session")
def kafka_bootstrap_servers(kafka_container):
    return kafka_container.get_bootstrap_server()
```

### pytest.ini

```ini
[pytest]
# Show test duration
addopts = -v --tb=short --durations=10

# Default markers
markers =
    integration: tests that require a running Kafka container
    slow: tests that take more than 5 seconds
```

### Running Specific Test Subsets

```bash
# Run only unit tests (fast, no Docker)
pytest test_order_processor.py test_order_publisher.py test_order_consumer_loop.py -v

# Run only integration tests
pytest test_integration.py test_pipeline_integration.py -v

# Run all tests
pytest -v

# Skip slow tests
pytest -m "not slow" -v

# Run with verbose output and show print statements
pytest -v -s
```

---

## 15.9 Common Pitfalls in Kafka Testing

### Pitfall 1: Reusing Consumer Group IDs Between Tests

If two tests use the same `group.id` and the first test commits offsets, the second test starts from those committed offsets instead of from the beginning.

**Fix:** Generate a unique group ID per test:

```python
import time, uuid

consumer = Consumer({
    "bootstrap.servers": bootstrap_servers,
    "group.id": f"test-{uuid.uuid4()}",
    "auto.offset.reset": "earliest",
})
```

### Pitfall 2: Topic State Leaking Between Tests

If tests share topics, messages from one test can appear in another test's consume loop.

**Fix:** Use unique topic names per test, or use `scope="function"` fixtures that create and delete topics for each test. The easiest approach: include the test name or a UUID in the topic name.

```python
@pytest.fixture
def unique_topic(kafka_bootstrap_servers, request):
    topic = f"test-{request.node.name}-{int(time.time())}"
    admin = AdminClient({"bootstrap.servers": kafka_bootstrap_servers})
    admin.create_topics([NewTopic(topic, 1, 1)])[topic].result()
    yield topic
    admin.delete_topics([topic])[topic].result()
```

### Pitfall 3: Not Waiting Long Enough for Messages

Kafka's rebalance + message delivery takes time. A `poll()` that returns `None` does not mean the topic is empty — the consumer may still be rebalancing.

**Fix:** Use a timeout-based loop with multiple consecutive empty polls before giving up:

```python
def consume_with_timeout(consumer, max_empty=5, poll_timeout=2.0):
    results = []
    empty = 0
    while empty < max_empty:
        msg = consumer.poll(timeout=poll_timeout)
        if msg is None:
            empty += 1
        elif not msg.error():
            results.append(msg)
            empty = 0
    return results
```

### Pitfall 4: Forgetting to flush() the Producer

`produce()` is asynchronous — it queues the message locally. If you don't call `flush()` before your test assertions, the message may not have been delivered yet.

```python
# WRONG
producer.produce(topic, value=b"hello")
messages = consume_all(bootstrap_servers, topic)  # may find 0 messages

# CORRECT
producer.produce(topic, value=b"hello")
producer.flush()  # wait for delivery
messages = consume_all(bootstrap_servers, topic)  # finds the message
```

### Pitfall 5: Testing with Auto-Commit and Then Asserting Offset State

Auto-commit runs on a timer. If your test inspects committed offsets immediately after consuming, the timer may not have fired yet.

**Fix:** Use `enable.auto.commit: False` and commit explicitly in tests that care about offset state.

### Pitfall 6: Slow Tests From Starting a New Container Per Test

`KafkaContainer` takes 5–15 seconds to start. If you use `scope="function"` (default), every test function gets its own container.

**Fix:** Use `scope="session"` for the container fixture, as shown in section 15.6. All tests share one container; tests are responsible for using unique topic names to avoid interference.
