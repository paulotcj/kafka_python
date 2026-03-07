# Module 8 — Consumer Groups and Rebalancing

This module sits at the intersection of operations and development. Understanding
consumer groups and rebalancing is essential for writing Kafka consumers that are
correct under real-world conditions — scaling up, rolling deployments, crashes, and
slow processing. Misunderstanding rebalancing is one of the most common sources of
duplicate messages and unexpected consumer behaviour in production.

**Prerequisites:**
- Kafka is running locally via Docker (see Module 2)
- `confluent-kafka` is installed: `pip install confluent-kafka`
- A topic with multiple partitions (the examples use 6 partitions)

```bash
# Create the topic used throughout this module
docker exec kafka /opt/kafka/bin/kafka-topics.sh --create \
  --topic group-demo \
  --bootstrap-server localhost:9092 \
  --partitions 6 \
  --replication-factor 1
```

---

## 8.1 How Consumer Groups Work

### The core idea

A **consumer group** is a set of consumers that cooperate to consume a topic. Kafka
distributes the topic's partitions across the consumers in the group so that:

1. Each partition is assigned to **exactly one** consumer in the group at any time.
2. No two consumers in the same group ever read the same partition simultaneously.
3. If there are more partitions than consumers, some consumers handle multiple partitions.
4. If there are more consumers than partitions, the extra consumers sit idle.

This is the mechanism that makes horizontal scaling possible: to double throughput,
double the number of consumers (up to the partition count limit).

### Visual: partition assignment across group sizes

```
Topic "group-demo" — 6 partitions

1 consumer in the group:
  Consumer A  →  partitions [0, 1, 2, 3, 4, 5]

2 consumers in the group:
  Consumer A  →  partitions [0, 1, 2]
  Consumer B  →  partitions [3, 4, 5]

3 consumers in the group:
  Consumer A  →  partitions [0, 1]
  Consumer B  →  partitions [2, 3]
  Consumer C  →  partitions [4, 5]

6 consumers in the group:
  Consumer A  →  partition [0]
  Consumer B  →  partition [1]
  Consumer C  →  partition [2]
  Consumer D  →  partition [3]
  Consumer E  →  partition [4]
  Consumer F  →  partition [5]

7 consumers in the group (one idle):
  Consumers A–F  →  one partition each
  Consumer G  →  no partition — idle, wasted resource
```

### Multiple consumer groups — independent cursors

Different consumer groups read the same topic independently. Each group has its own
committed offsets. Producing one message makes it available to every consumer group
that subscribes to the topic.

```
Topic "orders": [msg0] [msg1] [msg2] [msg3] [msg4]

Group "email-service":     committed offset = 3  (has processed msg0–2)
Group "analytics-service": committed offset = 1  (has processed msg0)
Group "audit-log":         committed offset = 5  (fully caught up)
```

This is fundamentally different from traditional message queues where a message is
consumed once and gone. In Kafka, every group sees every message.

### Demonstrating multiple groups with Python

```python
# Run this script in two terminals simultaneously, with different GROUP_ID values
# Terminal 1: GROUP_ID = "group-alpha"
# Terminal 2: GROUP_ID = "group-beta"
# Both will receive all messages independently.

import sys
from confluent_kafka import Consumer, Producer

GROUP_ID = sys.argv[1] if len(sys.argv) > 1 else "group-alpha"
TOPIC = "group-demo"

# First, produce some messages
producer = Producer({"bootstrap.servers": "localhost:9092"})
for i in range(10):
    producer.produce(TOPIC, value=f"message-{i}".encode())
producer.flush()
print(f"[{GROUP_ID}] Produced 10 messages")

# Now consume them as a specific group
consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": GROUP_ID,
    "auto.offset.reset": "earliest",
})
consumer.subscribe([TOPIC])

received = 0
empty_polls = 0
while empty_polls < 3:
    msg = consumer.poll(timeout=2.0)
    if msg is None:
        empty_polls += 1
        continue
    empty_polls = 0
    if not msg.error():
        received += 1
        print(f"[{GROUP_ID}] partition={msg.partition()} offset={msg.offset()} value={msg.value().decode()}")

print(f"[{GROUP_ID}] Total received: {received}")
consumer.close()
```

Run in two terminals at the same time:
```bash
# Terminal 1
python consumer_groups_demo.py group-alpha

# Terminal 2
python consumer_groups_demo.py group-beta
```

Both will print all 10 messages — they do not share or compete for messages.

### Observing group state with CLI

```bash
# List all consumer groups
docker exec kafka /opt/kafka/bin/kafka-consumer-groups.sh \
  --list \
  --bootstrap-server localhost:9092

# Describe a specific group — shows partition assignment and lag
docker exec kafka /opt/kafka/bin/kafka-consumer-groups.sh \
  --describe \
  --group group-alpha \
  --bootstrap-server localhost:9092
```

Output:
```
GROUP        TOPIC       PARTITION  CURRENT-OFFSET  LOG-END-OFFSET  LAG  CONSUMER-ID          HOST
group-alpha  group-demo  0          4               4               0    consumer-1-abc...    /172.17.0.1
group-alpha  group-demo  1          3               3               0    consumer-1-abc...    /172.17.0.1
group-alpha  group-demo  2          2               2               0    consumer-1-abc...    /172.17.0.1
...
```

---

## 8.2 Rebalancing

### What is a rebalance?

A **rebalance** is the process by which Kafka redistributes partition ownership among
the consumers in a group. It is triggered automatically whenever the group membership
changes.

**Events that trigger a rebalance:**

| Trigger | Why it happens |
|---|---|
| New consumer joins the group | After calling `subscribe()` and the first `poll()` |
| Consumer leaves gracefully | After calling `consumer.close()` |
| Consumer crashes or times out | `session.timeout.ms` expires without a heartbeat |
| `max.poll.interval.ms` exceeded | Consumer is processing too slowly between polls |
| Partition count changes | Topic partition count is increased |
| Subscription change | Consumer calls `subscribe()` with a different topic list |

### The two types of rebalance

#### Eager rebalancing (stop-the-world) — the old default

In eager rebalancing, every consumer in the group stops processing and gives up **all**
of its partitions before the new assignment begins. This creates a window where no
consumer is reading from any partition.

```
Before rebalance:
  Consumer A: partitions [0, 1, 2]
  Consumer B: partitions [3, 4, 5]

Rebalance triggered (Consumer C joins):
  Step 1: ALL consumers revoke ALL partitions
    Consumer A: partitions []   ← stop-the-world begins
    Consumer B: partitions []

  Step 2: Group coordinator assigns new partitions
    Consumer A: partitions [0, 1]
    Consumer B: partitions [2, 3]
    Consumer C: partitions [4, 5]   ← back to work
```

During Step 1 and Step 2, the entire group pauses. For large groups or slow
coordination, this pause can last several seconds.

#### Cooperative (incremental) rebalancing — the modern default

In cooperative rebalancing, only the partitions that need to move are revoked. All
other consumers keep processing their existing partitions without interruption.

```
Before rebalance:
  Consumer A: partitions [0, 1, 2]
  Consumer B: partitions [3, 4, 5]

Rebalance triggered (Consumer C joins):
  Round 1 — only partitions that will move are revoked:
    Consumer A: revokes partition [2]   ← only one partition pauses
    Consumer B: revokes partition [5]

  Round 2 — revoked partitions are assigned to the new consumer:
    Consumer A: partitions [0, 1]       ← kept most of its work
    Consumer B: partitions [3, 4]
    Consumer C: partitions [2, 5]       ← received the freed partitions
```

The group keeps processing during the rebalance — only the partitions being moved are
briefly paused.

### Configuring the partition assignor

```python
from confluent_kafka import Consumer

# RangeAssignor (classic eager) — assigns contiguous partition ranges
# Good for: ordered processing across consumers
consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "range-group",
    "partition.assignment.strategy": "range",
})

# RoundRobinAssignor (classic eager) — distributes partitions evenly round-robin
# Good for: even load distribution when all partitions have similar throughput
consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "roundrobin-group",
    "partition.assignment.strategy": "roundrobin",
})

# StickyAssignor (eager but minimises partition movement)
# Tries to keep the same partitions on the same consumer across rebalances
consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "sticky-group",
    "partition.assignment.strategy": "sticky",
})

# CooperativeStickyAssignor — incremental rebalancing, minimises movement
# RECOMMENDED for most production use cases
consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "cooperative-group",
    "partition.assignment.strategy": "cooperative-sticky",
})
```

### Observing a rebalance in real time

Open three terminals. In each, run this script with a different consumer ID:

```python
# save as: observe_rebalance.py
import sys
import signal
from confluent_kafka import Consumer

CONSUMER_ID = sys.argv[1] if len(sys.argv) > 1 else "consumer-1"
TOPIC = "group-demo"

def on_assign(consumer, partitions):
    print(f"\n[{CONSUMER_ID}] ASSIGNED: {[p.partition for p in partitions]}")

def on_revoke(consumer, partitions):
    print(f"\n[{CONSUMER_ID}] REVOKED:  {[p.partition for p in partitions]}")
    consumer.commit(asynchronous=False)

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "rebalance-demo-group",
    "auto.offset.reset": "latest",
    "enable.auto.commit": False,
    "partition.assignment.strategy": "cooperative-sticky",
})
consumer.subscribe([TOPIC], on_assign=on_assign, on_revoke=on_revoke)

running = True
signal.signal(signal.SIGINT, lambda s, f: globals().update(running=False))

while running:
    msg = consumer.poll(timeout=1.0)
    if msg and not msg.error():
        print(f"[{CONSUMER_ID}] partition={msg.partition()} offset={msg.offset()}")

consumer.close()
```

```bash
# Terminal 1 — start first consumer
python observe_rebalance.py consumer-1

# Terminal 2 — start second consumer (triggers rebalance)
python observe_rebalance.py consumer-2

# Terminal 3 — start third consumer (triggers another rebalance)
python observe_rebalance.py consumer-3

# Kill one terminal (Ctrl+C) — triggers a rebalance to redistribute its partitions
```

Watch the `ASSIGNED` and `REVOKED` callbacks print in each terminal as the group
rebalances. With `cooperative-sticky`, you will see that only the partitions that move
are revoked — the others keep running uninterrupted.

---

## 8.3 Static Group Membership

### The problem: rebalances during rolling deployments

A typical rolling deployment restarts consumers one at a time. With dynamic group
membership (the default), each restart triggers a rebalance:

```
Rolling deployment — 3 consumers, takes ~30s each:

t=0s:   Consumer A restarts → rebalance (all consumers pause)
t=30s:  Consumer B restarts → rebalance (all consumers pause again)
t=60s:  Consumer C restarts → rebalance (one more pause)

3 rebalances × pause duration = significant processing downtime
```

Each rebalance happens because Kafka sees the restarting consumer as a new member
(new client ID, no history), triggering a full partition redistribution.

### The solution: group.instance.id

Assigning a stable `group.instance.id` to each consumer tells Kafka "this consumer
will come back". When a consumer with a known instance ID disconnects and reconnects
within `session.timeout.ms`, Kafka does **not** trigger a rebalance — it simply waits
for it to reconnect and hands back its original partitions.

```python
import os
from confluent_kafka import Consumer

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "my-service",

    # A unique, stable ID for this specific consumer instance.
    # Must be unique within the group — a good value is the pod name
    # in Kubernetes or hostname in bare-metal deployments.
    "group.instance.id": os.environ.get("HOSTNAME", "consumer-instance-1"),

    # Increase session timeout to give the consumer time to restart
    # before Kafka gives up and triggers a rebalance.
    # Set this to be longer than your expected restart time.
    "session.timeout.ms": 60000,   # 60 seconds

    "auto.offset.reset": "earliest",
    "partition.assignment.strategy": "cooperative-sticky",
})
consumer.subscribe(["group-demo"])
```

### Static vs dynamic group membership comparison

| | Dynamic (default) | Static (`group.instance.id`) |
|---|---|---|
| **Consumer restart** | Triggers rebalance immediately | No rebalance if back within `session.timeout.ms` |
| **Consumer crash** | Rebalance after `session.timeout.ms` | Same — rebalance after timeout |
| **Rolling deployment** | Multiple rebalances — one per pod restart | Zero rebalances if restarts are fast |
| **Complexity** | None | Requires unique stable ID per instance |
| **Best for** | Development, stateless consumers | Production pods, stateful consumers |

### Verifying static membership with CLI

```bash
# Describe the group — look for the group.instance.id in the CONSUMER-ID column
docker exec kafka /opt/kafka/bin/kafka-consumer-groups.sh \
  --describe \
  --group my-service \
  --bootstrap-server localhost:9092
```

With static membership the CONSUMER-ID column will include the `group.instance.id`
value rather than a random UUID suffix.

---

## 8.4 Rebalance Listeners

Rebalance listeners are callbacks that fire at key moments in the rebalance lifecycle.
They give you a chance to save state before losing partitions and to restore it when
gaining new ones. Writing these correctly is the difference between at-least-once and
accidentally-at-most-once message processing.

### The two callbacks

```
on_revoke(consumer, partitions)
  Called BEFORE partitions are taken away.
  Last chance to commit offsets for those partitions.
  If you miss this, another consumer may re-read messages you already processed.

on_assign(consumer, partitions)
  Called AFTER new partitions are assigned.
  Opportunity to seek to a custom offset or restore local state.
```

### Correct pattern — commit in on_revoke

```python
import signal
from confluent_kafka import Consumer

TOPIC = "group-demo"

# Track the last processed message per partition so we know what to commit
last_processed = {}  # {partition: Message}

def on_assign(consumer, partitions):
    assigned = [p.partition for p in partitions]
    print(f"Assigned partitions: {assigned}")
    # Optionally: seek to a custom offset or load per-partition state here

def on_revoke(consumer, partitions):
    revoked = [p.partition for p in partitions]
    print(f"Revoking partitions: {revoked} — committing offsets now")

    # Commit offsets for every partition we are losing.
    # Use synchronous commit here — we must not return from on_revoke
    # until the commit is confirmed. An async commit might not complete
    # before the partitions are handed to another consumer.
    for p in partitions:
        pid = p.partition
        if pid in last_processed:
            consumer.commit(last_processed[pid], asynchronous=False)
            print(f"  Committed partition {pid} at offset {last_processed[pid].offset()}")
            del last_processed[pid]

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "safe-rebalance-group",
    "auto.offset.reset": "earliest",
    "enable.auto.commit": False,
    "partition.assignment.strategy": "cooperative-sticky",
})
consumer.subscribe([TOPIC], on_assign=on_assign, on_revoke=on_revoke)

running = True
signal.signal(signal.SIGINT, lambda s, f: globals().update(running=False))

while running:
    msg = consumer.poll(timeout=1.0)
    if msg is None:
        continue
    if msg.error():
        print(f"Error: {msg.error()}")
        continue

    # Process the message
    print(f"Processing partition={msg.partition()} offset={msg.offset()}")

    # Track the last message per partition — will be committed in on_revoke
    last_processed[msg.partition()] = msg

    # For normal (non-revoke) operation, commit periodically
    # Here we commit every message for simplicity
    consumer.commit(msg, asynchronous=True)

consumer.close()
print("Consumer shut down cleanly.")
```

### Cleaning up local state in on_revoke

If your consumer maintains local state per partition (e.g. an in-memory cache,
a running count, an open file), you must clean it up when partitions are revoked:

```python
from confluent_kafka import Consumer

# Example: per-partition running totals
partition_totals = {}  # {partition_id: float}

def on_assign(consumer, partitions):
    for p in partitions:
        partition_totals[p.partition] = 0.0
        print(f"Initialized state for partition {p.partition}")

def on_revoke(consumer, partitions):
    consumer.commit(asynchronous=False)
    for p in partitions:
        total = partition_totals.pop(p.partition, 0)
        print(f"Partition {p.partition} revoked — final total was {total:.2f}")
        # Flush to a database, write to a file, etc. before returning

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "stateful-consumer-group",
    "auto.offset.reset": "earliest",
    "enable.auto.commit": False,
})
consumer.subscribe(["group-demo"], on_assign=on_assign, on_revoke=on_revoke)

empty_polls = 0
while empty_polls < 5:
    msg = consumer.poll(timeout=2.0)
    if msg is None:
        empty_polls += 1
        continue
    empty_polls = 0
    if msg.error():
        continue

    # Simulate: each message contains a numeric value
    try:
        value = float(msg.value().decode())
        partition_totals[msg.partition()] = partition_totals.get(msg.partition(), 0) + value
    except ValueError:
        pass  # non-numeric message — ignore

    consumer.commit(msg, asynchronous=True)

consumer.close()
```

### Common mistakes with rebalance listeners

```python
# MISTAKE 1 — async commit in on_revoke
# The callback returns before the commit completes.
# Another consumer may be assigned this partition before the commit lands.
def on_revoke_wrong(consumer, partitions):
    consumer.commit(asynchronous=True)   # ← WRONG in on_revoke

# CORRECT — use synchronous commit in on_revoke
def on_revoke_correct(consumer, partitions):
    consumer.commit(asynchronous=False)  # ← blocks until confirmed

# MISTAKE 2 — doing nothing in on_revoke with manual commits
# If you use enable.auto.commit=False and never commit in on_revoke,
# the new owner of the partition will re-read from the last committed offset,
# potentially duplicating a significant amount of work.
def on_revoke_empty(consumer, partitions):
    pass   # ← WRONG if enable.auto.commit=False

# MISTAKE 3 — raising an exception in on_revoke
# Exceptions inside callbacks are silently swallowed by the client library.
# Always wrap your on_revoke body in a try/except and log errors.
def on_revoke_safe(consumer, partitions):
    try:
        consumer.commit(asynchronous=False)
    except Exception as e:
        print(f"Failed to commit during revoke: {e}")
        # Log and continue — do not re-raise
```

---

## 8.5 Consumer Group Lag

### What is consumer lag?

**Consumer lag** is the number of messages that exist in a partition but have not yet
been processed by a consumer group. It is calculated per partition as:

```
lag = log_end_offset - committed_offset

log_end_offset  — the offset of the next message the producer will write
committed_offset — the offset the consumer group has confirmed processing up to

Example:
  Partition 0: log_end_offset=100, committed_offset=95  → lag=5
  Partition 1: log_end_offset=200, committed_offset=200 → lag=0
  Partition 2: log_end_offset=150, committed_offset=120 → lag=30

  Total group lag: 5 + 0 + 30 = 35 messages behind
```

Lag is the most important operational metric for a Kafka consumer. Growing lag means
your consumers are falling behind the rate of production — a leading indicator that
something is wrong before it becomes a user-facing problem.

### Monitoring lag with CLI

```bash
# Show lag for all partitions of a group
docker exec kafka /opt/kafka/bin/kafka-consumer-groups.sh \
  --describe \
  --group my-consumer-group \
  --bootstrap-server localhost:9092
```

Output:
```
GROUP             TOPIC       PARTITION  CURRENT-OFFSET  LOG-END-OFFSET  LAG
my-consumer-group group-demo  0          45              50              5
my-consumer-group group-demo  1          50              50              0
my-consumer-group group-demo  2          30              60              30
my-consumer-group group-demo  3          60              60              0
my-consumer-group group-demo  4          58              60              2
my-consumer-group group-demo  5          60              60              0
```

```bash
# Show lag for ALL groups at once
docker exec kafka /opt/kafka/bin/kafka-consumer-groups.sh \
  --describe \
  --all-groups \
  --bootstrap-server localhost:9092

# Watch lag in real time (refresh every 2 seconds)
watch -n 2 "docker exec kafka /opt/kafka/bin/kafka-consumer-groups.sh \
  --describe --group my-consumer-group --bootstrap-server localhost:9092"
```

### Programmatic lag monitoring from Python

```python
from confluent_kafka import Consumer, TopicPartition
from confluent_kafka.admin import AdminClient

def get_consumer_group_lag(bootstrap_servers: str, group_id: str, topic: str) -> dict:
    """
    Returns lag per partition for a consumer group on a given topic.
    Returns a dict of {partition_id: lag}.
    """
    # Use a temporary consumer to query committed offsets
    consumer = Consumer({
        "bootstrap.servers": bootstrap_servers,
        "group.id": group_id,
    })

    # Get metadata to find out how many partitions the topic has
    metadata = consumer.list_topics(topic, timeout=10)
    if topic not in metadata.topics:
        consumer.close()
        raise ValueError(f"Topic '{topic}' not found")

    partitions = [
        TopicPartition(topic, pid)
        for pid in metadata.topics[topic].partitions
    ]

    # Get the committed offset for each partition in this group
    committed = consumer.committed(partitions, timeout=10)

    # Get the high watermark (log end offset) for each partition
    lag_per_partition = {}
    for tp in committed:
        low, high = consumer.get_watermark_offsets(tp, timeout=5)
        committed_offset = tp.offset if tp.offset >= 0 else low
        lag = max(0, high - committed_offset)
        lag_per_partition[tp.partition] = {
            "committed_offset": committed_offset,
            "log_end_offset": high,
            "lag": lag,
        }

    consumer.close()
    return lag_per_partition


# --- Usage ---
lag = get_consumer_group_lag(
    bootstrap_servers="localhost:9092",
    group_id="my-consumer-group",
    topic="group-demo",
)

total_lag = 0
print(f"{'Partition':<12} {'Committed':<12} {'End':<12} {'Lag':<8}")
print("-" * 48)
for partition, info in sorted(lag.items()):
    print(
        f"{partition:<12} "
        f"{info['committed_offset']:<12} "
        f"{info['log_end_offset']:<12} "
        f"{info['lag']:<8}"
    )
    total_lag += info["lag"]

print("-" * 48)
print(f"{'Total lag:':<36} {total_lag}")
```

### Building a lag monitor that runs continuously

```python
import time
from confluent_kafka import Consumer, TopicPartition

def monitor_lag(bootstrap_servers: str, group_id: str, topic: str, interval_seconds: int = 5):
    """Poll and print lag every `interval_seconds` seconds."""

    consumer = Consumer({
        "bootstrap.servers": bootstrap_servers,
        "group.id": group_id,
    })

    metadata = consumer.list_topics(topic, timeout=10)
    partitions = [TopicPartition(topic, pid) for pid in metadata.topics[topic].partitions]

    print(f"Monitoring lag for group '{group_id}' on topic '{topic}'")
    print(f"Refreshing every {interval_seconds}s — Ctrl+C to stop\n")

    try:
        while True:
            committed = consumer.committed(partitions, timeout=10)
            total_lag = 0

            for tp in committed:
                _, high = consumer.get_watermark_offsets(tp, timeout=5)
                committed_offset = tp.offset if tp.offset >= 0 else 0
                lag = max(0, high - committed_offset)
                total_lag += lag

            status = "OK" if total_lag == 0 else ("WARNING" if total_lag < 1000 else "CRITICAL")
            print(f"[{time.strftime('%H:%M:%S')}] Total lag: {total_lag:6d}  [{status}]")

            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        print("\nMonitor stopped.")
    finally:
        consumer.close()

monitor_lag("localhost:9092", "my-consumer-group", "group-demo", interval_seconds=3)
```

### Resetting offsets

Sometimes you need to move a consumer group's offsets — for replaying events after a
bug fix or skipping over a corrupted batch.

```bash
# Reset to the beginning of the topic (re-read all messages)
docker exec kafka /opt/kafka/bin/kafka-consumer-groups.sh \
  --reset-offsets \
  --group my-consumer-group \
  --topic group-demo \
  --to-earliest \
  --execute \
  --bootstrap-server localhost:9092

# Reset to the end (skip all existing messages, only read new ones)
docker exec kafka /opt/kafka/bin/kafka-consumer-groups.sh \
  --reset-offsets \
  --group my-consumer-group \
  --topic group-demo \
  --to-latest \
  --execute \
  --bootstrap-server localhost:9092

# Reset to a specific offset on a specific partition
docker exec kafka /opt/kafka/bin/kafka-consumer-groups.sh \
  --reset-offsets \
  --group my-consumer-group \
  --topic group-demo:2 \
  --to-offset 50 \
  --execute \
  --bootstrap-server localhost:9092

# Reset to a specific point in time (ISO 8601 format)
docker exec kafka /opt/kafka/bin/kafka-consumer-groups.sh \
  --reset-offsets \
  --group my-consumer-group \
  --topic group-demo \
  --to-datetime 2026-01-01T00:00:00.000 \
  --execute \
  --bootstrap-server localhost:9092
```

> **Important:** The consumer group must be inactive (no running consumers) when
> resetting offsets. Stop all consumers in the group first, reset, then restart them.

### Resetting offsets from Python

```python
from confluent_kafka.admin import AdminClient
from confluent_kafka import TopicPartition, OFFSET_BEGINNING, OFFSET_END

admin = AdminClient({"bootstrap.servers": "localhost:9092"})

# Alter committed offsets for a group
# This requires the group to be empty (no active consumers)
topic = "group-demo"
group = "my-consumer-group"

# Reset all 6 partitions to the beginning
offsets = [TopicPartition(topic, p, OFFSET_BEGINNING) for p in range(6)]

future = admin.alter_consumer_group_offsets([
    # ConsumerGroupTopicPartitions object
    # requires confluent-kafka >= 1.9
])
# Note: for older versions use the CLI approach above or seek() inside the consumer
```

---

## Summary — Quick Reference

| Concept | Key points |
|---|---|
| **Consumer group** | Set of consumers sharing work; each partition consumed by exactly one member |
| **Parallelism ceiling** | Max useful consumers = number of partitions; extras are idle |
| **Multiple groups** | Each group reads the topic independently with its own offsets |
| **Rebalance triggers** | Consumer joins, leaves, crashes, exceeds `max.poll.interval.ms`, partition count changes |
| **Eager rebalance** | All consumers stop and drop all partitions — stop-the-world |
| **Cooperative rebalance** | Only moved partitions are revoked — group keeps processing |
| **Recommended assignor** | `cooperative-sticky` — minimises disruption and partition movement |
| **`group.instance.id`** | Stable identity; prevents rebalance on consumer restart within `session.timeout.ms` |
| **`on_revoke`** | Commit offsets **synchronously** before returning — last chance before another consumer takes over |
| **`on_assign`** | Restore state or seek to custom offset after receiving new partitions |
| **Never async commit in `on_revoke`** | Commit may not land before the next consumer starts reading |
| **Lag** | `log_end_offset - committed_offset`; the most critical consumer health metric |
| **Lag = 0** | Consumer is fully caught up |
| **Growing lag** | Consumers are slower than producers — scale up or optimise processing |
| **Reset offsets** | Group must be inactive; use CLI or AdminClient; specify earliest/latest/offset/datetime |
