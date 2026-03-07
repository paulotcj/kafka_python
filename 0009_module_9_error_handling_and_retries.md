# Module 9 — Error Handling and Retries

Error handling in Kafka is almost entirely a developer concern. The broker is
remarkably resilient by design — most failure modes happen at the client level (your
Python code), not at the broker level. This module covers every class of error you
will encounter: producer delivery failures, consumer processing failures, poison pill
messages, and retry patterns that scale from simple to production-grade.

The central tension in Kafka error handling is the **at-least-once guarantee**: Kafka
ensures no message is lost, but it cannot guarantee your processing code succeeds on
the first attempt. Your job is to handle failures without losing messages and without
processing them incorrectly.

**Prerequisites:**
- Kafka is running locally via Docker (see Module 2)
- `confluent-kafka` is installed: `pip install confluent-kafka`
- Topics for the examples:

```bash
docker exec kafka /opt/kafka/bin/kafka-topics.sh --create \
  --topic orders --bootstrap-server localhost:9092 --partitions 3 --replication-factor 1

docker exec kafka /opt/kafka/bin/kafka-topics.sh --create \
  --topic orders.retry-1 --bootstrap-server localhost:9092 --partitions 3 --replication-factor 1

docker exec kafka /opt/kafka/bin/kafka-topics.sh --create \
  --topic orders.retry-2 --bootstrap-server localhost:9092 --partitions 3 --replication-factor 1

docker exec kafka /opt/kafka/bin/kafka-topics.sh --create \
  --topic orders.dlq --bootstrap-server localhost:9092 --partitions 3 --replication-factor 1
```

---

## 9.1 Producer Errors

### The two classes of producer error

Producer errors fall into two categories with fundamentally different handling
strategies:

| Class | Examples | What to do |
|---|---|---|
| **Retriable** | Network timeout, leader not available, broker temporarily down | The client retries automatically — configure how many times and for how long |
| **Non-retriable** | Message too large, invalid topic, serialization failure, authorization denied | Retrying will not help — fail fast, log, alert |

### How the producer handles retriable errors internally

```
producer.produce("orders", value=b"order-data")
         |
         v
    [local buffer]
         |
         v
    network send ──────► broker
                              |
                         broker down? ──► wait + retry (up to `retries` times)
                              |                        (up to `delivery.timeout.ms` total)
                         success ──────► delivery callback(err=None)
                              |
                         permanent fail ► delivery callback(err=KafkaError)
```

By the time your delivery callback is called with an error, the producer has already
exhausted all retries. There is nothing more it can do automatically.

### Configuring retry behaviour

```python
from confluent_kafka import Producer

producer = Producer({
    "bootstrap.servers": "localhost:9092",

    # retries — how many times to retry a failed send before giving up.
    # Default: INT_MAX (virtually infinite) when enable.idempotence=True.
    # The producer retries automatically; you do not write retry loops.
    "retries": 5,

    # delivery.timeout.ms — total wall-clock time budget for one message,
    # including all retries and waiting time. If the message is not delivered
    # within this window, it fails permanently regardless of retries remaining.
    # Default: 120000 (2 minutes).
    "delivery.timeout.ms": 30000,   # give up after 30 seconds total

    # retry.backoff.ms — how long to wait between retry attempts.
    # Default: 100ms. Increase to reduce broker pressure during an outage.
    "retry.backoff.ms": 250,

    # retry.backoff.max.ms — maximum backoff ceiling (exponential backoff).
    # Default: 1000ms.
    "retry.backoff.max.ms": 2000,

    # request.timeout.ms — timeout for a single broker request.
    # Must be less than delivery.timeout.ms.
    "request.timeout.ms": 10000,
})
```

### Handling errors in the delivery callback

```python
from confluent_kafka import Producer, KafkaError
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

failed_messages = []

def on_delivery(err, msg):
    if err is None:
        # Success — message confirmed on the broker
        logger.debug(f"Delivered to partition={msg.partition()} offset={msg.offset()}")
        return

    # By here, all retries are exhausted — this is a permanent failure
    error_code = err.code()

    if error_code == KafkaError.MSG_TIMED_OUT:
        # delivery.timeout.ms expired — broker was unreachable for too long
        logger.error(f"Delivery timed out: {msg.topic()} key={msg.key()} — broker unreachable")

    elif error_code == KafkaError._MSG_TIMED_OUT:
        # Local queue timeout — message sat in the buffer too long without being sent
        logger.error(f"Local queue timeout: {msg.topic()} — producer buffer may be full")

    elif error_code == KafkaError.UNKNOWN_TOPIC_OR_PART:
        # Topic does not exist and auto-creation is disabled
        logger.error(f"Topic not found: {msg.topic()} — create it before producing")

    elif error_code == KafkaError.MSG_SIZE_TOO_LARGE:
        # Message exceeds max.message.bytes on the broker
        logger.error(f"Message too large: {len(msg.value())} bytes")

    else:
        logger.error(f"Unhandled delivery error [{error_code}]: {err} — topic={msg.topic()}")

    # Save the failed message for later analysis or dead-lettering
    failed_messages.append({
        "topic": msg.topic(),
        "key": msg.key(),
        "value": msg.value(),
        "error": str(err),
        "error_code": error_code,
    })


producer = Producer({
    "bootstrap.servers": "localhost:9092",
    "delivery.timeout.ms": 10000,
    "retries": 3,
})

for i in range(10):
    producer.produce("orders", value=f"order-{i}".encode(), on_delivery=on_delivery)
    producer.poll(0)

producer.flush()

if failed_messages:
    logger.warning(f"{len(failed_messages)} messages failed permanently")
    for m in failed_messages:
        logger.warning(f"  Failed: {m}")
```

### Handling BufferError — the local queue is full

`BufferError` is raised synchronously by `produce()` (not via callback) when the
producer's internal send buffer is full. This happens when you produce faster than
the broker can accept:

```python
from confluent_kafka import Producer, BufferError
import time

producer = Producer({
    "bootstrap.servers": "localhost:9092",
    # buffer.memory — total bytes for the producer's send buffer.
    # Default: 33554432 (32MB).
    "buffer.memory": 33554432,
    # max.block.ms — how long produce() blocks waiting for buffer space.
    # Default: 60000 (60s). Set to 0 to raise BufferError immediately.
    "linger.ms": 100,
})

def produce_with_backpressure(producer, topic, value, max_retries=5):
    """
    Produce a message, handling BufferError with exponential backoff.
    This prevents the producer from crashing when the broker is slow.
    """
    for attempt in range(max_retries):
        try:
            producer.produce(topic, value=value)
            return  # success
        except BufferError:
            wait = 0.1 * (2 ** attempt)   # 0.1s, 0.2s, 0.4s, 0.8s, 1.6s
            print(f"Buffer full — waiting {wait:.1f}s before retry {attempt + 1}")
            # poll() drains the buffer by sending queued messages to the broker
            producer.poll(wait)

    raise RuntimeError(f"Could not produce after {max_retries} attempts — buffer persistently full")


for i in range(1000):
    produce_with_backpressure(producer, "orders", f"order-{i}".encode())

producer.flush()
print("Done.")
```

---

## 9.2 Consumer Errors

### Deserialization errors

A deserialization error happens when a message's bytes cannot be converted to the
expected format. With JSON this is a `json.JSONDecodeError`; with Avro/Protobuf it is
a schema mismatch.

The danger: if your consumer crashes on a bad message and restarts, it will read the
same message again and crash again — an infinite loop that blocks all progress on that
partition.

```python
import json
from confluent_kafka import Consumer, Producer

TOPIC = "orders"
DLQ_TOPIC = "orders.dlq"

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "order-processor",
    "auto.offset.reset": "earliest",
    "enable.auto.commit": False,
})
dlq_producer = Producer({"bootstrap.servers": "localhost:9092"})

consumer.subscribe([TOPIC])

def send_to_dlq(msg, reason: str):
    """Route an unprocessable message to the dead letter queue."""
    dlq_producer.produce(
        DLQ_TOPIC,
        key=msg.key(),
        value=msg.value(),
        headers=[
            ("original-topic",    msg.topic().encode()),
            ("original-partition", str(msg.partition()).encode()),
            ("original-offset",   str(msg.offset()).encode()),
            ("failure-reason",    reason.encode()),
            ("failed-at",         __import__("datetime").datetime.utcnow().isoformat().encode()),
        ],
    )
    dlq_producer.poll(0)

empty_polls = 0
while empty_polls < 3:
    msg = consumer.poll(timeout=2.0)
    if msg is None:
        empty_polls += 1
        continue
    empty_polls = 0
    if msg.error():
        print(f"Consumer error: {msg.error()}")
        continue

    # --- Deserialization ---
    try:
        data = json.loads(msg.value().decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"Deserialization failed at offset {msg.offset()}: {e}")
        send_to_dlq(msg, f"deserialization_error: {e}")
        consumer.commit(msg)   # commit past the bad message — do not block
        continue

    # --- Validation ---
    if "order_id" not in data:
        print(f"Invalid message at offset {msg.offset()}: missing order_id")
        send_to_dlq(msg, "validation_error: missing order_id")
        consumer.commit(msg)
        continue

    # --- Processing ---
    print(f"Processed order: {data['order_id']}")
    consumer.commit(msg)

dlq_producer.flush()
consumer.close()
```

### Processing errors — transient vs permanent

Not every processing failure means the message is bad. A database being temporarily
unavailable is a transient error — the message is fine, your dependencies are not.

```python
import json
import time
import random
from confluent_kafka import Consumer, Producer

TOPIC = "orders"
DLQ_TOPIC = "orders.dlq"

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "order-processor",
    "auto.offset.reset": "earliest",
    "enable.auto.commit": False,
})
dlq_producer = Producer({"bootstrap.servers": "localhost:9092"})
consumer.subscribe([TOPIC])


class TransientError(Exception):
    """Temporary failure — retry is appropriate (DB timeout, HTTP 503, etc.)."""

class PermanentError(Exception):
    """Permanent failure — retrying will not help (invalid data, business logic failure)."""


def process_order(data: dict):
    """Simulate order processing with occasional failures."""
    order_id = data.get("order_id")

    # Simulate a transient dependency failure (20% chance)
    if random.random() < 0.2:
        raise TransientError(f"Database timeout while processing order {order_id}")

    # Simulate a permanent business logic failure (5% chance)
    if data.get("total", 0) < 0:
        raise PermanentError(f"Order {order_id} has negative total — invalid")

    print(f"Order {order_id} processed successfully.")


def process_with_retry(msg, max_transient_retries=3):
    """
    Try to process a message, distinguishing transient from permanent errors.
    Returns True on success, False if the message should go to DLQ.
    """
    try:
        data = json.loads(msg.value().decode("utf-8"))
    except Exception as e:
        print(f"Cannot deserialize: {e}")
        return False   # permanent — send to DLQ

    for attempt in range(1, max_transient_retries + 1):
        try:
            process_order(data)
            return True   # success

        except TransientError as e:
            if attempt < max_transient_retries:
                wait = 0.5 * attempt
                print(f"  Transient error (attempt {attempt}/{max_transient_retries}): {e} — retrying in {wait}s")
                time.sleep(wait)
            else:
                print(f"  Transient error — exhausted {max_transient_retries} retries: {e}")
                return False   # give up — send to DLQ

        except PermanentError as e:
            print(f"  Permanent error: {e} — sending to DLQ immediately")
            return False   # do not retry — send to DLQ

    return False


empty_polls = 0
while empty_polls < 3:
    msg = consumer.poll(timeout=2.0)
    if msg is None:
        empty_polls += 1
        continue
    empty_polls = 0
    if msg.error():
        continue

    success = process_with_retry(msg)

    if not success:
        dlq_producer.produce(
            DLQ_TOPIC,
            key=msg.key(),
            value=msg.value(),
            headers=[("original-topic", msg.topic().encode())],
        )
        dlq_producer.poll(0)
        print(f"  → Sent offset {msg.offset()} to DLQ")

    # Commit regardless — we have either processed it or routed it to DLQ
    consumer.commit(msg)

dlq_producer.flush()
consumer.close()
```

### Poison pill messages

A **poison pill** is a message that always causes a processing failure regardless of
how many times you retry it. Without a dead letter queue, a poison pill will block
your consumer forever on the same offset.

```
Normal flow:           [msg0] → process → commit → [msg1] → process → commit → ...

Poison pill scenario:  [msg0] → process → commit → [msg1] → FAIL → retry → FAIL
                                                              → retry → FAIL
                                                              → [consumer stuck here forever]
```

The only safe response to a poison pill is to route it to a dead letter queue (DLQ)
and commit past it so the consumer can continue with the next message.

---

## 9.3 Retry Patterns

### Pattern 1 — Simple in-process retry with backoff

The simplest pattern: catch the exception in your processing function and retry a
fixed number of times with a wait between attempts. No extra topics needed.

**Use when:** transient errors are rare and short-lived (< a few seconds), and
you can afford to block the consumer while retrying.

```python
import time
from confluent_kafka import Consumer

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "simple-retry-group",
    "auto.offset.reset": "earliest",
    "enable.auto.commit": False,
})
consumer.subscribe(["orders"])

def process(value: bytes) -> None:
    """Your actual processing logic — raises on failure."""
    raise ConnectionError("DB unavailable")   # simulate failure

def process_with_exponential_backoff(value: bytes, max_attempts: int = 4) -> bool:
    """
    Retry with exponential backoff.
    Returns True on success, False after max_attempts failures.

    Backoff schedule (max_attempts=4):
      attempt 1: immediate
      attempt 2: wait 1s
      attempt 3: wait 2s
      attempt 4: wait 4s
    """
    for attempt in range(1, max_attempts + 1):
        try:
            process(value)
            return True
        except Exception as e:
            if attempt == max_attempts:
                print(f"All {max_attempts} attempts failed: {e}")
                return False
            wait = 2 ** (attempt - 1)   # 1, 2, 4 seconds
            print(f"Attempt {attempt} failed: {e} — retrying in {wait}s")
            time.sleep(wait)
    return False

empty_polls = 0
while empty_polls < 3:
    msg = consumer.poll(timeout=2.0)
    if msg is None:
        empty_polls += 1
        continue
    empty_polls = 0
    if msg.error():
        continue

    if process_with_exponential_backoff(msg.value()):
        consumer.commit(msg)
    else:
        print(f"Sending offset {msg.offset()} to DLQ")
        consumer.commit(msg)   # commit past the failed message

consumer.close()
```

**Limitation:** While retrying, the consumer is blocking. It is not calling `poll()`,
so no heartbeats are sent to the broker. If retries take longer than
`max.poll.interval.ms`, the broker evicts this consumer and triggers a rebalance.
Increase `max.poll.interval.ms` if your retry budget exceeds 5 minutes.

### Pattern 2 — Retry topics

The retry topic pattern avoids blocking the consumer. Instead of retrying inline, the
consumer publishes the failed message to a dedicated retry topic and moves on. A
separate consumer reads the retry topic and re-processes messages after a delay.

```
orders          → consumer → processing fails → publish to orders.retry-1
                                              → commit → continue with next message

orders.retry-1  → retry consumer → fails again → publish to orders.retry-2
orders.retry-2  → retry consumer → fails again → publish to orders.dlq
orders.dlq      → dead letter consumer (human review / alerting)
```

**Advantages over inline retry:**
- The main consumer is never blocked — it keeps processing at full speed
- Retry delays can be much longer (minutes or hours) without blocking
- Failed messages are visible in Kafka UI for debugging
- Each retry hop is auditable — you can see exactly how many times a message was tried

**Use when:** processing errors may last minutes or hours (e.g. downstream service
outage), or when you need to retry without slowing the main pipeline.

```bash
# Create the retry topic chain (already done in prerequisites, repeated for clarity)
for topic in orders.retry-1 orders.retry-2 orders.dlq; do
  docker exec kafka /opt/kafka/bin/kafka-topics.sh --create \
    --topic $topic \
    --bootstrap-server localhost:9092 \
    --partitions 3 \
    --replication-factor 1
done
```

### Shared retry infrastructure

```python
# retry_utils.py — shared helpers used by all consumers in the retry chain
import json
import datetime
from confluent_kafka import Producer

RETRY_CHAIN = ["orders", "orders.retry-1", "orders.retry-2", "orders.dlq"]
MAX_RETRIES = len(RETRY_CHAIN) - 2   # number of retry topics (excludes DLQ)

def next_topic(current_topic: str) -> str:
    """Return the next topic in the retry chain, or the DLQ if at the end."""
    try:
        idx = RETRY_CHAIN.index(current_topic)
        return RETRY_CHAIN[idx + 1]
    except (ValueError, IndexError):
        return RETRY_CHAIN[-1]   # DLQ

def get_retry_count(msg) -> int:
    """Read the retry count from the message headers. Returns 0 if not present."""
    if msg.headers():
        for key, value in msg.headers():
            if key == "retry-count":
                return int(value.decode())
    return 0

def forward_for_retry(producer: Producer, msg, reason: str):
    """
    Forward a failed message to the next topic in the retry chain.
    Increments the retry-count header with each hop.
    """
    retry_count = get_retry_count(msg) + 1
    destination = next_topic(msg.topic())

    # Preserve original headers and add retry metadata
    headers = list(msg.headers() or [])
    headers = [h for h in headers if h[0] not in (
        "retry-count", "last-failure-reason", "last-failure-at"
    )]
    headers += [
        ("retry-count",       str(retry_count).encode()),
        ("last-failure-reason", reason.encode()),
        ("last-failure-at",   datetime.datetime.utcnow().isoformat().encode()),
        ("original-topic",    (msg.topic() if retry_count == 1 else
                               next(v for k, v in (msg.headers() or [])
                                    if k == "original-topic") ).encode()
                              if retry_count == 1 else
                              next((v for k, v in (msg.headers() or [])
                                    if k == "original-topic"), msg.topic().encode())),
    ]

    producer.produce(destination, key=msg.key(), value=msg.value(), headers=headers)
    producer.poll(0)

    label = "DLQ" if destination == RETRY_CHAIN[-1] else f"retry (attempt {retry_count})"
    print(f"  → Forwarded to {destination} [{label}]")
```

### Main consumer (orders topic)

```python
# main_consumer.py
import json
from confluent_kafka import Consumer, Producer
from retry_utils import forward_for_retry

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "orders-processor",
    "auto.offset.reset": "earliest",
    "enable.auto.commit": False,
})
retry_producer = Producer({"bootstrap.servers": "localhost:9092"})
consumer.subscribe(["orders"])

def process_order(data: dict):
    raise ValueError("Simulated processing failure")   # replace with real logic

empty_polls = 0
while empty_polls < 3:
    msg = consumer.poll(timeout=2.0)
    if msg is None:
        empty_polls += 1
        continue
    empty_polls = 0
    if msg.error():
        continue

    try:
        data = json.loads(msg.value().decode())
        process_order(data)
        print(f"Processed: {data.get('order_id')}")
    except Exception as e:
        forward_for_retry(retry_producer, msg, str(e))

    consumer.commit(msg)   # always commit — message is either processed or forwarded

retry_producer.flush()
consumer.close()
```

### Retry consumer (orders.retry-1 and orders.retry-2)

```python
# retry_consumer.py — handles both orders.retry-1 and orders.retry-2
import sys
import json
import time
from confluent_kafka import Consumer, Producer
from retry_utils import forward_for_retry, get_retry_count

RETRY_TOPIC = sys.argv[1] if len(sys.argv) > 1 else "orders.retry-1"

# Delay before processing a retry — gives the downstream system time to recover
RETRY_DELAYS = {
    "orders.retry-1": 30,    # wait 30 seconds before first retry
    "orders.retry-2": 120,   # wait 2 minutes before second retry
}
DELAY_SECONDS = RETRY_DELAYS.get(RETRY_TOPIC, 60)

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": f"{RETRY_TOPIC}-processor",
    "auto.offset.reset": "earliest",
    "enable.auto.commit": False,
    # Increase poll interval to accommodate the retry delay
    "max.poll.interval.ms": max(300000, DELAY_SECONDS * 1000 + 30000),
})
retry_producer = Producer({"bootstrap.servers": "localhost:9092"})
consumer.subscribe([RETRY_TOPIC])

def process_order(data: dict):
    raise ValueError("Still failing")   # replace with real logic

empty_polls = 0
while empty_polls < 3:
    msg = consumer.poll(timeout=2.0)
    if msg is None:
        empty_polls += 1
        continue
    empty_polls = 0
    if msg.error():
        continue

    retry_count = get_retry_count(msg)
    print(f"Retry attempt {retry_count} for offset {msg.offset()} — waiting {DELAY_SECONDS}s")
    time.sleep(DELAY_SECONDS)

    try:
        data = json.loads(msg.value().decode())
        process_order(data)
        print(f"Retry succeeded for order: {data.get('order_id')}")
    except Exception as e:
        forward_for_retry(retry_producer, msg, str(e))

    consumer.commit(msg)

retry_producer.flush()
consumer.close()
```

### DLQ consumer — review and alerting

```python
# dlq_consumer.py — reads the dead letter queue for monitoring and alerting
import json
from confluent_kafka import Consumer

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "dlq-monitor",
    "auto.offset.reset": "earliest",
    "enable.auto.commit": True,
})
consumer.subscribe(["orders.dlq"])

print("Monitoring DLQ — Ctrl+C to stop\n")

empty_polls = 0
while empty_polls < 5:
    msg = consumer.poll(timeout=2.0)
    if msg is None:
        empty_polls += 1
        continue
    empty_polls = 0
    if msg.error():
        continue

    # Parse headers for context
    headers = dict(msg.headers() or [])
    retry_count     = headers.get("retry-count",         b"?").decode()
    failure_reason  = headers.get("last-failure-reason", b"unknown").decode()
    failed_at       = headers.get("last-failure-at",     b"unknown").decode()
    original_topic  = headers.get("original-topic",      b"unknown").decode()

    print("=" * 60)
    print(f"DLQ message received")
    print(f"  Original topic : {original_topic}")
    print(f"  Retry attempts : {retry_count}")
    print(f"  Last failure   : {failure_reason}")
    print(f"  Failed at      : {failed_at}")
    try:
        payload = json.loads(msg.value().decode())
        print(f"  Payload        : {json.dumps(payload, indent=4)}")
    except Exception:
        print(f"  Raw value      : {msg.value()}")
    print()

    # In production: send an alert (PagerDuty, Slack, email), write to a
    # monitoring database, or trigger a human review workflow

consumer.close()
```

### Pattern 3 — Circuit breaker

The circuit breaker pattern prevents a consumer from hammering a failing downstream
service. When too many consecutive failures occur, the circuit "opens" and the
consumer stops trying for a cooldown period before attempting again.

```
CLOSED (normal) → failure threshold exceeded → OPEN (paused)
    ↑                                              |
    |              cooldown expires                |
    +──────── HALF-OPEN (test one request) ←───── +
                   |            |
               success        failure
                   |            |
               CLOSED         OPEN (reset cooldown)
```

```python
import time
from enum import Enum

class CircuitState(Enum):
    CLOSED    = "closed"      # normal — requests pass through
    OPEN      = "open"        # tripped — requests blocked
    HALF_OPEN = "half_open"   # testing — one request allowed through

class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 5,      # open after this many consecutive failures
        cooldown_seconds: float = 60.0,  # stay open for this long
        success_threshold: int = 2,      # close after this many consecutive successes in half-open
    ):
        self.failure_threshold  = failure_threshold
        self.cooldown_seconds   = cooldown_seconds
        self.success_threshold  = success_threshold

        self.state             = CircuitState.CLOSED
        self.consecutive_failures  = 0
        self.consecutive_successes = 0
        self.opened_at         = None

    def call(self, fn, *args, **kwargs):
        """
        Execute fn(*args, **kwargs) through the circuit breaker.
        Raises CircuitOpenError if the circuit is open.
        """
        if self.state == CircuitState.OPEN:
            elapsed = time.time() - self.opened_at
            if elapsed >= self.cooldown_seconds:
                print(f"Circuit → HALF-OPEN (cooldown elapsed)")
                self.state = CircuitState.HALF_OPEN
                self.consecutive_successes = 0
            else:
                remaining = self.cooldown_seconds - elapsed
                raise CircuitOpenError(f"Circuit is OPEN — {remaining:.0f}s remaining in cooldown")

        try:
            result = fn(*args, **kwargs)
            self._on_success()
            return result

        except Exception as e:
            self._on_failure()
            raise

    def _on_success(self):
        self.consecutive_failures = 0
        if self.state == CircuitState.HALF_OPEN:
            self.consecutive_successes += 1
            if self.consecutive_successes >= self.success_threshold:
                print("Circuit → CLOSED (recovered)")
                self.state = CircuitState.CLOSED
                self.consecutive_successes = 0

    def _on_failure(self):
        self.consecutive_successes = 0
        self.consecutive_failures += 1
        if self.state in (CircuitState.CLOSED, CircuitState.HALF_OPEN):
            if self.consecutive_failures >= self.failure_threshold:
                print(f"Circuit → OPEN after {self.consecutive_failures} consecutive failures")
                self.state     = CircuitState.OPEN
                self.opened_at = time.time()


class CircuitOpenError(Exception):
    pass


# --- Usage with a Kafka consumer ---
from confluent_kafka import Consumer, Producer
from retry_utils import forward_for_retry

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "circuit-breaker-group",
    "auto.offset.reset": "earliest",
    "enable.auto.commit": False,
})
retry_producer = Producer({"bootstrap.servers": "localhost:9092"})
consumer.subscribe(["orders"])

# One circuit breaker guards the downstream database
db_circuit = CircuitBreaker(failure_threshold=5, cooldown_seconds=30)

def write_to_database(data: dict):
    """Simulated DB write that may fail."""
    import random
    if random.random() < 0.6:
        raise ConnectionError("DB connection refused")
    print(f"DB write OK: {data.get('order_id')}")

empty_polls = 0
while empty_polls < 3:
    msg = consumer.poll(timeout=2.0)
    if msg is None:
        empty_polls += 1
        continue
    empty_polls = 0
    if msg.error():
        continue

    try:
        import json
        data = json.loads(msg.value().decode())
        db_circuit.call(write_to_database, data)
        consumer.commit(msg)

    except CircuitOpenError as e:
        # Circuit is open — do not retry now, do not commit, pause and wait
        print(f"Circuit open: {e}")
        time.sleep(5)   # back off before polling again
        # Do NOT commit — the message will be re-read when the circuit closes

    except Exception as e:
        # Processing failed (circuit was closed) — forward to retry topic
        forward_for_retry(retry_producer, msg, str(e))
        consumer.commit(msg)

retry_producer.flush()
consumer.close()
```

---

## 9.4 Idempotent Processing

### Why idempotency matters with at-least-once delivery

Kafka's at-least-once guarantee means a message may be delivered more than once:
- Consumer crashes after processing but before committing the offset
- On restart, the consumer re-reads and re-processes the same message
- A rebalance can cause a message to be processed by two consumers momentarily

If your processing is not idempotent, duplicate delivery causes real harm:
- Charging a customer twice for one order
- Sending a confirmation email twice
- Incrementing a counter by 2 when it should increment by 1

An **idempotent operation** produces the same result no matter how many times it is
applied. Your goal is to design your consumers so duplicate delivery has no effect.

### Strategy 1 — Natural idempotency

Some operations are idempotent by nature — use these when possible:

```python
# Idempotent by nature — applying it twice has no extra effect
# SET is idempotent; INCREMENT is not
def update_user_status(user_id: int, status: str):
    db.execute(
        "UPDATE users SET status = %s WHERE user_id = %s",
        (status, user_id)
    )
    # Running this twice sets status to the same value both times ✓

# Upsert (INSERT ... ON CONFLICT DO UPDATE) — idempotent
def upsert_order(order_id: str, data: dict):
    db.execute("""
        INSERT INTO orders (order_id, status, total)
        VALUES (%s, %s, %s)
        ON CONFLICT (order_id) DO UPDATE
          SET status = EXCLUDED.status,
              total  = EXCLUDED.total
    """, (order_id, data["status"], data["total"]))
    # Re-inserting the same order_id just overwrites with the same data ✓
```

### Strategy 2 — Deduplication with a message ID

Add a unique ID to every message. On the consumer side, check if the ID has been
processed before, and skip if so.

```python
# Producer — always include a unique message ID in headers
import uuid
from confluent_kafka import Producer

producer = Producer({"bootstrap.servers": "localhost:9092"})

def produce_with_id(topic, value):
    message_id = str(uuid.uuid4())
    producer.produce(
        topic,
        value=value,
        headers=[("message-id", message_id.encode())],
    )
    producer.poll(0)
    return message_id

produce_with_id("orders", b'{"order_id": "ORD-001", "total": 49.99}')
producer.flush()
```

```python
# Consumer — check the message ID before processing
from confluent_kafka import Consumer

# In production, use Redis or a database for the seen IDs store
# so it survives consumer restarts. A set only works within one process run.
seen_message_ids = set()

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "idempotent-consumer",
    "auto.offset.reset": "earliest",
    "enable.auto.commit": False,
})
consumer.subscribe(["orders"])

def get_message_id(msg) -> str | None:
    """Extract the message-id header value."""
    if msg.headers():
        for key, value in msg.headers():
            if key == "message-id":
                return value.decode()
    return None

empty_polls = 0
while empty_polls < 3:
    msg = consumer.poll(timeout=2.0)
    if msg is None:
        empty_polls += 1
        continue
    empty_polls = 0
    if msg.error():
        continue

    message_id = get_message_id(msg)

    if message_id and message_id in seen_message_ids:
        print(f"Duplicate detected — skipping message_id={message_id}")
        consumer.commit(msg)
        continue

    # Process the message
    print(f"Processing message_id={message_id} offset={msg.offset()}")
    # ... your processing logic ...

    # Mark as seen AFTER successful processing
    if message_id:
        seen_message_ids.add(message_id)

    consumer.commit(msg)

consumer.close()
```

### Strategy 3 — Conditional writes (optimistic locking)

Use the current state of the data to decide whether to apply an update. If the data
has already been updated by a previous delivery, the condition fails and you skip it:

```python
def process_payment(payment_id: str, amount: float):
    """
    Mark a payment as completed — idempotent via status check.
    If already completed, the UPDATE matches 0 rows and has no effect.
    """
    rows_updated = db.execute("""
        UPDATE payments
           SET status = 'completed', completed_at = NOW()
         WHERE payment_id = %s
           AND status = 'pending'   -- only update if still pending
    """, (payment_id,))

    if rows_updated == 0:
        print(f"Payment {payment_id} was already processed — skipping")
    else:
        print(f"Payment {payment_id} marked as completed")
```

### Idempotency checklist

Before deploying a consumer to production, verify each of these:

```
□ Does my consumer handle duplicate delivery without side effects?
  → Use upserts, SET operations, or deduplication IDs

□ Do I commit the offset only AFTER processing completes successfully?
  → If processing fails before commit, the message is re-delivered — handle it

□ If my consumer uses an external service (email, payment gateway), does that
  service also support idempotent requests?
  → Many APIs support an Idempotency-Key header for exactly this purpose

□ Is my deduplication store (Redis, DB) durable across consumer restarts?
  → An in-memory set is lost on restart — use a persistent store in production

□ Does my consumer handle the case where the same message arrives with
  different content (schema drift or producer bug)?
  → Validate all required fields before processing, send invalid messages to DLQ
```

---

## Summary — Quick Reference

| Scenario | Correct response |
|---|---|
| **Retriable producer error** | Handled automatically by the client — configure `retries` and `delivery.timeout.ms` |
| **Permanent producer error** | Log in delivery callback, save for dead-lettering or alerting |
| **`BufferError`** | Catch in `produce()`, call `poll()` to drain buffer, back off with exponential wait |
| **Deserialization failure** | Send to DLQ, commit past the bad message — never block on it |
| **Transient processing error** | Retry inline (simple) or via retry topics (non-blocking) |
| **Permanent processing error** | Send to DLQ immediately — do not retry |
| **Poison pill** | Send to DLQ, commit — letting it block is never the right answer |
| **Dependency outage** | Use circuit breaker — fail fast, pause, recover gracefully |
| **Duplicate delivery** | Design for idempotency — upserts, deduplication IDs, conditional writes |
| **DLQ message** | Alert, log with context headers, review manually or replay after fixing root cause |
| **Retry topic delay** | Increase `max.poll.interval.ms` beyond your retry wait time to avoid rebalance |
