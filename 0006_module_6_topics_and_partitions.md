# Module 6 — Topics and Partitions In Depth

This module is more conceptual than Modules 4 and 5, but every concept is grounded
with CLI commands you can run against your local Docker cluster and Python code using
the Kafka `AdminClient`. By the end you will be able to make informed decisions about
topic design, partition count, replication, and data retention.

**Prerequisites:**
- Kafka is running locally via Docker (see Module 2)
- `confluent-kafka` is installed: `pip install confluent-kafka`
- The multi-broker setup (section 2.6) is recommended for replication examples.
  Single-broker is fine for all other sections.

---

## 6.1 Topic Design

### What is a topic?

A topic is a named, append-only log. Producers write to it; consumers read from it.
Think of it as a table in a database, but instead of rows you have an ordered stream
of events, and instead of deleting rows when they are read, the data stays until a
retention policy removes it.

```
Topic: "user-events"
  |
  +-- Partition 0: [signup] [login] [purchase] [logout] ...
  |
  +-- Partition 1: [login] [profile-update] [login] ...
  |
  +-- Partition 2: [signup] [purchase] [purchase] ...
```

### Naming conventions

There is no enforced naming standard, but the following conventions are widely used
in production and help teams understand what a topic contains at a glance:

| Pattern | Example | Notes |
|---|---|---|
| `<domain>.<entity>.<event>` | `payments.invoice.created` | Best for event-driven systems |
| `<team>.<service>.<entity>` | `checkout.cart.items` | Good for team ownership clarity |
| `<env>.<domain>.<event>` | `prod.orders.shipped` | Useful when sharing a cluster across environments |

**Rules to follow:**
- Use lowercase letters, numbers, hyphens (`-`) or dots (`.`) — avoid underscores
  (they cause issues with some metrics systems that also use dots as separators)
- Be specific — `user-events` is too broad; `user.profile.updated` is clear
- Include the environment prefix if dev/staging/prod share the same cluster
- Avoid encoding partition count or schema version in the topic name — these change

```bash
# Creating topics with well-structured names
docker exec kafka /opt/kafka/bin/kafka-topics.sh --create \
  --topic user.profile.updated \
  --bootstrap-server localhost:9092 \
  --partitions 6 \
  --replication-factor 1

docker exec kafka /opt/kafka/bin/kafka-topics.sh --create \
  --topic payments.invoice.created \
  --bootstrap-server localhost:9092 \
  --partitions 3 \
  --replication-factor 1
```

### How many topics do you need?

This is one of the most common design questions. The two extreme approaches are:

**One topic per event type** (recommended for most cases)

```
user.registered
user.profile.updated
user.deleted
order.placed
order.shipped
order.cancelled
payment.completed
payment.refunded
```

Advantages:
- Each topic has a clear, single contract — producers and consumers are decoupled
- Independent retention policies per event type
- Easy to add a new consumer for one event type without affecting others
- Independent scaling (partition count) per event type

**One broad topic with a type field in the payload** (avoid unless justified)

```
all-events  →  {"type": "user.registered", "data": {...}}
all-events  →  {"type": "order.placed", "data": {...}}
```

Problems:
- Every consumer reads all event types and must filter — wasteful
- One fast event type can starve slower ones in the same partitions
- Retention is coupled — you cannot keep orders longer than user events
- Adding a new event type requires all existing consumers to update their filter logic

**Exception — when a broader topic makes sense:**
- Audit logs that must capture every event type in chronological order
- CDC (change data capture) streams from a single database table
- When the consuming service genuinely needs multiple event types together and
  ordering across them matters

### Topic as a contract between services

A Kafka topic is a public API. Any service that produces to or consumes from a topic
is depending on its schema and semantics. Treat topic design with the same care as
REST API design:

- **Do not change the meaning of an existing field** — add new fields instead
- **Do not rename fields** — it is a breaking change for all consumers
- **Document your topics** — which service owns them, what events they contain,
  what the schema is (covered in Module 12)
- **Think before creating** — a topic that is in production is hard to delete or
  rename because all consumers must be updated simultaneously

---

## 6.2 Partition Count

### What is a partition?

A topic is split into one or more **partitions**. Each partition is an independent,
ordered log stored on one broker. Partitions are the unit of parallelism in Kafka.

```
Topic "orders" with 3 partitions:

Producer A  -->  partition 0  -->  Consumer Group Member 1
Producer B  -->  partition 1  -->  Consumer Group Member 2
Producer C  -->  partition 2  -->  Consumer Group Member 3
```

### Relationship between partitions and consumer parallelism

This is the single most important thing to understand about partitions:

**The maximum number of active consumers in a group = the number of partitions.**

```
Topic: 3 partitions

Scenario 1 — 1 consumer in the group:
  Consumer A reads partitions 0, 1, 2  (all three)

Scenario 2 — 2 consumers in the group:
  Consumer A reads partitions 0, 1
  Consumer B reads partition 2

Scenario 3 — 3 consumers in the group:
  Consumer A reads partition 0
  Consumer B reads partition 1
  Consumer C reads partition 2

Scenario 4 — 4 consumers in the group (one is idle):
  Consumer A reads partition 0
  Consumer B reads partition 1
  Consumer C reads partition 2
  Consumer D reads NOTHING  ← idle, wasted resource
```

**Implication:** If you want to scale to N parallel consumers, you need at least N
partitions. Adding more consumers than partitions does nothing.

### Observing this with CLI

Create a topic with 3 partitions and describe it:

```bash
docker exec kafka /opt/kafka/bin/kafka-topics.sh --create \
  --topic partition-demo \
  --bootstrap-server localhost:9092 \
  --partitions 3 \
  --replication-factor 1

docker exec kafka /opt/kafka/bin/kafka-topics.sh --describe \
  --topic partition-demo \
  --bootstrap-server localhost:9092
```

Output:
```
Topic: partition-demo  PartitionCount: 3  ReplicationFactor: 1
  Partition: 0  Leader: 1  Replicas: 1  Isr: 1
  Partition: 1  Leader: 1  Replicas: 1  Isr: 1
  Partition: 2  Leader: 1  Replicas: 1  Isr: 1
```

### Observing partition assignment with Python

```python
from confluent_kafka import Consumer, TopicPartition
import threading
import time

# Start 3 consumers in the same group and observe how partitions are distributed
def run_consumer(consumer_id):
    consumer = Consumer({
        "bootstrap.servers": "localhost:9092",
        "group.id": "partition-demo-group",
        "auto.offset.reset": "latest",
    })
    consumer.subscribe(["partition-demo"])

    # Poll once to trigger rebalance and get partition assignment
    consumer.poll(timeout=5.0)
    assignment = consumer.assignment()
    print(f"Consumer {consumer_id} assigned partitions: {[p.partition for p in assignment]}")

    time.sleep(5)  # hold the assignment so we can observe it
    consumer.close()

# Launch 3 consumers in parallel threads
threads = [threading.Thread(target=run_consumer, args=(i,)) for i in range(3)]
for t in threads:
    t.start()
for t in threads:
    t.join()
```

Expected output (partition numbers may differ):
```
Consumer 0 assigned partitions: [0]
Consumer 1 assigned partitions: [1]
Consumer 2 assigned partitions: [2]
```

### Impact on ordering guarantees

Kafka only guarantees ordering **within a partition**. Across partitions there is no
global order.

```
Producer sends: event-A, event-B, event-C (all for user-123, all with key "user-123")

  All 3 messages hash to partition 1:
    Partition 1: [event-A @ offset 0] [event-B @ offset 1] [event-C @ offset 2]
    Consumer reads: A, then B, then C  ✓  ORDERED

  If sent WITHOUT a key (round-robin):
    Partition 0: [event-A @ offset 0]
    Partition 1: [event-B @ offset 0]
    Partition 2: [event-C @ offset 0]
    Consumer may read: B, then A, then C  ✗  NO ORDER GUARANTEE
```

**Rule:** If ordering matters between related events, use a consistent key. All events
for the same entity (same user, same order, same device) will land on the same
partition and be consumed in order.

### How to choose the number of partitions

There is no universal answer, but these guidelines help:

| Factor | Guidance |
|---|---|
| **Desired consumer parallelism** | Set partitions ≥ maximum number of consumers you plan to run |
| **Throughput target** | Each partition can typically handle 10–50 MB/s. Divide your target throughput by that to get minimum partitions needed |
| **Ordering requirements** | Fewer partitions → easier to maintain ordering. More partitions → harder |
| **Starting point** | For most applications, start with 3–12 partitions. You can always increase later (but not decrease) |

### Repartitioning pitfalls

You can increase the partition count of an existing topic:

```bash
docker exec kafka /opt/kafka/bin/kafka-topics.sh --alter \
  --topic partition-demo \
  --partitions 6 \
  --bootstrap-server localhost:9092
```

**However, there are two critical caveats:**

1. **Existing messages are NOT redistributed.** They stay in their original partitions.
   Only new messages are distributed across all 6 partitions.

2. **Key-to-partition mapping changes.** Before the change, key `"user-123"` may have
   mapped to partition 1. After increasing to 6 partitions, the same key maps to a
   different partition. Any consumer that was relying on partition-based ordering for
   that key will now see new messages on a different partition than old messages.

**You cannot decrease the partition count** of an existing topic. If you need fewer
partitions, you must create a new topic and migrate data.

```python
# Demonstrating the key-to-partition shift with Python
from confluent_kafka import Producer

def show_partition(num_partitions, key):
    """Simulate where murmur2 would send this key."""
    import struct

    def murmur2(data):
        # Simplified murmur2 hash (same algorithm Kafka uses)
        h = 0x9747b28c
        length = len(data)
        for i in range(0, length - 3, 4):
            k = struct.unpack_from("<I", data, i)[0]
            k *= 0xcc9e2d51
            k = ((k << 15) | (k >> 17)) & 0xFFFFFFFF
            k *= 0x1b873593
            h ^= k
            h = ((h << 13) | (h >> 19)) & 0xFFFFFFFF
            h = (h * 5 + 0xe6546b64) & 0xFFFFFFFF
        remaining = length & 3
        if remaining:
            k = 0
            for i in range(remaining - 1, -1, -1):
                k = (k << 8) | data[length - remaining + i]
            k *= 0xcc9e2d51
            k = ((k << 15) | (k >> 17)) & 0xFFFFFFFF
            k *= 0x1b873593
            h ^= k
        h ^= length
        h ^= h >> 16
        h *= 0x85ebca6b
        h ^= h >> 13
        h *= 0xc2b2ae35
        h ^= h >> 16
        return h & 0x7FFFFFFF

    return murmur2(key.encode()) % num_partitions

key = "user-123"
print(f"Key '{key}' with 3 partitions  → partition {show_partition(3, key)}")
print(f"Key '{key}' with 6 partitions  → partition {show_partition(6, key)}")
# The partition will be different — this breaks ordering for existing data
```

### Using AdminClient to manage topics from Python

```python
from confluent_kafka.admin import AdminClient, NewTopic, NewPartitions

admin = AdminClient({"bootstrap.servers": "localhost:9092"})

# --- Create a topic ---
new_topic = NewTopic(
    topic="python-created-topic",
    num_partitions=3,
    replication_factor=1,
    config={
        "retention.ms": "86400000",   # 1 day
        "cleanup.policy": "delete",
    },
)

futures = admin.create_topics([new_topic])
for topic, future in futures.items():
    try:
        future.result()  # blocks until done
        print(f"Topic '{topic}' created.")
    except Exception as e:
        print(f"Failed to create topic '{topic}': {e}")

# --- List all topics ---
metadata = admin.list_topics(timeout=10)
for topic_name in sorted(metadata.topics):
    topic = metadata.topics[topic_name]
    print(f"Topic: {topic_name}  partitions={len(topic.partitions)}")

# --- Increase partition count ---
futures = admin.create_partitions(
    {"python-created-topic": NewPartitions(total_count=6)}
)
for topic, future in futures.items():
    try:
        future.result()
        print(f"Partitions increased for '{topic}'.")
    except Exception as e:
        print(f"Failed: {e}")

# --- Delete a topic ---
futures = admin.delete_topics(["python-created-topic"])
for topic, future in futures.items():
    try:
        future.result()
        print(f"Topic '{topic}' deleted.")
    except Exception as e:
        print(f"Failed to delete '{topic}': {e}")
```

---

## 6.3 Replication

### What is replication?

Replication is Kafka's mechanism for fault tolerance. When a topic has a replication
factor > 1, each partition is copied to multiple brokers. If one broker fails, another
broker already has a complete copy and can take over immediately.

```
Topic "orders", replication-factor=3, 3 brokers:

Partition 0:  Leader on Broker 1  |  Follower on Broker 2  |  Follower on Broker 3
Partition 1:  Leader on Broker 2  |  Follower on Broker 3  |  Follower on Broker 1
Partition 2:  Leader on Broker 3  |  Follower on Broker 1  |  Follower on Broker 2
```

Producers and consumers always talk to the **leader** of a partition. Followers
silently replicate from the leader in the background. If a leader broker goes down,
Kafka elects a new leader from the followers.

### In-Sync Replicas (ISR)

The **ISR** (In-Sync Replicas) is the set of replicas that are fully caught up with
the leader. A replica is removed from the ISR if it falls too far behind
(`replica.lag.time.max.ms`).

```
Healthy cluster:
  Partition 0: Leader=Broker1  Replicas=1,2,3  ISR=1,2,3

Broker 2 goes down:
  Partition 0: Leader=Broker1  Replicas=1,2,3  ISR=1,3  ← broker 2 removed from ISR

Broker 2 comes back and catches up:
  Partition 0: Leader=Broker1  Replicas=1,2,3  ISR=1,2,3  ← broker 2 rejoins ISR
```

### Observing replication with CLI (requires multi-broker setup)

```bash
# Create a topic with replication factor 3
docker exec kafka-1 /opt/kafka/bin/kafka-topics.sh --create \
  --topic replicated-demo \
  --bootstrap-server kafka-1:9094 \
  --partitions 3 \
  --replication-factor 3

# Describe — observe Leader, Replicas, and Isr columns
docker exec kafka-1 /opt/kafka/bin/kafka-topics.sh --describe \
  --topic replicated-demo \
  --bootstrap-server kafka-1:9094
```

Output:
```
Topic: replicated-demo  PartitionCount: 3  ReplicationFactor: 3
  Partition: 0  Leader: 1  Replicas: 1,2,3  Isr: 1,2,3
  Partition: 1  Leader: 2  Replicas: 2,3,1  Isr: 2,3,1
  Partition: 2  Leader: 3  Replicas: 3,1,2  Isr: 3,1,2
```

```bash
# Stop broker 2 to simulate a failure
docker compose stop kafka-2

# Describe again — ISR shrinks, leader re-elected where needed
docker exec kafka-1 /opt/kafka/bin/kafka-topics.sh --describe \
  --topic replicated-demo \
  --bootstrap-server kafka-1:9094
```

Output after failure:
```
Topic: replicated-demo  PartitionCount: 3  ReplicationFactor: 3
  Partition: 0  Leader: 1  Replicas: 1,2,3  Isr: 1,3     ← broker 2 gone from ISR
  Partition: 1  Leader: 3  Replicas: 2,3,1  Isr: 3,1     ← new leader elected (was 2)
  Partition: 2  Leader: 3  Replicas: 3,1,2  Isr: 3,1
```

```bash
# Bring broker 2 back
docker compose start kafka-2

# Describe again after ~30 seconds — broker 2 rejoins ISR
docker exec kafka-1 /opt/kafka/bin/kafka-topics.sh --describe \
  --topic replicated-demo \
  --bootstrap-server kafka-1:9094
```

### min.insync.replicas — the safety floor

`min.insync.replicas` (set on the topic or broker) defines the minimum number of
replicas that must acknowledge a write before the producer receives success.

It only takes effect when `acks=all` on the producer.

```
replication-factor=3, min.insync.replicas=2, acks=all:

All 3 brokers up:     write succeeds (3 replicas ack'd ≥ 2 required)
1 broker down:        write succeeds (2 replicas ack'd = 2 required)
2 brokers down:       write FAILS    (1 replica ack'd < 2 required)
                      → NotEnoughReplicasException
```

```bash
# Set min.insync.replicas when creating a topic
docker exec kafka-1 /opt/kafka/bin/kafka-topics.sh --create \
  --topic safe-writes \
  --bootstrap-server kafka-1:9094 \
  --partitions 3 \
  --replication-factor 3 \
  --config min.insync.replicas=2
```

```python
# Creating a topic with min.insync.replicas from Python
from confluent_kafka.admin import AdminClient, NewTopic

admin = AdminClient({"bootstrap.servers": "localhost:9092"})

topic = NewTopic(
    topic="safe-writes-python",
    num_partitions=3,
    replication_factor=3,
    config={
        "min.insync.replicas": "2",
    },
)
futures = admin.create_topics([topic])
for t, f in futures.items():
    try:
        f.result()
        print(f"Created '{t}'")
    except Exception as e:
        print(f"Failed: {e}")
```

### Inspecting replication from Python

```python
from confluent_kafka.admin import AdminClient

admin = AdminClient({"bootstrap.servers": "localhost:9092"})

metadata = admin.list_topics(timeout=10)

for topic_name, topic_meta in sorted(metadata.topics.items()):
    if topic_name.startswith("__"):
        continue  # skip internal topics
    print(f"\nTopic: {topic_name}")
    for pid, partition in sorted(topic_meta.partitions.items()):
        leader = partition.leader
        replicas = partition.replicas
        isrs = partition.isrs
        in_sync = len(isrs) == len(replicas)
        status = "OK" if in_sync else "UNDER-REPLICATED"
        print(
            f"  Partition {pid}: leader={leader}"
            f"  replicas={replicas}"
            f"  isr={isrs}"
            f"  [{status}]"
        )
```

### Choosing the right replication factor

| Replication factor | Brokers needed | Fault tolerance | Use case |
|---|---|---|---|
| 1 | 1+ | None — lose 1 broker, lose data | Local development only |
| 2 | 2+ | 1 broker failure | Rarely used — no majority for quorum |
| 3 | 3+ | 1 broker failure (with min.insync.replicas=2) | Standard production |
| 5 | 5+ | 2 broker failures | Critical data requiring highest durability |

---

## 6.4 Retention Policies

Kafka retains messages for a configurable period — this is fundamentally different
from traditional message queues that delete messages after they are consumed. Because
data is retained, any consumer group can re-read the full history at any time.

### Time-based retention (retention.ms)

Deletes messages older than the specified duration. The default is 7 days (604800000 ms).

```bash
# Create a topic with 1-hour retention
docker exec kafka /opt/kafka/bin/kafka-topics.sh --create \
  --topic short-lived-events \
  --bootstrap-server localhost:9092 \
  --partitions 3 \
  --replication-factor 1 \
  --config retention.ms=3600000

# Alter retention on an existing topic (change to 24 hours)
docker exec kafka /opt/kafka/bin/kafka-configs.sh --alter \
  --entity-type topics \
  --entity-name short-lived-events \
  --add-config retention.ms=86400000 \
  --bootstrap-server localhost:9092

# Check current retention setting
docker exec kafka /opt/kafka/bin/kafka-configs.sh --describe \
  --entity-type topics \
  --entity-name short-lived-events \
  --bootstrap-server localhost:9092
```

```python
# Setting retention from Python AdminClient
from confluent_kafka.admin import AdminClient, ConfigResource, ConfigEntry
from confluent_kafka.admin import CONFIG_SOURCE_DYNAMIC_TOPIC_CONFIG

admin = AdminClient({"bootstrap.servers": "localhost:9092"})

# ConfigResource identifies which entity to configure (a topic in this case)
resource = ConfigResource("topic", "short-lived-events")

# AlterConfigs — set one or more config values
result = admin.alter_configs([resource])
# Note: to set values, pass config entries via describe+alter pattern
# For simplicity, use kafka-configs.sh CLI for ad-hoc changes
# and NewTopic(config={...}) when creating topics programmatically

# Describe current config for a topic
resources = [ConfigResource("topic", "short-lived-events")]
futures = admin.describe_configs(resources)
for resource, future in futures.items():
    config = future.result()
    # Only show non-default values
    for key, entry in sorted(config.items()):
        if entry.is_default:
            continue
        print(f"{key} = {entry.value}")
```

### Size-based retention (retention.bytes)

Deletes old log segments once the total size of a partition exceeds the limit.
This caps disk usage regardless of message age.

```bash
# Limit partition size to 1 GB
docker exec kafka /opt/kafka/bin/kafka-topics.sh --create \
  --topic size-limited-topic \
  --bootstrap-server localhost:9092 \
  --partitions 3 \
  --replication-factor 1 \
  --config retention.bytes=1073741824

# You can combine both time and size — whichever limit is hit first wins
docker exec kafka /opt/kafka/bin/kafka-topics.sh --create \
  --topic dual-limited-topic \
  --bootstrap-server localhost:9092 \
  --partitions 3 \
  --replication-factor 1 \
  --config retention.ms=86400000 \
  --config retention.bytes=536870912
```

### Compacted topics — keeping only the latest value per key

A **compacted topic** retains only the most recent message for each key. Older
messages with the same key are deleted during a background compaction process. This
is useful for maintaining the current state of an entity rather than its full history.

```
Before compaction:
  [user-1: "Alice"] [user-2: "Bob"] [user-1: "Alicia"] [user-3: "Carol"] [user-2: "Robert"]

After compaction:
  [user-1: "Alicia"] [user-3: "Carol"] [user-2: "Robert"]
  (only the latest value per key survives)
```

Use compacted topics for:
- **Configuration stores** — always want the latest config value per key
- **User profile snapshots** — latest profile per user ID
- **Database change capture** — latest state of each database row

```bash
# Create a compacted topic
docker exec kafka /opt/kafka/bin/kafka-topics.sh --create \
  --topic user-profiles \
  --bootstrap-server localhost:9092 \
  --partitions 3 \
  --replication-factor 1 \
  --config cleanup.policy=compact \
  --config min.cleanable.dirty.ratio=0.01 \
  --config segment.ms=10000
```

```python
# Producing to a compacted topic — keys are mandatory
from confluent_kafka import Producer

producer = Producer({"bootstrap.servers": "localhost:9092"})

# Write initial values
for user_id in range(1, 4):
    producer.produce(
        "user-profiles",
        key=f"user-{user_id}".encode(),
        value=f'{{"name": "User {user_id}", "version": 1}}'.encode(),
    )
producer.flush()
print("Initial profiles written.")

# Update user-2 — after compaction, only this version survives for user-2
producer.produce(
    "user-profiles",
    key=b"user-2",
    value=b'{"name": "Robert", "version": 2}',
)
producer.flush()
print("user-2 profile updated.")
```

### Tombstones — deleting a key from a compacted topic

To delete a key from a compacted topic, produce a message with `value=None` (a
**tombstone**). After compaction, both the tombstone and all previous messages for
that key are removed.

```python
from confluent_kafka import Producer

producer = Producer({"bootstrap.servers": "localhost:9092"})

# Sending a tombstone — value must be None (not b"" or b"null")
producer.produce(
    "user-profiles",
    key=b"user-3",
    value=None,   # tombstone — signals "delete this key"
)
producer.flush()
print("Tombstone sent for user-3. After compaction, user-3 will be removed.")
```

### Infinite retention

For event sourcing or audit logs where you never want data to expire:

```bash
docker exec kafka /opt/kafka/bin/kafka-topics.sh --create \
  --topic audit-log \
  --bootstrap-server localhost:9092 \
  --partitions 6 \
  --replication-factor 1 \
  --config retention.ms=-1 \
  --config retention.bytes=-1
```

> **Warning:** Infinite retention requires careful disk capacity planning. Use
> size-based retention (`retention.bytes`) as a safety net even on "infinite"
> topics, or ensure your storage scales automatically.

---

## 6.5 Log Segments

### How Kafka stores data on disk

Each partition is stored on disk as a series of **segment files**. A segment is a
chunk of the partition's log. Kafka never overwrites or modifies existing segments —
it only appends to the active (latest) segment.

```
/kafka-logs/my-first-topic-0/          ← partition 0 directory
  00000000000000000000.log             ← segment: messages at offsets 0–999
  00000000000000000000.index           ← offset index for fast lookup
  00000000000000000000.timeindex       ← timestamp index
  00000000000000001000.log             ← segment: messages at offsets 1000–1999
  00000000000000001000.index
  00000000000000001000.timeindex
  00000000000000002000.log             ← active segment (currently being written)
  00000000000000002000.index
  00000000000000002000.timeindex
```

The filename is the **base offset** of the segment — the offset of the first message
in that file.

### The .log, .index, and .timeindex files

| File | Purpose |
|---|---|
| `.log` | The actual message data — appended sequentially |
| `.index` | Maps offsets to byte positions in the `.log` file — allows O(1) seek |
| `.timeindex` | Maps timestamps to offsets — enables time-based queries (`offsetsForTimes`) |

### When does a new segment start?

Kafka rolls over to a new segment when either:
- The active segment reaches `segment.bytes` (default: 1 GB)
- The active segment has been open for `segment.ms` (default: 7 days)

You can tune these for your workload:

```bash
# Smaller segments — more frequent cleanup, faster compaction for compacted topics
docker exec kafka /opt/kafka/bin/kafka-topics.sh --create \
  --topic fast-rolling-topic \
  --bootstrap-server localhost:9092 \
  --partitions 3 \
  --replication-factor 1 \
  --config segment.bytes=52428800 \
  --config segment.ms=3600000
```

### Segment cleanup — how retention works in practice

Kafka does not delete individual messages. It deletes **entire segments** once they
are eligible:

```
Retention policy: delete messages older than 1 hour

Segment A (offsets 0–999):    written 3 hours ago  → ELIGIBLE for deletion
Segment B (offsets 1000–1999): written 2 hours ago → ELIGIBLE for deletion
Segment C (offsets 2000–2999): written 30 minutes ago → KEPT (too recent)
Active segment (offsets 3000+): currently being written → ALWAYS KEPT
```

Kafka only deletes a segment when ALL messages in it are past the retention threshold.
This means the actual retention may be slightly longer than `retention.ms` for the
last few messages of an eligible segment.

### Inspecting log segments inside Docker

```bash
# Shell into the Kafka container
docker exec -it kafka bash

# List segment files for partition 0 of my-first-topic
ls -lh /tmp/kraft-combined-logs/my-first-topic-0/

# Use the DumpLogSegments tool to inspect raw message content
/opt/kafka/bin/kafka-dump-log.sh \
  --files /tmp/kraft-combined-logs/my-first-topic-0/00000000000000000000.log \
  --print-data-log
```

Example output from DumpLogSegments:
```
Dumping /tmp/kraft-combined-logs/my-first-topic-0/00000000000000000000.log
Starting offset: 0
baseOffset: 0 lastOffset: 0 count: 1 ... magic: 2 ... isTransactional: false ...
| offset: 0 CreateTime: 1741200000000 keySize: -1 valueSize: 13 ... payload: Hello, Kafka!
| offset: 1 CreateTime: 1741200001000 keySize: 7 valueSize: 5 ... key: user-1 payload: Alice
```

### Querying by timestamp from Python

The `.timeindex` file makes it possible to find the offset of the first message
written at or after a given timestamp — useful for replaying events from a specific
point in time:

```python
from confluent_kafka import Consumer, TopicPartition
import datetime
import time

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "timestamp-query-demo",
    "enable.auto.commit": False,
})

topic = "my-first-topic"

# Find the offset of the first message written after midnight yesterday
yesterday_midnight = datetime.datetime.combine(
    datetime.date.today() - datetime.timedelta(days=1),
    datetime.time.min,
    tzinfo=datetime.timezone.utc,
)
timestamp_ms = int(yesterday_midnight.timestamp() * 1000)

# Build TopicPartition objects with the target timestamp
partitions = [
    TopicPartition(topic, p, timestamp_ms)
    for p in range(3)  # 3 partitions
]

# offsets_for_times() returns the offset of the first message at or after
# the given timestamp for each partition
offsets = consumer.offsets_for_times(partitions, timeout=10.0)

for tp in offsets:
    if tp.offset == -1:
        print(f"Partition {tp.partition}: no messages after the given timestamp")
    else:
        print(f"Partition {tp.partition}: first message at or after yesterday midnight is offset {tp.offset}")
        # Seek to that offset and start consuming
        consumer.assign([tp])

# Now consume from the found offsets
empty_polls = 0
while empty_polls < 3:
    msg = consumer.poll(timeout=2.0)
    if msg is None:
        empty_polls += 1
        continue
    empty_polls = 0
    if not msg.error():
        ts_ms = msg.timestamp()[1]
        ts_human = datetime.datetime.fromtimestamp(ts_ms / 1000).isoformat()
        print(f"[{ts_human}] partition={msg.partition()} offset={msg.offset()} value={msg.value().decode()}")

consumer.close()
```

---

## Summary — Quick Reference

| Topic | Key points |
|---|---|
| **Topic naming** | `domain.entity.event` — lowercase, dots or hyphens, no underscores |
| **One topic per event type** | Default choice — independent retention, scaling, and schema |
| **Broad topics** | Only when cross-event ordering is a hard requirement |
| **Partitions = parallelism** | Max active consumers in a group = partition count |
| **Ordering** | Guaranteed within a partition only — use keys for related events |
| **Increasing partitions** | Allowed, but existing data is not redistributed and key mapping shifts |
| **Decreasing partitions** | Not possible — create a new topic instead |
| **Replication factor** | 3 is the production standard; requires 3+ brokers |
| **ISR** | The set of replicas fully caught up with the leader |
| **min.insync.replicas** | Floor for writes with `acks=all` — prevents silent data loss |
| **retention.ms** | Delete messages older than N ms (default 7 days) |
| **retention.bytes** | Delete old segments when partition size exceeds limit |
| **Compacted topics** | Keep only the latest message per key — good for state/snapshots |
| **Tombstone** | Message with `value=None` — deletes a key from a compacted topic |
| **Log segments** | Kafka stores partitions as rolling `.log` files — never modifies, only appends |
| **segment.bytes / segment.ms** | Controls how often Kafka rolls a new segment file |
| **offsets_for_times()** | Find the offset of the first message at or after a timestamp |
