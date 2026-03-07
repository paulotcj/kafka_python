# Module 18 — Performance Tuning

## Overview

Kafka is fast by default. Most performance problems come from misconfiguration, not from Kafka itself. This module covers the levers you can pull to optimise throughput, latency, and cost — and how to measure whether your changes are working.

**The tuning mindset:**
1. Measure first (benchmark the baseline)
2. Change one thing at a time
3. Measure again and compare

---

## 18.1 Producer Tuning

### The Throughput vs Latency Trade-off

Every producer tuning decision is a dial between **latency** (how quickly a single message is delivered) and **throughput** (how many messages per second in total). The defaults favour low latency; high-throughput workloads need explicit tuning.

### Batching: linger.ms and batch.size

The producer holds messages in a buffer and sends them as batches. Two settings control when a batch is sent:

| Setting | Default | Behaviour |
|---|---|---|
| `linger.ms` | 0 | Wait up to N ms for the batch to fill before sending |
| `batch.size` | 16384 (16 KB) | Send immediately when the batch reaches this size |

A batch is sent when either limit is reached first.

With `linger.ms=0` (default), the producer sends as soon as a message arrives — one message per request. This is optimal for latency but terrible for throughput.

```python
from confluent_kafka import Producer

# Low latency (default) — good for interactive workloads
low_latency_producer = Producer({
    "bootstrap.servers": "localhost:9092",
    "linger.ms": 0,
    "batch.size": 16384,
})

# High throughput — good for bulk ingestion, log aggregation
high_throughput_producer = Producer({
    "bootstrap.servers": "localhost:9092",
    "linger.ms": 20,         # wait up to 20ms to accumulate a batch
    "batch.size": 65536,     # or send when batch reaches 64 KB
    "compression.type": "lz4",
    "acks": "1",             # leader-only ack (faster than "all")
    "queue.buffering.max.messages": 100000,
    "queue.buffering.max.kbytes": 102400,  # 100 MB buffer
})
```

### Compression

Compression reduces network bandwidth and broker storage at the cost of CPU time.

| Codec | CPU | Ratio | Latency | Best For |
|---|---|---|---|---|
| `none` | None | 1.0x | Lowest | Pre-compressed data |
| `gzip` | High | Best | Higher | Storage efficiency critical |
| `snappy` | Low | Good | Low | General purpose |
| `lz4` | Lowest | Good | Lowest | High throughput, low latency |
| `zstd` | Medium | Best | Low | Best ratio/speed balance |

```python
# Benchmark compression impact
import time
import json
import random
from confluent_kafka import Producer

def benchmark_producer(config: dict, messages: list, label: str):
    delivered = []

    def on_delivery(err, msg):
        if not err:
            delivered.append(1)

    producer = Producer(config)
    start = time.perf_counter()
    for msg in messages:
        producer.produce("benchmark-topic", value=msg, callback=on_delivery)
    producer.flush()
    elapsed = time.perf_counter() - start

    print(f"{label}: {len(delivered)} msgs in {elapsed:.2f}s = {len(delivered)/elapsed:.0f} msg/s")

# Generate test data (JSON is highly compressible)
messages = [
    json.dumps({
        "id": i,
        "event": "user_clicked",
        "user_id": f"user-{random.randint(1, 10000)}",
        "timestamp": time.time(),
        "properties": {"page": "/checkout", "item": "SKU-001"},
    }).encode()
    for i in range(10000)
]

base_config = {
    "bootstrap.servers": "localhost:9092",
    "linger.ms": 5,
    "batch.size": 65536,
}

for codec in ["none", "gzip", "snappy", "lz4", "zstd"]:
    benchmark_producer({**base_config, "compression.type": codec}, messages, f"compression={codec}")
```

### acks Setting

| `acks` | Who Must Acknowledge | Durability | Latency |
|---|---|---|---|
| `0` | No one (fire and forget) | None | Lowest |
| `1` | Partition leader only | Medium | Low |
| `all` | All in-sync replicas | Highest | Higher |

```python
# For analytics pipelines where some loss is acceptable
analytics_producer = Producer({
    "bootstrap.servers": "localhost:9092",
    "acks": "1",
    "linger.ms": 10,
    "compression.type": "lz4",
})

# For financial transactions — no message loss acceptable
financial_producer = Producer({
    "bootstrap.servers": "localhost:9092",
    "acks": "all",
    "enable.idempotence": True,
    "linger.ms": 1,
})
```

### Buffer Memory

The producer maintains an in-memory buffer for unsent messages:

```python
producer = Producer({
    "bootstrap.servers": "localhost:9092",
    # Total memory available to the producer for buffering (bytes)
    "queue.buffering.max.kbytes": 102400,    # 100 MB (default: 1 GB)
    # Max number of messages in the queue
    "queue.buffering.max.messages": 1000000,
    # How long to block produce() when the buffer is full (ms)
    # After this, produce() raises BufferError
    "queue.buffering.backpressure.threshold": 10,
})
```

### Complete Producer Configuration Reference

```python
optimised_producer = Producer({
    "bootstrap.servers": "localhost:9092",

    # Durability
    "acks": "all",
    "enable.idempotence": True,

    # Batching
    "linger.ms": 10,
    "batch.size": 65536,          # 64 KB

    # Compression
    "compression.type": "lz4",

    # Buffer
    "queue.buffering.max.kbytes": 102400,   # 100 MB
    "queue.buffering.max.messages": 500000,

    # Retries (automatically set when enable.idempotence=True)
    "retries": 2147483647,
    "retry.backoff.ms": 100,
    "delivery.timeout.ms": 120000,           # 2 minutes total delivery window

    # In-flight requests (max 5 with idempotence)
    "max.in.flight.requests.per.connection": 5,
})
```

---

## 18.2 Consumer Tuning

### Fetch Tuning

The consumer fetches messages from brokers in batches. Two settings control when a fetch response is sent:

```python
consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "my-group",

    # Minimum bytes the broker waits to accumulate before responding
    # Default: 1 byte (respond immediately)
    # Increase for higher throughput (broker waits for more data)
    "fetch.min.bytes": 65536,   # 64 KB

    # Maximum time the broker waits even if fetch.min.bytes not reached
    # Default: 500ms
    "fetch.max.wait.ms": 500,

    # Maximum bytes returned per fetch per partition
    # Default: 1 MB — increase if your messages are large
    "max.partition.fetch.bytes": 1048576,

    # Maximum messages returned per poll() call
    # Smaller = lower latency per batch, larger = higher throughput
    "max.poll.records": 500,

    # Maximum time between poll() calls before the broker considers
    # the consumer dead and triggers a rebalance
    # Must be larger than your processing time per batch
    "max.poll.interval.ms": 300000,   # 5 minutes

    # How often the consumer sends heartbeats to the broker
    "session.timeout.ms": 45000,
    "heartbeat.interval.ms": 15000,   # ~1/3 of session.timeout.ms
})
```

### Processing in Batches

Instead of processing one message at a time, accumulate a batch and process together:

```python
import json
from confluent_kafka import Consumer

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "batch-processor",
    "auto.offset.reset": "earliest",
    "enable.auto.commit": False,
    "max.poll.records": 500,
    "fetch.min.bytes": 65536,
    "fetch.max.wait.ms": 200,
})
consumer.subscribe(["orders"])

BATCH_SIZE = 100
BATCH_TIMEOUT_S = 5.0

import time

def process_batch(messages: list):
    """Process a batch of decoded messages — e.g. bulk insert to a database."""
    print(f"Processing batch of {len(messages)} messages")
    # db.bulk_insert(messages)  # much faster than one insert per message

batch = []
last_flush = time.time()
last_msg = None

while True:
    msg = consumer.poll(timeout=0.1)  # short timeout — we manage the batch timer

    if msg and not msg.error():
        batch.append(json.loads(msg.value().decode()))
        last_msg = msg

    # Flush when batch is full OR when timeout elapsed
    should_flush = (
        len(batch) >= BATCH_SIZE
        or (batch and time.time() - last_flush > BATCH_TIMEOUT_S)
    )

    if should_flush and batch:
        process_batch(batch)
        consumer.commit(message=last_msg, asynchronous=False)
        batch.clear()
        last_msg = None
        last_flush = time.time()
```

### Multi-Threaded Consumer

`confluent_kafka.Consumer` is not thread-safe — one consumer per thread. For parallel processing, use a thread pool for the business logic while keeping the consumer on the main thread:

```python
import json
import threading
import queue
from confluent_kafka import Consumer

WORKER_COUNT = 4
work_queue = queue.Queue(maxsize=1000)

def worker(worker_id: int):
    """Process messages from the work queue."""
    while True:
        item = work_queue.get()
        if item is None:  # shutdown signal
            break
        msg, data = item
        try:
            # Your heavy processing here
            process_order(data)
        except Exception as e:
            print(f"Worker {worker_id} error: {e}")
        finally:
            work_queue.task_done()

def process_order(data: dict):
    import time
    time.sleep(0.01)  # simulate work
    print(f"Processed: {data.get('order_id')}")

# Start worker threads
workers = []
for i in range(WORKER_COUNT):
    t = threading.Thread(target=worker, args=(i,), daemon=True)
    t.start()
    workers.append(t)

# Consumer on the main thread
consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "threaded-consumer",
    "auto.offset.reset": "earliest",
    "enable.auto.commit": True,
    "max.poll.records": 100,
})
consumer.subscribe(["orders"])

try:
    empty = 0
    while empty < 5:
        msg = consumer.poll(timeout=1.0)
        if msg is None:
            empty += 1
            continue
        empty = 0
        if not msg.error():
            data = json.loads(msg.value().decode())
            work_queue.put((msg, data))  # hand off to worker thread
finally:
    # Shutdown workers
    for _ in workers:
        work_queue.put(None)
    work_queue.join()
    consumer.close()
```

> **Note:** With this pattern, offset commits are not strictly tied to processing completion. If a worker fails after the consumer has auto-committed, the message is lost. For stronger guarantees, disable auto-commit and commit only after `work_queue.join()` for each batch.

---

## 18.3 Partition Tuning

### Choosing Partition Count

Partitions are the unit of parallelism. More partitions = more potential throughput, but also more overhead.

**Rule of thumb:** `partitions = max(target_throughput / single_partition_throughput, target_consumer_count)`

| Factor | Effect |
|---|---|
| More partitions | Higher throughput ceiling, more consumer parallelism |
| More partitions | More open file handles on broker, more rebalance time |
| More partitions | More end-to-end latency for replication |
| Fewer partitions | Less overhead, faster rebalances, simpler ordering |

**Practical guidance:**
- Start with **6–12 partitions** for most topics
- Match partitions to your peak consumer parallelism (e.g. 6 consumers → at least 6 partitions)
- Avoid > 100 partitions per topic unless you have measured need

### Hot Partition Problem

If all messages land on one partition (because they all have the same key, or you have a single high-volume key), that partition becomes a bottleneck regardless of total partition count.

```python
import json
import time
from collections import defaultdict
from confluent_kafka import Consumer

def detect_hot_partitions(bootstrap_servers: str, topic: str, duration_seconds: int = 30):
    """Sample message distribution to detect hot partitions."""
    consumer = Consumer({
        "bootstrap.servers": bootstrap_servers,
        "group.id": f"hot-partition-detector-{time.time()}",
        "auto.offset.reset": "latest",
    })
    consumer.subscribe([topic])

    counts = defaultdict(int)
    start = time.time()
    print(f"Sampling {topic} for {duration_seconds}s...")

    while time.time() - start < duration_seconds:
        msg = consumer.poll(timeout=0.5)
        if msg and not msg.error():
            counts[msg.partition()] += 1

    consumer.close()

    total = sum(counts.values())
    if total == 0:
        print("No messages received.")
        return

    print(f"\nPartition distribution ({total} messages):")
    for p, count in sorted(counts.items()):
        pct = count / total * 100
        bar = "#" * int(pct / 2)
        print(f"  Partition {p}: {count:6d} ({pct:5.1f}%) {bar}")

    max_pct = max(counts.values()) / total * 100
    if max_pct > 70:
        hot_partition = max(counts, key=counts.get)
        print(f"\n  WARNING: Partition {hot_partition} is hot ({max_pct:.1f}% of traffic)")
        print("  Consider adding randomness to high-volume keys or using a custom partitioner.")

detect_hot_partitions("localhost:9092", "orders")
```

**Fixing a hot partition:** If `user_id="enterprise-client-1"` generates 90% of traffic, add a suffix to distribute it:

```python
import random

def get_partition_key(user_id: str, num_shards: int = 10) -> str:
    """Add a random shard suffix to high-volume keys."""
    return f"{user_id}-{random.randint(0, num_shards - 1)}"

# Consumer-side: strip the shard suffix to recover the original user_id
def get_user_id_from_key(key: str) -> str:
    return key.rsplit("-", 1)[0]
```

---

## 18.4 Benchmarking with Built-In Tools

Kafka ships with performance test tools. Use them to establish a baseline before tuning.

### Producer Performance Test

```bash
# Test producer throughput: 1 million 1 KB messages
docker exec kafka /opt/kafka/bin/kafka-producer-perf-test.sh \
  --topic benchmark-topic \
  --num-records 1000000 \
  --record-size 1024 \
  --throughput -1 \
  --producer-props \
    bootstrap.servers=localhost:9092 \
    acks=all \
    linger.ms=5 \
    batch.size=65536 \
    compression.type=lz4
```

Sample output:
```
100000 records sent, 98234.0 records/sec (95.9 MB/sec), 3.5 ms avg latency, 245.0 ms max latency
200000 records sent, 102041.0 records/sec (99.6 MB/sec), 2.8 ms avg latency ...
...
1000000 records sent, 101010.1 records/sec (98.6 MB/sec), 3.1 ms avg latency, 245 ms max latency
```

**Key fields:**
- `records/sec` — throughput in messages per second
- `MB/sec` — throughput in megabytes per second
- `avg latency` — average time from produce to broker ack
- `max latency` — worst-case latency spike (often first batch)

### Consumer Performance Test

```bash
# Test consumer throughput reading the same 1M messages
docker exec kafka /opt/kafka/bin/kafka-consumer-perf-test.sh \
  --bootstrap-server localhost:9092 \
  --topic benchmark-topic \
  --messages 1000000 \
  --group benchmark-consumer-group \
  --threads 1
```

Sample output:
```
start.time             end.time               data.consumed.in.MB  MB.sec  data.consumed.in.nMsg  nMsg.sec
2025-01-01 12:00:00    2025-01-01 12:00:08    976.6                122.1   1000000                125000
```

### Python Throughput Benchmark

```python
# throughput_test.py

import time
import threading
from confluent_kafka import Producer, Consumer
from confluent_kafka.admin import AdminClient, NewTopic

BOOTSTRAP_SERVERS = "localhost:9092"
TOPIC = "perf-test"
MESSAGE_COUNT = 50000
MESSAGE_SIZE = 1024  # bytes

def setup():
    admin = AdminClient({"bootstrap.servers": BOOTSTRAP_SERVERS})
    try:
        admin.create_topics([NewTopic(TOPIC, 6, 1)])[TOPIC].result()
    except Exception:
        pass  # topic already exists

def producer_benchmark(config_label: str, extra_config: dict):
    payload = b"x" * MESSAGE_SIZE
    delivered = []

    def on_delivery(err, msg):
        if not err:
            delivered.append(time.perf_counter())

    config = {"bootstrap.servers": BOOTSTRAP_SERVERS, **extra_config}
    producer = Producer(config)

    start = time.perf_counter()
    for _ in range(MESSAGE_COUNT):
        producer.produce(TOPIC, value=payload, callback=on_delivery)
    producer.flush()
    end = time.perf_counter()

    elapsed = end - start
    throughput = MESSAGE_COUNT / elapsed
    data_rate_mb = (MESSAGE_COUNT * MESSAGE_SIZE / 1024 / 1024) / elapsed
    print(f"{config_label}:")
    print(f"  {throughput:,.0f} msg/s  |  {data_rate_mb:.1f} MB/s  |  {elapsed:.2f}s total")

setup()

print("=== Producer Benchmark ===\n")

producer_benchmark("baseline (acks=1, no compression)", {
    "acks": "1",
})
producer_benchmark("linger.ms=10", {
    "acks": "1",
    "linger.ms": 10,
    "batch.size": 65536,
})
producer_benchmark("linger.ms=10 + lz4", {
    "acks": "1",
    "linger.ms": 10,
    "batch.size": 65536,
    "compression.type": "lz4",
})
producer_benchmark("linger.ms=10 + zstd", {
    "acks": "1",
    "linger.ms": 10,
    "batch.size": 65536,
    "compression.type": "zstd",
})
```

---

## 18.5 End-to-End Latency Measurement

Throughput is only half the picture. For latency-sensitive applications, measure the time from produce to consume:

```python
# latency_test.py

import time
import json
import statistics
import threading
from confluent_kafka import Producer, Consumer
from confluent_kafka.admin import AdminClient, NewTopic

BOOTSTRAP_SERVERS = "localhost:9092"
TOPIC = "latency-test"
COUNT = 1000

latencies = []
receive_event = threading.Event()

def setup():
    admin = AdminClient({"bootstrap.servers": BOOTSTRAP_SERVERS})
    try:
        admin.create_topics([NewTopic(TOPIC, 1, 1)])[TOPIC].result()
    except Exception:
        pass

def consumer_thread():
    consumer = Consumer({
        "bootstrap.servers": BOOTSTRAP_SERVERS,
        "group.id": f"latency-test-{time.time()}",
        "auto.offset.reset": "latest",
        "fetch.min.bytes": 1,
        "fetch.max.wait.ms": 0,  # respond immediately
    })
    consumer.subscribe([TOPIC])
    consumer.poll(timeout=2.0)  # wait for assignment
    receive_event.set()         # signal producer to start

    received = 0
    while received < COUNT:
        msg = consumer.poll(timeout=1.0)
        if msg and not msg.error():
            sent_ts = json.loads(msg.value())["sent_at"]
            latency_ms = (time.perf_counter() - sent_ts) * 1000
            latencies.append(latency_ms)
            received += 1

    consumer.close()

setup()
t = threading.Thread(target=consumer_thread)
t.start()

# Wait for consumer to be ready
receive_event.wait(timeout=10)

producer = Producer({
    "bootstrap.servers": BOOTSTRAP_SERVERS,
    "linger.ms": 0,
    "acks": "1",
})

for i in range(COUNT):
    payload = json.dumps({"seq": i, "sent_at": time.perf_counter()}).encode()
    producer.produce(TOPIC, value=payload)
    producer.poll(0)

producer.flush()
t.join()

print(f"\nEnd-to-End Latency ({COUNT} messages):")
print(f"  Min:    {min(latencies):.2f} ms")
print(f"  Median: {statistics.median(latencies):.2f} ms")
print(f"  p95:    {sorted(latencies)[int(COUNT * 0.95)]:.2f} ms")
print(f"  p99:    {sorted(latencies)[int(COUNT * 0.99)]:.2f} ms")
print(f"  Max:    {max(latencies):.2f} ms")
```

---

## 18.6 Performance Tuning Reference Card

### Producer

| Goal | Setting | Recommended Value |
|---|---|---|
| Max throughput | `linger.ms` | 20–50 |
| Max throughput | `batch.size` | 65536–1048576 |
| Max throughput | `compression.type` | `lz4` or `zstd` |
| Max throughput | `acks` | `1` (if loss acceptable) |
| Max durability | `acks` | `all` |
| Max durability | `enable.idempotence` | `true` |
| Min latency | `linger.ms` | `0` |
| Min latency | `batch.size` | `16384` (default) |

### Consumer

| Goal | Setting | Recommended Value |
|---|---|---|
| Max throughput | `fetch.min.bytes` | 65536–1048576 |
| Max throughput | `fetch.max.wait.ms` | 500 |
| Max throughput | `max.poll.records` | 500–2000 |
| Min latency | `fetch.min.bytes` | `1` (default) |
| Min latency | `fetch.max.wait.ms` | `0` |
| Stability | `session.timeout.ms` | 45000 |
| Stability | `heartbeat.interval.ms` | 15000 |
| Stability | `max.poll.interval.ms` | > processing time per batch |
