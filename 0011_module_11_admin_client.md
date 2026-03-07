# Module 11 — Admin Client and Topic Management

## Overview

The **AdminClient** lets you manage a Kafka cluster programmatically from Python: create and delete topics, inspect configuration, adjust partition counts, manage consumer group offsets, and more. This is the same functionality exposed by the CLI tools (`kafka-topics.sh`, `kafka-consumer-groups.sh`) but available inside your application code.

Common use cases:
- Bootstrap scripts that create required topics when a service starts
- CI/CD pipelines that provision test environments
- Monitoring scripts that inspect consumer lag or topic health
- Migration tools that reset offsets or repartition topics

```bash
pip install confluent-kafka
```

---

## 11.1 AdminClient Basics

### Creating the Client

```python
from confluent_kafka.admin import AdminClient

admin = AdminClient({
    "bootstrap.servers": "localhost:9092",
})
```

The AdminClient connects to the cluster through any broker. The broker gives it the full cluster metadata, and subsequent operations are routed to the appropriate broker automatically.

### Creating Topics

```python
from confluent_kafka.admin import AdminClient, NewTopic

admin = AdminClient({"bootstrap.servers": "localhost:9092"})

# Define topics to create
new_topics = [
    NewTopic(
        topic="orders",
        num_partitions=6,
        replication_factor=1,
        config={
            "retention.ms": str(7 * 24 * 60 * 60 * 1000),  # 7 days in ms
            "cleanup.policy": "delete",
        },
    ),
    NewTopic(
        topic="user-events",
        num_partitions=12,
        replication_factor=1,
        config={
            "retention.ms": str(30 * 24 * 60 * 60 * 1000),  # 30 days
        },
    ),
    NewTopic(
        topic="user-profiles",
        num_partitions=6,
        replication_factor=1,
        config={
            "cleanup.policy": "compact",  # keep only the latest value per key
        },
    ),
]

# create_topics() returns a dict of {topic_name: Future}
futures = admin.create_topics(new_topics)

for topic_name, future in futures.items():
    try:
        future.result()  # blocks until the operation completes
        print(f"Topic '{topic_name}' created successfully.")
    except Exception as e:
        print(f"Failed to create topic '{topic_name}': {e}")
```

> `future.result()` will raise a `KafkaException` with `TOPIC_ALREADY_EXISTS` if the topic already exists. If your script needs to be idempotent (safe to run multiple times), catch that error:

```python
from confluent_kafka.admin import AdminClient, NewTopic
from confluent_kafka import KafkaException
import confluent_kafka

def ensure_topic_exists(admin: AdminClient, topic: str, partitions: int, replication: int):
    """Create topic if it does not already exist. Idempotent."""
    future = admin.create_topics([NewTopic(topic, partitions, replication)])
    try:
        future[topic].result()
        print(f"Created topic: {topic}")
    except KafkaException as e:
        if e.args[0].code() == confluent_kafka.KafkaError.TOPIC_ALREADY_EXISTS:
            print(f"Topic already exists: {topic}")
        else:
            raise

admin = AdminClient({"bootstrap.servers": "localhost:9092"})
ensure_topic_exists(admin, "orders", partitions=6, replication=1)
ensure_topic_exists(admin, "orders", partitions=6, replication=1)  # safe to call again
```

### Listing Topics

```python
from confluent_kafka.admin import AdminClient

admin = AdminClient({"bootstrap.servers": "localhost:9092"})

# list_topics() returns a ClusterMetadata object
metadata = admin.list_topics(timeout=10)

print(f"Cluster ID: {metadata.cluster_id}")
print(f"Controller broker: {metadata.controller_id}")
print()

# Filter out internal Kafka topics (those starting with __) for a clean list
user_topics = {
    name: topic
    for name, topic in metadata.topics.items()
    if not name.startswith("__")
}

print(f"Topics ({len(user_topics)} total):")
for name, topic in sorted(user_topics.items()):
    print(f"  {name}")
    for partition_id, partition in topic.partitions.items():
        print(f"    Partition {partition_id}: leader={partition.leader}, replicas={partition.replicas}, isrs={partition.isrs}")
```

Example output:
```
Cluster ID: MkU3OEVBNTcwNTJENDM2Qk
Controller broker: 1

Topics (3 total):
  orders
    Partition 0: leader=1, replicas=[1], isrs=[1]
    Partition 1: leader=1, replicas=[1], isrs=[1]
    ...
  user-events
    ...
  user-profiles
    ...
```

### Deleting Topics

```python
from confluent_kafka.admin import AdminClient

admin = AdminClient({"bootstrap.servers": "localhost:9092"})

topics_to_delete = ["test-topic", "temp-data"]
futures = admin.delete_topics(topics_to_delete, operation_timeout=30)

for topic_name, future in futures.items():
    try:
        future.result()
        print(f"Deleted topic: {topic_name}")
    except Exception as e:
        print(f"Failed to delete topic '{topic_name}': {e}")
```

> **Warning:** Topic deletion in Kafka is asynchronous. The topic is marked for deletion, and the actual data is removed in the background. Do not immediately re-create a topic with the same name — allow a few seconds.

### Describing Topic Configuration

```python
from confluent_kafka.admin import AdminClient, ConfigResource

admin = AdminClient({"bootstrap.servers": "localhost:9092"})

# ConfigResource.Type.TOPIC, ConfigResource.Type.BROKER
resource = ConfigResource(ConfigResource.Type.TOPIC, "orders")
futures = admin.describe_configs([resource])

for resource, future in futures.items():
    try:
        config = future.result()
        print(f"Configuration for topic '{resource.name}':")
        for key, entry in sorted(config.items()):
            # Only show non-default values for brevity
            if not entry.is_default:
                print(f"  {key} = {entry.value}  (source: {entry.source.name})")
    except Exception as e:
        print(f"Failed to describe config: {e}")
```

### Altering Topic Configuration

```python
from confluent_kafka.admin import AdminClient, ConfigResource, ConfigEntry

admin = AdminClient({"bootstrap.servers": "localhost:9092"})

resource = ConfigResource(
    ConfigResource.Type.TOPIC,
    "orders",
    set_config={
        "retention.ms": str(14 * 24 * 60 * 60 * 1000),  # change to 14 days
        "segment.ms": str(24 * 60 * 60 * 1000),          # 1-day segments
    },
)

futures = admin.alter_configs([resource])

for resource, future in futures.items():
    try:
        future.result()
        print(f"Config updated for: {resource.name}")
    except Exception as e:
        print(f"Failed to update config: {e}")
```

---

## 11.2 Partition Management

### Increasing Partition Count

```python
from confluent_kafka.admin import AdminClient, NewPartitions

admin = AdminClient({"bootstrap.servers": "localhost:9092"})

# Increase 'orders' topic from current count to 12 partitions
futures = admin.create_partitions([NewPartitions("orders", 12)])

for topic, future in futures.items():
    try:
        future.result()
        print(f"Partition count increased for: {topic}")
    except Exception as e:
        print(f"Failed to increase partitions for '{topic}': {e}")
```

> **Important constraints:**
> - You can only **increase** partition count, never decrease it.
> - Existing messages stay in their current partitions — they are not redistributed.
> - For topics with key-based routing, increasing partitions breaks ordering guarantees for new messages (the same key may now hash to a different partition). See Module 6 for details.

### Checking Current Partition Count Before Increasing

```python
from confluent_kafka.admin import AdminClient, NewPartitions

admin = AdminClient({"bootstrap.servers": "localhost:9092"})

def get_partition_count(admin: AdminClient, topic: str) -> int:
    metadata = admin.list_topics(topic=topic, timeout=10)
    return len(metadata.topics[topic].partitions)

def safe_increase_partitions(admin: AdminClient, topic: str, new_count: int):
    current = get_partition_count(admin, topic)
    if new_count <= current:
        print(f"Topic '{topic}' already has {current} partitions (requested {new_count}). No change.")
        return
    futures = admin.create_partitions([NewPartitions(topic, new_count)])
    futures[topic].result()
    print(f"Topic '{topic}' increased from {current} to {new_count} partitions.")

safe_increase_partitions(admin, "orders", 12)
```

---

## 11.3 Consumer Group Management

### Listing Consumer Groups

```python
from confluent_kafka.admin import AdminClient

admin = AdminClient({"bootstrap.servers": "localhost:9092"})

# list_consumer_groups returns a ListConsumerGroupsResult future
result = admin.list_consumer_groups()
groups = result.result()

print(f"Consumer groups ({len(groups.valid)} found):")
for group in groups.valid:
    print(f"  {group.group_id}  state={group.state.name}  type={group.type.name}")

if groups.errors:
    print("Errors:")
    for error in groups.errors:
        print(f"  {error}")
```

### Describing Consumer Groups

```python
from confluent_kafka.admin import AdminClient

admin = AdminClient({"bootstrap.servers": "localhost:9092"})

group_ids = ["order-processing-group", "analytics-group"]
futures = admin.describe_consumer_groups(group_ids)

for group_id, future in futures.items():
    try:
        description = future.result()
        print(f"\nGroup: {group_id}")
        print(f"  State: {description.state.name}")
        print(f"  Protocol: {description.partition_assignor}")
        print(f"  Members: {len(description.members)}")
        for member in description.members:
            print(f"    Member: {member.member_id}")
            print(f"      Client ID: {member.client_id}")
            print(f"      Host: {member.host}")
            print(f"      Partitions assigned: {len(member.assignment.topic_partitions)}")
            for tp in member.assignment.topic_partitions:
                print(f"        {tp.topic} partition {tp.partition}")
    except Exception as e:
        print(f"Failed to describe group '{group_id}': {e}")
```

### Inspecting Consumer Group Offsets

```python
from confluent_kafka.admin import AdminClient
from confluent_kafka import TopicPartition, ConsumerGroupTopicPartitions

admin = AdminClient({"bootstrap.servers": "localhost:9092"})

# Get committed offsets for a specific group and topic
group_id = "order-processing-group"
topic = "orders"

# First, find out how many partitions the topic has
metadata = admin.list_topics(topic=topic, timeout=10)
partitions = list(metadata.topics[topic].partitions.keys())

topic_partitions = [TopicPartition(topic, p) for p in partitions]

futures = admin.list_consumer_group_offsets(
    [ConsumerGroupTopicPartitions(group_id, topic_partitions)]
)

for group_future in futures.values():
    try:
        result = group_future.result()
        print(f"Committed offsets for group '{result.group_id}':")
        for tp in result.topic_partitions:
            if tp.error:
                print(f"  {tp.topic} partition {tp.partition}: ERROR {tp.error}")
            else:
                print(f"  {tp.topic} partition {tp.partition}: offset {tp.offset}")
    except Exception as e:
        print(f"Failed to get offsets: {e}")
```

### Resetting Consumer Group Offsets

Resetting offsets lets you re-read messages from a specific point. The consumer group must be **inactive** (no running consumers) for the reset to take effect.

```python
from confluent_kafka.admin import AdminClient
from confluent_kafka import TopicPartition, ConsumerGroupTopicPartitions, OFFSET_BEGINNING, OFFSET_END

admin = AdminClient({"bootstrap.servers": "localhost:9092"})

GROUP_ID = "order-processing-group"
TOPIC = "orders"

def reset_offsets_to_beginning(admin: AdminClient, group_id: str, topic: str):
    """Reset all partitions of a topic to the earliest offset for a group."""
    metadata = admin.list_topics(topic=topic, timeout=10)
    partitions = list(metadata.topics[topic].partitions.keys())

    topic_partitions = [
        TopicPartition(topic, p, OFFSET_BEGINNING)  # OFFSET_BEGINNING = -2
        for p in partitions
    ]

    futures = admin.alter_consumer_group_offsets(
        [ConsumerGroupTopicPartitions(group_id, topic_partitions)]
    )

    for future in futures.values():
        try:
            result = future.result()
            print(f"Offsets reset to beginning for group '{result.group_id}':")
            for tp in result.topic_partitions:
                print(f"  {tp.topic} partition {tp.partition}: set to {tp.offset}")
        except Exception as e:
            print(f"Failed to reset offsets: {e}")

def reset_offsets_to_timestamp(admin: AdminClient, group_id: str, topic: str, timestamp_ms: int):
    """Reset offsets to the first message at or after a specific timestamp."""
    from confluent_kafka import Consumer

    # We need a temporary consumer to resolve the timestamp to an offset
    temp_consumer = Consumer({
        "bootstrap.servers": "localhost:9092",
        "group.id": "__temp_offset_resolver__",
    })
    metadata = admin.list_topics(topic=topic, timeout=10)
    partitions = list(metadata.topics[topic].partitions.keys())

    # offsets_for_times returns the offset of the first message >= timestamp
    tps = [TopicPartition(topic, p, timestamp_ms) for p in partitions]
    resolved = temp_consumer.offsets_for_times(tps, timeout=10)
    temp_consumer.close()

    futures = admin.alter_consumer_group_offsets(
        [ConsumerGroupTopicPartitions(group_id, resolved)]
    )

    for future in futures.values():
        try:
            result = future.result()
            print(f"Offsets reset to timestamp {timestamp_ms} for group '{group_id}':")
            for tp in result.topic_partitions:
                print(f"  {tp.topic} partition {tp.partition}: offset {tp.offset}")
        except Exception as e:
            print(f"Failed to reset offsets: {e}")

# Example usage:
admin = AdminClient({"bootstrap.servers": "localhost:9092"})

# Re-process all messages from the beginning:
reset_offsets_to_beginning(admin, "order-processing-group", "orders")

# Re-process messages from the last 2 hours:
import time
two_hours_ago_ms = int((time.time() - 7200) * 1000)
reset_offsets_to_timestamp(admin, "order-processing-group", "orders", two_hours_ago_ms)
```

---

## 11.4 Broker Configuration

### Inspecting Broker Configuration

```python
from confluent_kafka.admin import AdminClient, ConfigResource

admin = AdminClient({"bootstrap.servers": "localhost:9092"})

# Get metadata to find broker IDs
metadata = admin.list_topics(timeout=10)
broker_ids = [str(b) for b in metadata.brokers.keys()]

resources = [ConfigResource(ConfigResource.Type.BROKER, broker_id) for broker_id in broker_ids]
futures = admin.describe_configs(resources)

for resource, future in futures.items():
    try:
        config = future.result()
        print(f"\nBroker {resource.name} — non-default settings:")
        for key, entry in sorted(config.items()):
            if not entry.is_default and not entry.is_sensitive:
                print(f"  {key} = {entry.value}")
    except Exception as e:
        print(f"Failed to describe broker {resource.name}: {e}")
```

---

## 11.5 Complete Admin Toolkit

Here is a self-contained admin utilities module you can drop into any project:

```python
"""
kafka_admin.py — reusable Kafka admin utilities
"""
import time
from typing import Optional
from confluent_kafka.admin import AdminClient, NewTopic, NewPartitions, ConfigResource
from confluent_kafka import KafkaException, TopicPartition, ConsumerGroupTopicPartitions
import confluent_kafka


class KafkaAdmin:
    def __init__(self, bootstrap_servers: str):
        self.admin = AdminClient({"bootstrap.servers": bootstrap_servers})
        self.bootstrap_servers = bootstrap_servers

    # ------------------------------------------------------------------
    # Topics
    # ------------------------------------------------------------------

    def topic_exists(self, topic: str) -> bool:
        metadata = self.admin.list_topics(timeout=10)
        return topic in metadata.topics

    def create_topic(
        self,
        topic: str,
        partitions: int = 1,
        replication_factor: int = 1,
        config: Optional[dict] = None,
    ) -> bool:
        """Create a topic. Returns True if created, False if already exists."""
        new_topic = NewTopic(
            topic,
            num_partitions=partitions,
            replication_factor=replication_factor,
            config=config or {},
        )
        future = self.admin.create_topics([new_topic])
        try:
            future[topic].result()
            print(f"[admin] Created topic '{topic}' ({partitions} partitions, RF={replication_factor})")
            return True
        except KafkaException as e:
            if e.args[0].code() == confluent_kafka.KafkaError.TOPIC_ALREADY_EXISTS:
                print(f"[admin] Topic '{topic}' already exists.")
                return False
            raise

    def delete_topic(self, topic: str, wait_seconds: float = 2.0):
        """Delete a topic and wait briefly for deletion to propagate."""
        future = self.admin.delete_topics([topic])
        future[topic].result()
        print(f"[admin] Deleted topic '{topic}'")
        time.sleep(wait_seconds)

    def get_partition_count(self, topic: str) -> int:
        metadata = self.admin.list_topics(topic=topic, timeout=10)
        return len(metadata.topics[topic].partitions)

    def increase_partitions(self, topic: str, new_total: int):
        current = self.get_partition_count(topic)
        if new_total <= current:
            print(f"[admin] Topic '{topic}' already has {current} partitions.")
            return
        future = self.admin.create_partitions([NewPartitions(topic, new_total)])
        future[topic].result()
        print(f"[admin] Topic '{topic}' partitions: {current} -> {new_total}")

    def describe_topic(self, topic: str):
        """Print partition, leader, replica, and ISR information for a topic."""
        metadata = self.admin.list_topics(topic=topic, timeout=10)
        t = metadata.topics[topic]
        print(f"\nTopic: {topic} ({len(t.partitions)} partitions)")
        for pid, p in sorted(t.partitions.items()):
            print(f"  Partition {pid}: leader={p.leader} replicas={p.replicas} isrs={p.isrs}")

    def set_topic_config(self, topic: str, config: dict):
        resource = ConfigResource(ConfigResource.Type.TOPIC, topic, set_config=config)
        future = self.admin.alter_configs([resource])
        future[resource].result()
        print(f"[admin] Config updated for topic '{topic}': {config}")

    # ------------------------------------------------------------------
    # Consumer groups
    # ------------------------------------------------------------------

    def list_groups(self) -> list[str]:
        result = self.admin.list_consumer_groups().result()
        return [g.group_id for g in result.valid]

    def reset_group_to_beginning(self, group_id: str, topic: str):
        """Reset all partition offsets for a group to the earliest message."""
        metadata = self.admin.list_topics(topic=topic, timeout=10)
        partitions = list(metadata.topics[topic].partitions.keys())
        tps = [TopicPartition(topic, p, confluent_kafka.OFFSET_BEGINNING) for p in partitions]
        future = self.admin.alter_consumer_group_offsets(
            [ConsumerGroupTopicPartitions(group_id, tps)]
        )
        for f in future.values():
            f.result()
        print(f"[admin] Group '{group_id}' offset reset to beginning for topic '{topic}'")

    def get_lag(self, group_id: str, topic: str) -> dict[int, int]:
        """
        Return per-partition lag for a consumer group and topic.
        Lag = high-water-mark offset - committed offset.
        """
        from confluent_kafka import Consumer

        metadata = self.admin.list_topics(topic=topic, timeout=10)
        partitions = list(metadata.topics[topic].partitions.keys())
        tps = [TopicPartition(topic, p) for p in partitions]

        # Get committed offsets
        future = self.admin.list_consumer_group_offsets(
            [ConsumerGroupTopicPartitions(group_id, tps)]
        )
        committed = {}
        for f in future.values():
            result = f.result()
            for tp in result.topic_partitions:
                committed[tp.partition] = tp.offset

        # Get end offsets via a temporary consumer
        temp = Consumer({"bootstrap.servers": self.bootstrap_servers, "group.id": "__lag_checker__"})
        end_offsets = {}
        for p in partitions:
            lo, hi = temp.get_watermark_offsets(TopicPartition(topic, p), timeout=10)
            end_offsets[p] = hi
        temp.close()

        lag = {}
        for p in partitions:
            committed_offset = committed.get(p, 0)
            # -2 = OFFSET_BEGINNING (not yet committed), treat as 0
            if committed_offset < 0:
                committed_offset = 0
            lag[p] = max(0, end_offsets[p] - committed_offset)

        return lag


# ------------------------------------------------------------------
# Example usage
# ------------------------------------------------------------------
if __name__ == "__main__":
    admin = KafkaAdmin("localhost:9092")

    # Create topics
    admin.create_topic("orders", partitions=6, replication_factor=1, config={
        "retention.ms": str(7 * 24 * 60 * 60 * 1000),
    })
    admin.create_topic("orders", partitions=6, replication_factor=1)  # safe to call again

    # Inspect the topic
    admin.describe_topic("orders")

    # List consumer groups
    groups = admin.list_groups()
    print(f"\nConsumer groups: {groups}")

    # Check lag
    if "order-processing-group" in groups:
        lag = admin.get_lag("order-processing-group", "orders")
        total_lag = sum(lag.values())
        print(f"\nLag for 'order-processing-group': {lag} (total: {total_lag})")
```

Run it:
```bash
python3 kafka_admin.py
```

---

## 11.6 CLI Reference (for comparison)

The CLI equivalents are useful when scripting outside Python or debugging:

```bash
# List topics
docker exec kafka /opt/kafka/bin/kafka-topics.sh \
  --list --bootstrap-server localhost:9092

# Create topic
docker exec kafka /opt/kafka/bin/kafka-topics.sh \
  --create --topic orders --partitions 6 --replication-factor 1 \
  --bootstrap-server localhost:9092

# Describe topic
docker exec kafka /opt/kafka/bin/kafka-topics.sh \
  --describe --topic orders --bootstrap-server localhost:9092

# Delete topic
docker exec kafka /opt/kafka/bin/kafka-topics.sh \
  --delete --topic orders --bootstrap-server localhost:9092

# Increase partition count
docker exec kafka /opt/kafka/bin/kafka-topics.sh \
  --alter --topic orders --partitions 12 \
  --bootstrap-server localhost:9092

# List consumer groups
docker exec kafka /opt/kafka/bin/kafka-consumer-groups.sh \
  --list --bootstrap-server localhost:9092

# Describe consumer group (offsets + lag)
docker exec kafka /opt/kafka/bin/kafka-consumer-groups.sh \
  --describe --group order-processing-group \
  --bootstrap-server localhost:9092

# Reset consumer group offsets to beginning (group must be inactive)
docker exec kafka /opt/kafka/bin/kafka-consumer-groups.sh \
  --reset-offsets --to-earliest \
  --group order-processing-group \
  --topic orders \
  --execute \
  --bootstrap-server localhost:9092

# Reset to specific datetime
docker exec kafka /opt/kafka/bin/kafka-consumer-groups.sh \
  --reset-offsets --to-datetime 2025-01-01T00:00:00.000 \
  --group order-processing-group \
  --topic orders \
  --execute \
  --bootstrap-server localhost:9092
```
