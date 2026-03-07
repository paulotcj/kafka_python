# Module 5 — Consumers

A **consumer** is any application that reads messages from Kafka topics. This module
walks through every aspect of the `confluent-kafka` Python consumer, from the
simplest possible example to production-grade patterns covering offset management,
rebalancing, partition assignment, and async consumption.

**Prerequisites:**
- Kafka is running locally via Docker (see Module 2)
- `confluent-kafka` is installed: `pip install confluent-kafka`
- `aiokafka` is installed for section 5.6: `pip install aiokafka`
- The examples assume `bootstrap.servers = "localhost:9092"` (single-broker).
  For the multi-broker setup use `"localhost:9092,localhost:9095,localhost:9096"`.
- A topic with messages exists. If not, run the producer from Module 4 or:

```bash
# Create the topic
docker exec kafka /opt/kafka/bin/kafka-topics.sh --create \
  --topic my-first-topic \
  --bootstrap-server localhost:9092 \
  --partitions 3 \
  --replication-factor 1

# Produce a few messages
docker exec -it kafka /opt/kafka/bin/kafka-console-producer.sh \
  --topic my-first-topic \
  --bootstrap-server localhost:9092
```

---

## 5.1 Basic Consumer

### How the consumer works internally

Before writing any code, it helps to understand what happens inside the consumer:

```
Your code                 Consumer (local process)               Kafka Broker
---------                 ------------------------               ------------
subscribe("topic")
     |
     v
  poll(2.0)  -------  sends fetch request  -------->  broker looks up
     |                                                 committed offset
     |                                                 for this group
     |                                                       |
     |        <------- returns batch of messages  ----------+
     v
process message
     |
     v
  poll(2.0)  -------  sends heartbeat + fetch  ----->  broker knows
                                                        consumer is alive
```

Key points:
- `subscribe()` registers the consumer in a **consumer group** and triggers a
  **rebalance** — Kafka decides which partitions this consumer is responsible for.
- `poll()` does two things at once: it fetches new messages AND sends a heartbeat to
  the broker proving this consumer is still alive.
- If `poll()` is not called within `max.poll.interval.ms`, the broker assumes the
  consumer has died and reassigns its partitions to other consumers in the group.
- The consumer tracks its position in each partition using **offsets** — the index of
  the last message it processed. Offsets are stored on the broker so they survive
  consumer restarts.

### Minimal example

```python
from confluent_kafka import Consumer

# Create the consumer with the minimum required configuration.
consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "my-consumer-group",       # identifies this consumer group
    "auto.offset.reset": "earliest",        # start from the beginning if no offset exists
})

# subscribe() tells the consumer which topic(s) to read.
# Multiple topics: consumer.subscribe(["topic-a", "topic-b"])
consumer.subscribe(["my-first-topic"])

# The poll loop — the heart of every Kafka consumer.
# poll(timeout) waits up to `timeout` seconds for a message.
# Returns a Message object or None.
empty_polls = 0
while empty_polls < 3:
    msg = consumer.poll(timeout=2.0)

    if msg is None:
        # No message yet — could be initial rebalance or no new messages
        empty_polls += 1
        continue

    empty_polls = 0

    if msg.error():
        print(f"Error: {msg.error()}")
        continue

    # Decode the bytes payload back to a string
    print(f"Received: {msg.value().decode('utf-8')}")

# Always close the consumer — commits offsets and notifies the group coordinator
consumer.close()
```

### Deserializing messages

Kafka messages are always raw bytes. You must decode them on the consumer side to
match whatever format the producer used:

```python
import json
from confluent_kafka import Consumer

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "deserialization-demo",
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
    if msg.error():
        continue

    raw_bytes = msg.value()

    # --- String messages ---
    text = raw_bytes.decode("utf-8")

    # --- JSON messages ---
    # data = json.loads(raw_bytes)

    # --- Key deserialization ---
    # Keys are also bytes; decode them the same way
    if msg.key():
        key = msg.key().decode("utf-8")
        print(f"key={key}  value={text}")
    else:
        print(f"value={text}")

consumer.close()
```

### Graceful shutdown

A consumer running in a loop needs to handle `Ctrl+C` (SIGINT) without losing
in-flight messages or leaving the group in a bad state:

```python
import signal
from confluent_kafka import Consumer

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "graceful-shutdown-demo",
    "auto.offset.reset": "earliest",
})
consumer.subscribe(["my-first-topic"])

running = True

def handle_shutdown(signum, frame):
    global running
    print("\nShutdown signal received — finishing current poll...")
    running = False

signal.signal(signal.SIGINT, handle_shutdown)   # Ctrl+C
signal.signal(signal.SIGTERM, handle_shutdown)  # kill / docker stop

while running:
    msg = consumer.poll(timeout=1.0)
    if msg is None:
        continue
    if msg.error():
        print(f"Error: {msg.error()}")
        continue
    print(f"Received: {msg.value().decode('utf-8')}")

# close() commits offsets and sends a LeaveGroup request.
# This triggers an immediate rebalance so other consumers can
# take over this consumer's partitions without waiting for a timeout.
consumer.close()
print("Consumer closed cleanly.")
```

---

## 5.2 Consumer Configuration

### bootstrap.servers and group.id

```python
consumer = Consumer({
    # Entry point to the cluster — see Module 4 section 4.2 for full explanation.
    # List all brokers for resilience.
    "bootstrap.servers": "localhost:9092,localhost:9095,localhost:9096",

    # group.id — the name of the consumer group this instance belongs to.
    # All consumers with the same group.id cooperate: Kafka distributes
    # partitions among them so each partition is read by exactly one consumer.
    # Different group.ids read the same topic independently — each group
    # maintains its own offset and sees all messages.
    "group.id": "my-consumer-group",
})
```

### auto.offset.reset

Controls what happens when a consumer group reads a partition for the very first time
(no committed offset exists yet):

```python
# "earliest" — start from offset 0. Read ALL messages ever written to the topic
#              (subject to retention). Good for: initial data loads, catching up.
consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "group-a",
    "auto.offset.reset": "earliest",
})

# "latest" — start from the current end of the log. Only receive messages
#            that arrive AFTER this consumer starts. Good for: live dashboards,
#            real-time processing where historical data is not needed.
consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "group-b",
    "auto.offset.reset": "latest",
})

# "none" — throw an error if no committed offset is found. Forces you to
#          explicitly manage offsets. Good for: strict pipelines where
#          accidentally starting from the wrong position would cause issues.
consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "group-c",
    "auto.offset.reset": "none",
})
```

To observe the difference, produce 10 messages, then start two consumers with
different `auto.offset.reset` values and different `group.id`s:

```bash
# Terminal 1 — sees all 10 historical messages + any new ones
# Terminal 2 — only sees messages produced after it starts
```

### enable.auto.commit and auto.commit.interval.ms

```python
# Auto-commit ON (default) — Kafka automatically commits the offset of the
# last polled message every auto.commit.interval.ms milliseconds.
# Simple to use but has risks — see section 5.3 for details.
consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "auto-commit-group",
    "auto.offset.reset": "earliest",
    "enable.auto.commit": True,
    "auto.commit.interval.ms": 5000,  # commit every 5 seconds (default)
})

# Auto-commit OFF — you control exactly when offsets are committed.
# More complex but necessary for reliable at-least-once processing.
consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "manual-commit-group",
    "auto.offset.reset": "earliest",
    "enable.auto.commit": False,
})
```

### max.poll.interval.ms and session.timeout.ms

These two timeouts control how Kafka detects dead consumers:

```python
consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "timeout-demo",

    # max.poll.interval.ms — the maximum time between two consecutive poll()
    # calls before Kafka considers this consumer dead and reassigns its partitions.
    # Default: 300000 (5 minutes).
    # If your message processing takes longer than this, increase it — otherwise
    # Kafka will evict your consumer mid-processing, causing a rebalance.
    "max.poll.interval.ms": 300000,

    # session.timeout.ms — how long the broker waits for a heartbeat before
    # declaring the consumer dead. Heartbeats are sent automatically inside poll().
    # Default: 45000 (45 seconds). Must be between group.min.session.timeout.ms
    # and group.max.session.timeout.ms on the broker (default 6s – 1800s).
    "session.timeout.ms": 45000,
})
```

### fetch.min.bytes and fetch.max.wait.ms

Controls how the broker batches fetch responses for throughput:

```python
consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "fetch-tuning-demo",

    # fetch.min.bytes — minimum data the broker accumulates before responding
    # to a fetch request. Default: 1 (respond immediately with whatever is available).
    # Increasing this reduces fetch requests at the cost of slightly higher latency.
    "fetch.min.bytes": 1,

    # fetch.max.wait.ms — maximum time the broker waits to accumulate
    # fetch.min.bytes before responding anyway. Default: 500ms.
    "fetch.max.wait.ms": 500,

    # max.partition.fetch.bytes — maximum bytes fetched per partition per request.
    # Default: 1MB. Increase if your messages are large.
    "max.partition.fetch.bytes": 1048576,
})
```

---

## 5.3 Offset Management

Offsets are the most important concept for reliable Kafka consumption. Getting them
wrong leads to either **data loss** (messages skipped) or **duplicate processing**
(messages processed twice).

### What is an offset?

Each partition is an ordered, append-only log. Every message gets a sequential
integer ID called an **offset**, starting at 0:

```
Partition 0:  [offset 0] [offset 1] [offset 2] [offset 3] ...
Partition 1:  [offset 0] [offset 1] [offset 2] ...
Partition 2:  [offset 0] [offset 1] ...
```

The consumer group stores a **committed offset** per partition on the broker. This is
the offset of the next message to read. When the consumer restarts, it picks up from
the committed offset — not from the beginning.

```
Committed offset = 3  →  next message to read is offset 3
                         (offsets 0, 1, 2 were already processed)
```

### Auto-commit — how it works and its risks

With `enable.auto.commit=True` (the default), the consumer periodically commits the
offset of the **last message returned by poll()**, regardless of whether your
application has finished processing it.

```python
from confluent_kafka import Consumer
import time

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "auto-commit-risk-demo",
    "auto.offset.reset": "earliest",
    "enable.auto.commit": True,
    "auto.commit.interval.ms": 5000,  # commits every 5 seconds
})
consumer.subscribe(["my-first-topic"])

# The risk scenario:
# 1. poll() returns messages at offsets 0, 1, 2
# 2. You start processing offset 0
# 3. Auto-commit fires after 5 seconds — commits offset 3 (marks 0,1,2 as done)
# 4. Your application crashes while processing offset 1
# 5. On restart, the consumer starts at offset 3 — offsets 1 and 2 are LOST

empty_polls = 0
while empty_polls < 3:
    msg = consumer.poll(timeout=2.0)
    if msg is None:
        empty_polls += 1
        continue
    empty_polls = 0
    if not msg.error():
        # If this processing step crashes or takes >5s,
        # the offset may be committed before the message is handled
        time.sleep(0.1)
        print(f"Processed offset {msg.offset()}")

consumer.close()
```

**Use auto-commit only when:** occasional message loss is acceptable (e.g. metrics,
analytics) and you prioritise simplicity over correctness.

### Manual commit — commit after each message

Commit only after you have successfully processed each message. Guarantees
at-least-once delivery — if the consumer crashes, it re-reads the last unprocessed
message.

```python
from confluent_kafka import Consumer

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "manual-commit-per-message",
    "auto.offset.reset": "earliest",
    "enable.auto.commit": False,  # turn off auto-commit
})
consumer.subscribe(["my-first-topic"])

empty_polls = 0
while empty_polls < 3:
    msg = consumer.poll(timeout=2.0)
    if msg is None:
        empty_polls += 1
        continue
    empty_polls = 0
    if msg.error():
        print(f"Error: {msg.error()}")
        continue

    # --- Process the message ---
    print(f"Processing offset {msg.offset()}: {msg.value().decode()}")

    # commit() — synchronous commit. Blocks until the broker confirms.
    # Only commit AFTER processing is complete.
    # If this line is never reached (crash during processing),
    # the consumer will re-read this message on restart — at-least-once.
    consumer.commit(msg)

consumer.close()
```

### Manual commit — commit after a batch (more efficient)

Committing after every single message adds overhead. Committing after a batch is more
efficient, at the cost of potentially reprocessing a few messages on crash:

```python
from confluent_kafka import Consumer

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "manual-commit-per-batch",
    "auto.offset.reset": "earliest",
    "enable.auto.commit": False,
})
consumer.subscribe(["my-first-topic"])

batch = []
BATCH_SIZE = 10

empty_polls = 0
while empty_polls < 3:
    msg = consumer.poll(timeout=2.0)
    if msg is None:
        empty_polls += 1
        # Commit any partial batch when we run out of messages
        if batch:
            consumer.commit(batch[-1])
            print(f"Committed partial batch of {len(batch)}")
            batch.clear()
        continue

    empty_polls = 0
    if msg.error():
        continue

    batch.append(msg)

    if len(batch) >= BATCH_SIZE:
        # Process all messages in the batch
        for m in batch:
            print(f"Processing offset {m.offset()}: {m.value().decode()}")

        # Commit the offset of the LAST message in the batch.
        # Kafka only needs the highest offset per partition — it implies
        # all previous offsets are also committed.
        consumer.commit(batch[-1])
        print(f"Committed batch of {len(batch)}")
        batch.clear()

consumer.close()
```

### Async commit

`consumer.commit()` is synchronous by default — it blocks until the broker confirms.
For higher throughput, use async commit:

```python
from confluent_kafka import Consumer

def on_commit(err, partitions):
    """Called when the async commit completes."""
    if err:
        print(f"Commit failed: {err}")
    else:
        for p in partitions:
            print(f"Committed partition={p.partition()} offset={p.offset}")

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "async-commit-demo",
    "auto.offset.reset": "earliest",
    "enable.auto.commit": False,
    "on_commit": on_commit,  # called when async commit completes
})
consumer.subscribe(["my-first-topic"])

empty_polls = 0
while empty_polls < 3:
    msg = consumer.poll(timeout=2.0)
    if msg is None:
        empty_polls += 1
        continue
    empty_polls = 0
    if msg.error():
        continue

    print(f"Processing: {msg.value().decode()}")

    # commit(asynchronous=True) returns immediately — the broker confirms later
    # via the on_commit callback. Do not use this if processing order matters.
    consumer.commit(msg, asynchronous=True)

consumer.close()
```

### Seeking to a specific offset

Sometimes you need to re-read messages from a specific position — for replaying
events, debugging, or recovering from a bug in your processing logic:

```python
from confluent_kafka import Consumer, TopicPartition

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "seek-demo",
    "auto.offset.reset": "earliest",
    "enable.auto.commit": False,
})
consumer.subscribe(["my-first-topic"])

# After subscribe, we need at least one poll() to trigger partition assignment
# before we can seek. poll() returns None during the rebalance — that's OK.
consumer.poll(timeout=5.0)

# Get the list of partitions assigned to this consumer
assignment = consumer.assignment()
print(f"Assigned partitions: {[p.partition for p in assignment]}")

# Seek partition 0 to offset 0 (rewind to the very beginning)
tp = TopicPartition("my-first-topic", partition=0, offset=0)
consumer.seek(tp)
print("Seeked partition 0 to offset 0")

# Now read from offset 0
empty_polls = 0
while empty_polls < 3:
    msg = consumer.poll(timeout=2.0)
    if msg is None:
        empty_polls += 1
        continue
    empty_polls = 0
    if not msg.error() and msg.partition() == 0:
        print(f"Re-read from partition 0, offset {msg.offset()}: {msg.value().decode()}")

consumer.close()
```

### Seeking to the beginning or end of a topic

```python
from confluent_kafka import Consumer, TopicPartition, OFFSET_BEGINNING, OFFSET_END

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "seek-special-demo",
    "auto.offset.reset": "earliest",
})
consumer.subscribe(["my-first-topic"])

# Trigger partition assignment
consumer.poll(timeout=5.0)
assignment = consumer.assignment()

# Rewind ALL assigned partitions to the very beginning
for tp in assignment:
    consumer.seek(TopicPartition(tp.topic, tp.partition, OFFSET_BEGINNING))
    print(f"Rewound partition {tp.partition} to beginning")

# Or seek to the end (skip all existing messages, only receive new ones)
# for tp in assignment:
#     consumer.seek(TopicPartition(tp.topic, tp.partition, OFFSET_END))

empty_polls = 0
while empty_polls < 3:
    msg = consumer.poll(timeout=2.0)
    if msg is None:
        empty_polls += 1
        continue
    empty_polls = 0
    if not msg.error():
        print(f"partition={msg.partition()} offset={msg.offset()} value={msg.value().decode()}")

consumer.close()
```

---

## 5.4 Consumer Lifecycle

### The poll-process-commit loop

The correct structure for a production consumer:

```
start
  |
  v
subscribe(topics)
  |
  v
+-------- poll() --------> rebalance? -----> partitions assigned/revoked
|             |
|         message received
|             |
|         process message
|             |
|         commit offset  <-- only after successful processing
|             |
+<-----------+
  |
  v
close()  (on shutdown signal)
```

```python
import signal
from confluent_kafka import Consumer

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "lifecycle-demo",
    "auto.offset.reset": "earliest",
    "enable.auto.commit": False,
})
consumer.subscribe(["my-first-topic"])

running = True
signal.signal(signal.SIGINT, lambda s, f: globals().update(running=False))

while running:
    msg = consumer.poll(timeout=1.0)
    if msg is None:
        continue
    if msg.error():
        print(f"Consumer error: {msg.error()}")
        continue

    # 1. Process
    print(f"Processing: partition={msg.partition()} offset={msg.offset()} value={msg.value().decode()}")

    # 2. Commit (only after successful processing)
    consumer.commit(msg)

# 3. Graceful shutdown
consumer.close()
print("Shutdown complete.")
```

### subscribe() vs assign()

`subscribe()` and `assign()` are the two ways to give a consumer its partitions:

| | `subscribe()` | `assign()` |
|---|---|---|
| **Partition selection** | Kafka decides (automatic) | You decide (manual) |
| **Group coordination** | Yes — participates in rebalances | No — standalone, no group |
| **Use case** | Most applications | Special cases only |
| **Offset tracking** | Per group, stored on broker | You manage it yourself |

```python
from confluent_kafka import Consumer, TopicPartition

# subscribe() — standard approach; Kafka assigns partitions automatically
consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "subscribe-demo",
})
consumer.subscribe(["my-first-topic"])

# assign() — you specify exactly which partitions to read
# No group.id needed; this consumer is standalone
consumer2 = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "assign-demo",  # still needed for offset storage
})
# Read only partition 0 and partition 2
consumer2.assign([
    TopicPartition("my-first-topic", 0),
    TopicPartition("my-first-topic", 2),
])
```

### Rebalance callbacks (on_assign and on_revoke)

A rebalance happens when consumers join or leave the group, or when partitions change.
During a rebalance, partition ownership is redistributed. You can hook into this
process to commit offsets before losing partitions:

```python
from confluent_kafka import Consumer

def on_assign(consumer, partitions):
    """
    Called when partitions are assigned to this consumer after a rebalance.
    partitions — list of TopicPartition objects just assigned.
    """
    print(f"Partitions assigned: {[p.partition for p in partitions]}")
    # Optional: seek to a custom offset after assignment
    # for p in partitions:
    #     p.offset = OFFSET_BEGINNING
    # consumer.assign(partitions)

def on_revoke(consumer, partitions):
    """
    Called just BEFORE partitions are taken away from this consumer.
    This is your last chance to commit offsets for those partitions.
    partitions — list of TopicPartition objects about to be revoked.
    """
    print(f"Partitions being revoked: {[p.partition for p in partitions]}")
    # Commit offsets for the partitions we are losing.
    # If we don't do this here, another consumer may re-read messages.
    consumer.commit(asynchronous=False)

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "rebalance-callbacks-demo",
    "auto.offset.reset": "earliest",
    "enable.auto.commit": False,
})

# Pass the callbacks to subscribe()
consumer.subscribe(
    ["my-first-topic"],
    on_assign=on_assign,
    on_revoke=on_revoke,
)

empty_polls = 0
while empty_polls < 5:
    msg = consumer.poll(timeout=2.0)
    if msg is None:
        empty_polls += 1
        continue
    empty_polls = 0
    if not msg.error():
        print(f"Received: partition={msg.partition()} offset={msg.offset()}")
        consumer.commit(msg)

consumer.close()
```

To observe the callbacks in action, start two consumers with the same `group.id` in
separate terminals — each time a consumer joins or leaves, you will see the callbacks
fire as Kafka redistributes partitions.

---

## 5.5 Consuming from Specific Partitions

Sometimes you need to read a specific partition directly — for example when building
a custom partition processor, a data exporter, or a debugging tool.

### assign() — read specific partitions from a specific offset

```python
from confluent_kafka import Consumer, TopicPartition

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "specific-partition-demo",
    "enable.auto.commit": False,
})

# Read partition 1 starting from offset 0
# No subscribe() — assign() replaces it entirely
consumer.assign([TopicPartition("my-first-topic", partition=1, offset=0)])

empty_polls = 0
while empty_polls < 3:
    msg = consumer.poll(timeout=2.0)
    if msg is None:
        empty_polls += 1
        continue
    empty_polls = 0
    if not msg.error():
        print(
            f"partition={msg.partition()}"
            f"  offset={msg.offset()}"
            f"  value={msg.value().decode()}"
        )

consumer.close()
```

### Reading multiple specific partitions

```python
from confluent_kafka import Consumer, TopicPartition

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "multi-partition-assign-demo",
    "enable.auto.commit": False,
})

# Assign partitions 0 and 2, each starting from a different offset
consumer.assign([
    TopicPartition("my-first-topic", partition=0, offset=0),
    TopicPartition("my-first-topic", partition=2, offset=5),  # start at offset 5
])

empty_polls = 0
while empty_polls < 3:
    msg = consumer.poll(timeout=2.0)
    if msg is None:
        empty_polls += 1
        continue
    empty_polls = 0
    if not msg.error():
        print(f"partition={msg.partition()} offset={msg.offset()} value={msg.value().decode()}")

consumer.close()
```

### Querying the watermarks of a partition

Watermarks tell you the first available offset and the last written offset for a
partition — useful for knowing how many messages exist or how far behind a consumer is:

```python
from confluent_kafka import Consumer, TopicPartition

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "watermark-demo",
})

topic = "my-first-topic"

# get_watermark_offsets(partition, timeout) returns (low, high):
#   low  — the earliest available offset (messages before this have been deleted
#           by retention policy)
#   high — the offset of the NEXT message to be written (i.e. current end + 1)
#          Number of messages available = high - low

for partition_id in range(3):
    tp = TopicPartition(topic, partition_id)
    low, high = consumer.get_watermark_offsets(tp, timeout=5.0)
    print(f"Partition {partition_id}: low={low}  high={high}  messages available={high - low}")

consumer.close()
```

---

## 5.6 Async Consumer (aiokafka)

`aiokafka` is the library to use when your application is already async — built with
FastAPI, aiohttp, or any other `asyncio`-based framework. It lets you consume Kafka
messages without blocking the event loop.

Install it first:

```bash
pip install aiokafka
```

### Basic async consumer

```python
import asyncio
from aiokafka import AIOKafkaConsumer

async def consume():
    consumer = AIOKafkaConsumer(
        "my-first-topic",
        bootstrap_servers="localhost:9092",
        group_id="async-consumer-demo",
        auto_offset_reset="earliest",
    )

    # start() connects to the broker and joins the consumer group
    await consumer.start()
    print("Consumer started.")

    try:
        # Async for loop — yields one message at a time without blocking
        async for msg in consumer:
            print(
                f"Received: partition={msg.partition}"
                f"  offset={msg.offset}"
                f"  value={msg.value.decode('utf-8')}"
            )
    finally:
        # stop() commits offsets and leaves the consumer group cleanly
        await consumer.stop()
        print("Consumer stopped.")

asyncio.run(consume())
```

### Async consumer with manual commit

```python
import asyncio
from aiokafka import AIOKafkaConsumer

async def consume():
    consumer = AIOKafkaConsumer(
        "my-first-topic",
        bootstrap_servers="localhost:9092",
        group_id="async-manual-commit",
        auto_offset_reset="earliest",
        enable_auto_commit=False,   # disable auto-commit
    )
    await consumer.start()

    try:
        async for msg in consumer:
            print(f"Processing offset {msg.offset}: {msg.value.decode()}")

            # Simulate async work — e.g. writing to a database
            await asyncio.sleep(0.01)

            # Commit after successful processing
            await consumer.commit()

    finally:
        await consumer.stop()

asyncio.run(consume())
```

### Integrating with FastAPI

A common real-world pattern: a FastAPI app that runs a Kafka consumer in the
background while also serving HTTP requests:

```python
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from aiokafka import AIOKafkaConsumer

received_messages = []

async def kafka_consumer_loop():
    """Background task — runs for the lifetime of the application."""
    consumer = AIOKafkaConsumer(
        "my-first-topic",
        bootstrap_servers="localhost:9092",
        group_id="fastapi-consumer-group",
        auto_offset_reset="latest",  # only new messages while the app is running
    )
    await consumer.start()
    try:
        async for msg in consumer:
            value = msg.value.decode("utf-8")
            print(f"[Kafka] Received: {value}")
            received_messages.append({
                "value": value,
                "partition": msg.partition,
                "offset": msg.offset,
            })
    finally:
        await consumer.stop()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start the background consumer when the app starts
    task = asyncio.create_task(kafka_consumer_loop())
    yield
    # Cancel the consumer when the app shuts down
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

app = FastAPI(lifespan=lifespan)

@app.get("/messages")
def get_messages():
    """HTTP endpoint that returns all messages received so far."""
    return {"count": len(received_messages), "messages": received_messages}
```

Run it:
```bash
pip install fastapi uvicorn aiokafka
uvicorn main:app --reload
```

Then open `http://localhost:8000/messages` to see messages your Kafka consumer has
received, and produce messages in another terminal to watch the list grow in real time.

### Handling backpressure in async consumers

If your message processing is slower than the rate of incoming messages, messages
pile up. Use a bounded `asyncio.Queue` to apply backpressure:

```python
import asyncio
from aiokafka import AIOKafkaConsumer

async def process_message(msg):
    """Simulate slow processing — e.g. a database write."""
    await asyncio.sleep(0.1)
    print(f"Processed offset {msg.offset}: {msg.value.decode()}")

async def consume_with_backpressure():
    queue = asyncio.Queue(maxsize=100)  # block the consumer if 100 messages are queued

    async def producer_task():
        """Reads from Kafka and puts messages onto the queue."""
        consumer = AIOKafkaConsumer(
            "my-first-topic",
            bootstrap_servers="localhost:9092",
            group_id="backpressure-demo",
            auto_offset_reset="earliest",
        )
        await consumer.start()
        try:
            async for msg in consumer:
                # put() blocks when the queue is full — this pauses Kafka fetching
                # and prevents unbounded memory growth
                await queue.put(msg)
        finally:
            await consumer.stop()

    async def worker_task():
        """Takes messages off the queue and processes them."""
        while True:
            msg = await queue.get()
            await process_message(msg)
            queue.task_done()

    # Run the consumer and multiple workers concurrently
    await asyncio.gather(
        producer_task(),
        worker_task(),
        worker_task(),  # second worker processes in parallel
    )

asyncio.run(consume_with_backpressure())
```

---

## Summary — Quick Reference

| Topic | Key points |
|---|---|
| `subscribe()` | Kafka assigns partitions automatically; participates in rebalances |
| `assign()` | You pick partitions manually; no group coordination |
| `poll(timeout)` | Fetches messages AND sends heartbeats; must be called regularly |
| `auto.offset.reset` | `earliest` = read all history; `latest` = only new messages |
| Auto-commit | Simple but risky — offset committed before processing completes |
| Manual commit | Commit after processing — guarantees at-least-once delivery |
| `consumer.commit(msg)` | Synchronous — blocks until broker confirms |
| `consumer.commit(asynchronous=True)` | Non-blocking — result delivered via callback |
| `consumer.seek(TopicPartition(...))` | Jump to any offset in any partition |
| `get_watermark_offsets()` | Returns (low, high) — how many messages exist |
| `on_assign` / `on_revoke` | Callbacks fired during rebalances |
| Graceful shutdown | `signal` handler sets flag → loop exits → `consumer.close()` |
| `aiokafka` | Async consumer for `asyncio` applications (FastAPI, aiohttp) |
| Backpressure | Use `asyncio.Queue(maxsize=N)` to limit in-flight messages |
