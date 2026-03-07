# Module 4 — Producers

A **producer** is any application that publishes messages to Kafka topics. This
module walks through every aspect of the `confluent-kafka` Python producer, from the
simplest possible example to production-grade patterns. Every concept includes a
runnable code example.

**Prerequisites:**
- Kafka is running locally via Docker (see Module 2)
- `confluent-kafka` is installed: `pip install confluent-kafka`
- The examples below assume `bootstrap.servers = "localhost:9092"` (single-broker).
  For the multi-broker setup use `"localhost:9092,localhost:9095,localhost:9096"`.

---

## 4.1 Basic Producer

### How the producer works internally

Before writing any code it helps to understand what happens when you call
`producer.produce()`:

```
Your code                 Producer (local process)            Kafka Broker
---------                 ------------------------            ------------
produce("msg")  -->  [internal buffer / send queue]
                              |
                     batches messages together
                     (waits for linger.ms or batch.size)
                              |
                              +------- network send -------->  broker stores
                                                               message on disk
                                                                    |
                     delivery callback  <-------- broker ack -------+
```

Key points:
- `produce()` is **non-blocking**. It puts the message into a local in-memory buffer
  and returns immediately. The message has not reached the broker yet.
- The background thread inside the producer sends the buffer to the broker in batches.
- `flush()` blocks your code until every buffered message has been acknowledged by the
  broker. Always call it before your program exits.

### Minimal example

```python
from confluent_kafka import Producer

# Create the producer with the minimum required configuration.
producer = Producer({"bootstrap.servers": "localhost:9092"})

# produce() adds the message to the internal buffer.
# topic   — the topic to write to (it must already exist, or auto-creation must be enabled)
# value   — the message payload; must be bytes, not a string
producer.produce("my-first-topic", value=b"Hello, Kafka!")

# flush() waits until the broker has acknowledged the message.
# Without flush(), the message may never be sent if the program exits first.
producer.flush()

print("Message delivered.")
```

### Creating the topic first

If the topic does not exist, create it before running the producer:

```bash
docker exec kafka /opt/kafka/bin/kafka-topics.sh --create \
  --topic my-first-topic \
  --bootstrap-server localhost:9092 \
  --partitions 3 \
  --replication-factor 1
```

### Encoding strings to bytes

Kafka messages are raw bytes. Python strings must be encoded before sending:

```python
# Correct — encode the string to bytes
producer.produce("my-first-topic", value="Hello".encode("utf-8"))

# Also correct — bytes literal
producer.produce("my-first-topic", value=b"Hello")

# Wrong — will raise a TypeError
producer.produce("my-first-topic", value="Hello")
```

### Sending multiple messages and flushing

```python
from confluent_kafka import Producer

producer = Producer({"bootstrap.servers": "localhost:9092"})

messages = ["first", "second", "third", "fourth", "fifth"]

for msg in messages:
    producer.produce("my-first-topic", value=msg.encode("utf-8"))
    # poll(0) gives the producer a chance to call delivery callbacks
    # and handle internal events without blocking. A good habit in loops.
    producer.poll(0)

# flush() with a timeout: block for up to 10 seconds.
# Returns the number of messages still in the buffer (0 means all delivered).
remaining = producer.flush(timeout=10)
if remaining > 0:
    print(f"Warning: {remaining} messages were not delivered")
else:
    print("All messages delivered.")
```

---

## 4.2 Producer Configuration

The producer accepts a dictionary of configuration options. Below are the most
important ones explained with examples.

### bootstrap.servers

```python
# Single broker (development)
producer = Producer({"bootstrap.servers": "localhost:9092"})

# Multiple brokers (production / multi-broker Docker setup)
# List all brokers for resilience. If the first one is down,
# the client tries the next. Only one needs to be reachable at startup.
producer = Producer({"bootstrap.servers": "localhost:9092,localhost:9095,localhost:9096"})
```

### acks — durability guarantee

`acks` controls how many broker acknowledgements the producer waits for before
considering a message delivered. This is the single most important trade-off between
durability and latency.

```python
# acks=0 — Fire and forget. The producer does not wait for any acknowledgement.
#          Fastest. Messages can be lost if the broker crashes before writing.
producer = Producer({
    "bootstrap.servers": "localhost:9092",
    "acks": 0,
})

# acks=1 — Wait for the partition leader to write the message to its local log.
#          Default. Good balance of speed and safety. Message can still be lost
#          if the leader crashes before followers replicate it.
producer = Producer({
    "bootstrap.servers": "localhost:9092",
    "acks": 1,
})

# acks=all (or acks=-1) — Wait for ALL in-sync replicas to acknowledge.
#          Strongest durability. No data loss as long as at least one
#          in-sync replica survives. Use this for critical data.
producer = Producer({
    "bootstrap.servers": "localhost:9092",
    "acks": "all",
})
```

### linger.ms and batch.size — throughput tuning

The producer groups messages into batches before sending. These two settings control
how batching works:

```python
producer = Producer({
    "bootstrap.servers": "localhost:9092",

    # linger.ms — how long (milliseconds) to wait for more messages before
    # sending a batch. Default is 0 (send immediately).
    # Increasing this allows larger batches at the cost of slightly higher latency.
    # 5-10ms is a common production value.
    "linger.ms": 5,

    # batch.size — maximum size of a batch in bytes. Default is 16384 (16 KB).
    # The producer sends a batch when either linger.ms expires OR batch.size
    # is reached, whichever comes first.
    "batch.size": 65536,  # 64 KB
})
```

To see the effect, produce a burst of messages and observe fewer network round-trips:

```python
from confluent_kafka import Producer
import time

producer = Producer({
    "bootstrap.servers": "localhost:9092",
    "linger.ms": 10,
    "batch.size": 65536,
})

start = time.time()
for i in range(1000):
    producer.produce("my-first-topic", value=f"msg-{i}".encode())
producer.flush()
elapsed = time.time() - start
print(f"Produced 1000 messages in {elapsed:.3f}s")
```

### compression.type

Compression reduces network bandwidth and disk usage at the cost of CPU:

```python
producer = Producer({
    "bootstrap.servers": "localhost:9092",
    # Options: "none" (default), "gzip", "snappy", "lz4", "zstd"
    # lz4  — best speed, moderate compression ratio
    # zstd — best compression ratio, slightly more CPU
    # snappy — good balance, common in older Hadoop ecosystems
    "compression.type": "lz4",
})
```

### enable.idempotence — prevent duplicate messages

Without idempotence, a network timeout can cause the producer to retry and deliver the
same message twice. Enabling idempotence prevents this:

```python
producer = Producer({
    "bootstrap.servers": "localhost:9092",
    # enable.idempotence=True automatically sets:
    #   acks=all
    #   retries=INT_MAX
    #   max.in.flight.requests.per.connection=5
    # This guarantees exactly-once delivery to the broker (not end-to-end).
    "enable.idempotence": True,
})
```

### max.in.flight.requests.per.connection — ordering vs throughput

```python
producer = Producer({
    "bootstrap.servers": "localhost:9092",

    # How many unacknowledged requests can be in-flight at the same time.
    # Default is 5.
    #
    # If retries > 0 and this is > 1, messages CAN arrive out of order:
    #   batch 1 sent → fails → broker receives batch 2 first → batch 1 retried
    #
    # To guarantee strict ordering per partition:
    #   set to 1 (slower — only one request at a time)
    # OR
    #   enable idempotence (handles ordering automatically, supports up to 5)
    "max.in.flight.requests.per.connection": 1,
    "retries": 3,
})
```

### Full production-grade configuration

```python
from confluent_kafka import Producer

producer = Producer({
    "bootstrap.servers": "localhost:9092,localhost:9095,localhost:9096",
    "acks": "all",                                  # strongest durability
    "enable.idempotence": True,                     # no duplicates on retry
    "compression.type": "lz4",                      # fast compression
    "linger.ms": 5,                                 # batch for 5ms
    "batch.size": 65536,                            # 64KB max batch
    "retries": 5,                                   # retry transient failures
    "delivery.timeout.ms": 30000,                   # give up after 30s total
    "request.timeout.ms": 10000,                    # per-request timeout
})
```

---

## 4.3 Callbacks and Delivery Reports

By default, `produce()` is fire-and-forget from your code's perspective — you do not
know if the message actually reached the broker unless you add a callback.

A **delivery callback** is a function that Kafka calls after each message is either
successfully delivered or permanently failed. It is the correct way to track delivery.

### Basic delivery callback

```python
from confluent_kafka import Producer

def on_delivery(err, msg):
    """
    Called by the producer for every message once it is either delivered
    or permanently failed.

    Parameters:
        err  — None on success, a KafkaError object on failure
        msg  — the Message object (contains topic, partition, offset, key, value)
    """
    if err:
        print(f"Delivery FAILED: topic={msg.topic()} error={err}")
    else:
        print(
            f"Delivered: value={msg.value().decode('utf-8')}"
            f"  partition={msg.partition()}"
            f"  offset={msg.offset()}"
        )

producer = Producer({"bootstrap.servers": "localhost:9092"})

for i in range(5):
    producer.produce(
        "my-first-topic",
        value=f"Message {i}".encode("utf-8"),
        on_delivery=on_delivery,  # attach the callback here
    )
    # poll(0) triggers any pending callbacks without blocking.
    # The callback is NOT called inside produce() — it is called when you
    # call poll() or flush().
    producer.poll(0)

producer.flush()
```

Expected output:
```
Delivered: value=Message 0  partition=1  offset=5
Delivered: value=Message 1  partition=0  offset=3
Delivered: value=Message 2  partition=2  offset=2
Delivered: value=Message 3  partition=1  offset=6
Delivered: value=Message 4  partition=0  offset=4
```

> **Important:** The callback is not called inside `produce()`. It is triggered when
> you call `producer.poll()` or `producer.flush()`. If you never call either, you
> never receive callbacks.

### Tracking delivery with a counter

A common pattern is to count delivered vs failed messages:

```python
from confluent_kafka import Producer

delivered = 0
failed = 0

def on_delivery(err, msg):
    global delivered, failed
    if err:
        failed += 1
        print(f"FAILED: {err}")
    else:
        delivered += 1

producer = Producer({"bootstrap.servers": "localhost:9092"})

for i in range(100):
    producer.produce("my-first-topic", value=f"msg-{i}".encode(), on_delivery=on_delivery)
    producer.poll(0)

producer.flush()
print(f"Delivered: {delivered}  Failed: {failed}")
```

### Handling delivery errors

```python
from confluent_kafka import Producer, KafkaError

def on_delivery(err, msg):
    if err is None:
        return  # success — nothing to do

    # Retriable errors are handled automatically by the producer (with retries).
    # By the time the callback is called with an error, all retries have been
    # exhausted — this is a permanent failure.
    if err.code() == KafkaError.MSG_TIMED_OUT:
        print(f"Message timed out after all retries: {msg.value()}")
    elif err.code() == KafkaError._MSG_TIMED_OUT:
        print(f"Local queue timeout (buffer full): {msg.value()}")
    else:
        print(f"Unhandled delivery error [{err.code()}]: {err}")
        # In production: send to a dead letter topic or alert on-call

producer = Producer({
    "bootstrap.servers": "localhost:9092",
    "retries": 3,
    "delivery.timeout.ms": 10000,
})

producer.produce("my-first-topic", value=b"important message", on_delivery=on_delivery)
producer.flush()
```

---

## 4.4 Message Keys

Every Kafka message can have an optional **key** alongside its value. Keys have two
important roles:

1. **Partition routing** — messages with the same key always go to the same partition.
2. **Ordering guarantee** — within a partition, messages are ordered. So all messages
   with the same key are ordered relative to each other.

### How Kafka uses the key to pick a partition

```
key "user-123"
      |
      v
murmur2_hash("user-123") % num_partitions
      |
      v
always lands on partition 1 (for example)
```

The same key → the same hash → the same partition. This is deterministic.

### Sending messages with a key

```python
from confluent_kafka import Producer

producer = Producer({"bootstrap.servers": "localhost:9092"})

# Without a key — messages are distributed round-robin across all partitions.
# No ordering guarantee between messages.
producer.produce("my-first-topic", value=b"no key here")

# With a key — all messages with key "user-123" always go to the same partition,
# preserving their order relative to each other.
producer.produce(
    "my-first-topic",
    key="user-123".encode("utf-8"),   # key must also be bytes
    value=b"user signed up",
)
producer.produce(
    "my-first-topic",
    key="user-123".encode("utf-8"),
    value=b"user updated profile",
)
producer.produce(
    "my-first-topic",
    key="user-123".encode("utf-8"),
    value=b"user deleted account",
)

producer.flush()
# The three "user-123" messages above are guaranteed to be in the partition
# in that exact order: signed up → updated profile → deleted account.
```

### Verifying key-based routing

Run this script and observe that all messages for the same key land on the same
partition:

```python
from confluent_kafka import Producer

producer = Producer({"bootstrap.servers": "localhost:9092"})

users = ["user-1", "user-2", "user-3", "user-1", "user-2", "user-1"]

def on_delivery(err, msg):
    if not err:
        print(
            f"key={msg.key().decode():<10}"
            f"  partition={msg.partition()}"
            f"  offset={msg.offset()}"
        )

for user in users:
    producer.produce(
        "my-first-topic",
        key=user.encode("utf-8"),
        value=f"event from {user}".encode("utf-8"),
        on_delivery=on_delivery,
    )
    producer.poll(0)

producer.flush()
```

Expected output (partition numbers will vary, but the same key always maps to the
same partition):
```
key=user-1      partition=2  offset=0
key=user-2      partition=0  offset=0
key=user-3      partition=1  offset=0
key=user-1      partition=2  offset=1
key=user-2      partition=0  offset=1
key=user-1      partition=2  offset=2
```

### Choosing good keys

| Use case | Good key |
|---|---|
| User activity events | `user_id` |
| Order processing | `order_id` |
| IoT sensor readings | `device_id` |
| Per-country events | `country_code` |
| No ordering needed | No key (round-robin) |

> **Avoid hot partitions:** If one key accounts for the vast majority of messages
> (e.g. a single very active user), all that traffic lands on one partition and one
> broker, creating a bottleneck. Design your keys so traffic is spread relatively
> evenly across partitions.

---

## 4.5 Message Headers

Headers are key-value pairs attached to a message as metadata. They do not modify the
payload and are useful for passing context between services without coupling them to
the message format.

Common uses:
- **Trace/correlation IDs** — for distributed tracing across services
- **Content type** — `content-type: application/json`
- **Source service** — which service produced the message
- **Schema version** — the version of the message format

```python
from confluent_kafka import Producer
import uuid

producer = Producer({"bootstrap.servers": "localhost:9092"})

# Headers are passed as a list of (key, value) tuples.
# Both key and value must be strings or bytes.
trace_id = str(uuid.uuid4())

producer.produce(
    "my-first-topic",
    value='{"user_id": 123, "action": "login"}'.encode("utf-8"),
    headers=[
        ("content-type", "application/json"),
        ("source-service", "auth-service"),
        ("trace-id", trace_id),
        ("schema-version", "2"),
    ],
)

producer.flush()
print(f"Produced message with trace-id: {trace_id}")
```

### Reading headers on the consumer side

```python
from confluent_kafka import Consumer

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "headers-demo",
    "auto.offset.reset": "earliest",
})
consumer.subscribe(["my-first-topic"])

empty_polls = 0
while empty_polls < 3:
    msg = consumer.poll(timeout=2.0)
    if msg is None:
        empty_polls += 1
        continue
    empty_polls = 0

    if not msg.error():
        print(f"Value: {msg.value().decode('utf-8')}")

        # msg.headers() returns a list of (key, value) tuples, or None
        if msg.headers():
            print("Headers:")
            for key, value in msg.headers():
                # Header values are bytes — decode them for display
                print(f"  {key}: {value.decode('utf-8') if value else None}")

consumer.close()
```

---

## 4.6 Partitioner Strategies

The **partitioner** decides which partition a message goes to. Understanding this is
important for controlling data distribution and ordering.

### Default behaviour (murmur2 hash)

- **With a key:** `partition = murmur2_hash(key) % num_partitions`. Deterministic —
  same key always maps to same partition.
- **Without a key:** uses the **sticky partitioner** — batches all keyless messages
  to one partition, then switches to another when the batch is sent. Better throughput
  than pure round-robin.

### Demonstrating key-based vs keyless distribution

```python
from confluent_kafka import Producer
from collections import defaultdict

producer = Producer({"bootstrap.servers": "localhost:9092"})

partition_counts = defaultdict(int)

def on_delivery(err, msg):
    if not err:
        partition_counts[msg.partition()] += 1

# Send 30 messages WITHOUT a key
for i in range(30):
    producer.produce("my-first-topic", value=f"no-key-{i}".encode(), on_delivery=on_delivery)
    producer.poll(0)

producer.flush()
print("Distribution without keys:", dict(partition_counts))

partition_counts.clear()

# Send 30 messages WITH keys
for i in range(30):
    key = f"user-{i % 5}"  # only 5 unique keys
    producer.produce("my-first-topic", key=key.encode(), value=f"msg-{i}".encode(), on_delivery=on_delivery)
    producer.poll(0)

producer.flush()
print("Distribution with keys:", dict(partition_counts))
```

### Custom partitioner

If you need full control over which partition a message goes to, you can write a
custom partitioner function:

```python
from confluent_kafka import Producer

def my_partitioner(key_bytes, all_partitions, available_partitions):
    """
    Called for every message to determine its partition.

    Parameters:
        key_bytes         — the message key as bytes (or None)
        all_partitions    — list of all partition IDs for this topic
        available_partitions — partitions with available leaders (subset of all)

    Returns:
        An integer partition ID from all_partitions
    """
    if key_bytes is None:
        # No key — use partition 0
        return all_partitions[0]

    # Route all "priority" messages to partition 0
    if key_bytes.startswith(b"priority-"):
        return all_partitions[0]

    # Everything else: simple modulo on the key length
    return all_partitions[len(key_bytes) % len(all_partitions)]

producer = Producer({
    "bootstrap.servers": "localhost:9092",
    "partitioner": my_partitioner,
})

producer.produce("my-first-topic", key=b"priority-order-99", value=b"urgent")
producer.produce("my-first-topic", key=b"normal-order-1", value=b"not urgent")
producer.flush()
```

---

## 4.7 Timestamps

Every Kafka message has a timestamp. There are two types:

| Type | Description |
|---|---|
| `CreateTime` | Set by the producer when the message is created. Default. |
| `LogAppendTime` | Set by the broker when the message is written to the log. Overrides the producer timestamp if configured on the topic. |

### Setting a custom timestamp

```python
from confluent_kafka import Producer
import time

producer = Producer({"bootstrap.servers": "localhost:9092"})

# timestamp is in milliseconds since Unix epoch
now_ms = int(time.time() * 1000)

producer.produce(
    "my-first-topic",
    value=b"message with custom timestamp",
    timestamp=now_ms,
)

# Backdating a message (e.g. replaying historical data)
from datetime import datetime, timezone
historical_dt = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
historical_ms = int(historical_dt.timestamp() * 1000)

producer.produce(
    "my-first-topic",
    value=b"historical event",
    timestamp=historical_ms,
)

producer.flush()
```

### Reading the timestamp on the consumer side

```python
from confluent_kafka import Consumer, TIMESTAMP_CREATE_TIME, TIMESTAMP_LOG_APPEND_TIME
import datetime

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "timestamp-demo",
    "auto.offset.reset": "earliest",
})
consumer.subscribe(["my-first-topic"])

empty_polls = 0
while empty_polls < 3:
    msg = consumer.poll(timeout=2.0)
    if msg is None:
        empty_polls += 1
        continue
    empty_polls = 0

    if not msg.error():
        ts_type, ts_ms = msg.timestamp()

        if ts_type == TIMESTAMP_CREATE_TIME:
            ts_label = "CreateTime"
        elif ts_type == TIMESTAMP_LOG_APPEND_TIME:
            ts_label = "LogAppendTime"
        else:
            ts_label = "NotAvailable"

        ts_human = datetime.datetime.fromtimestamp(ts_ms / 1000).isoformat()
        print(f"[{ts_label}] {ts_human}  value={msg.value().decode()}")

consumer.close()
```

---

## 4.8 Producer Patterns

### Pattern 1 — Fire and forget

Send a message and do not check whether it was delivered. The fastest approach, but
you will not know about failures.

Use when: logging, metrics, non-critical analytics events where occasional loss is
acceptable.

```python
from confluent_kafka import Producer

producer = Producer({
    "bootstrap.servers": "localhost:9092",
    "acks": 0,  # do not wait for any acknowledgement
})

for i in range(1000):
    producer.produce("analytics-events", value=f"event-{i}".encode())
    producer.poll(0)

# flush() still called to avoid losing messages in the buffer on exit
producer.flush()
```

### Pattern 2 — Synchronous send (blocking until ack)

Send one message and block until it is acknowledged before sending the next. The
safest approach per message, but the slowest — each message is a full round trip.

Use when: you need to know exactly which message failed before proceeding.

```python
from confluent_kafka import Producer, KafkaException

producer = Producer({
    "bootstrap.servers": "localhost:9092",
    "acks": "all",
})

def send_sync(producer, topic, value):
    """
    Send one message and block until the broker acknowledges it.
    Raises an exception if delivery fails.
    """
    result = {"error": None}

    def on_delivery(err, msg):
        result["error"] = err

    producer.produce(topic, value=value, on_delivery=on_delivery)
    producer.flush()  # blocks until this message (and all prior) are delivered

    if result["error"]:
        raise KafkaException(result["error"])

# Each call blocks until the broker confirms delivery
send_sync(producer, "orders", b'{"order_id": 1, "status": "created"}')
print("Order 1 confirmed")

send_sync(producer, "orders", b'{"order_id": 2, "status": "created"}')
print("Order 2 confirmed")
```

### Pattern 3 — Asynchronous send with callback

Send messages as fast as possible and handle results asynchronously via callbacks.
The best balance of throughput and correctness for most use cases.

Use when: you want high throughput but still need to know about failures.

```python
from confluent_kafka import Producer

failed_messages = []

def on_delivery(err, msg):
    if err:
        print(f"FAILED: {msg.value()} — {err}")
        failed_messages.append(msg.value())
    else:
        print(f"OK: partition={msg.partition()} offset={msg.offset()}")

producer = Producer({
    "bootstrap.servers": "localhost:9092",
    "acks": "all",
    "enable.idempotence": True,
    "linger.ms": 5,
})

# Produce all messages without waiting
for i in range(20):
    producer.produce(
        "my-first-topic",
        value=f"async-message-{i}".encode(),
        on_delivery=on_delivery,
    )
    producer.poll(0)  # trigger callbacks for already-completed messages

# Wait for all remaining messages and trigger remaining callbacks
producer.flush()

if failed_messages:
    print(f"\n{len(failed_messages)} messages failed — consider retry or dead letter queue")
else:
    print("\nAll messages delivered successfully")
```

### Pattern 4 — Buffered/batched sending for throughput

Send a large volume of messages with settings tuned for maximum throughput. Accepts
slightly higher latency in exchange for fewer, larger network requests.

Use when: bulk data loading, log shipping, high-volume event streams.

```python
from confluent_kafka import Producer
import time

producer = Producer({
    "bootstrap.servers": "localhost:9092",
    "acks": 1,                    # leader ack only — don't wait for replicas
    "compression.type": "lz4",    # compress batches
    "linger.ms": 20,              # wait up to 20ms to fill a batch
    "batch.size": 524288,         # 512KB max batch size
    "buffer.memory": 67108864,    # 64MB total producer buffer
    "max.in.flight.requests.per.connection": 5,  # 5 requests in-flight at once
})

total = 100_000
start = time.time()

for i in range(total):
    producer.produce(
        "my-first-topic",
        value=f"bulk-message-{i}".encode(),
    )
    # Call poll() periodically to prevent the internal queue from filling up
    if i % 10_000 == 0:
        producer.poll(0)

producer.flush()
elapsed = time.time() - start
print(f"Sent {total:,} messages in {elapsed:.2f}s ({total/elapsed:,.0f} msg/s)")
```

### Pattern 5 — Transactional producer (exactly-once)

Transactions allow you to send multiple messages atomically — either all are written
or none are. This is the foundation of exactly-once semantics.

Use when: read-process-write pipelines where duplicate processing would cause
incorrect results (e.g. financial transactions, inventory updates).

```python
from confluent_kafka import Producer

producer = Producer({
    "bootstrap.servers": "localhost:9092",
    # transactional.id must be unique per producer instance.
    # Using the same ID across restarts allows Kafka to fence zombie producers.
    "transactional.id": "order-processor-1",
    "enable.idempotence": True,  # required for transactions
    "acks": "all",               # required for transactions
})

# Initialise the transaction coordinator on the broker
producer.init_transactions()

try:
    producer.begin_transaction()

    # All messages in this transaction are atomic
    producer.produce("orders",    value=b'{"order_id": 42, "status": "confirmed"}')
    producer.produce("inventory", value=b'{"item_id": 7, "delta": -1}')
    producer.produce("audit-log", value=b'{"event": "order_confirmed", "order_id": 42}')

    # commit_transaction() sends all messages and commits atomically.
    # Consumers with isolation.level=read_committed will not see these
    # messages until the transaction is committed.
    producer.commit_transaction()
    print("Transaction committed — all 3 messages are visible atomically")

except Exception as e:
    # abort_transaction() rolls back — none of the messages will be visible
    producer.abort_transaction()
    print(f"Transaction aborted: {e}")
```

> **Note on transactions:** Consumers must be configured with
> `"isolation.level": "read_committed"` to skip messages from aborted transactions.
> The default `"read_uncommitted"` will see all messages including those from aborted
> transactions.

---

## Summary — Quick Reference

| Topic | Key points |
|---|---|
| `produce()` | Non-blocking — adds to local buffer only |
| `flush()` | Blocks until all buffered messages are acknowledged |
| `poll(0)` | Triggers callbacks; call in produce loops |
| `acks` | `0`=fastest/lossy, `1`=default, `"all"`=safest |
| `linger.ms` + `batch.size` | Controls batching — increase for throughput |
| `compression.type` | `lz4` for speed, `zstd` for ratio |
| `enable.idempotence` | Prevents duplicates on retry |
| Keys | Same key → same partition → ordered delivery |
| No key | Round-robin / sticky across all partitions |
| Headers | Metadata (trace IDs, content type) — not part of payload |
| Callbacks | Only fired during `poll()` or `flush()` |
| Transactions | Atomic multi-message writes; requires `transactional.id` |
