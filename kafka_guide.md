# Apache Kafka with Python — Comprehensive Learning Guide

This guide is structured as a progressive learning path. Each module builds on the previous one, taking you from foundational concepts to production-grade Kafka applications in Python.

---

## Table of Contents

1. [Module 1 — Kafka Fundamentals](#module-1--kafka-fundamentals)
2. [Module 2 — Environment Setup](#module-2--environment-setup)
3. [Module 3 — Python Kafka Libraries](#module-3--python-kafka-libraries)
4. [Module 4 — Producers](#module-4--producers)
5. [Module 5 — Consumers](#module-5--consumers)
6. [Module 6 — Topics and Partitions In Depth](#module-6--topics-and-partitions-in-depth)
7. [Module 7 — Serialization and Schemas](#module-7--serialization-and-schemas)
8. [Module 8 — Consumer Groups and Rebalancing](#module-8--consumer-groups-and-rebalancing)
9. [Module 9 — Error Handling and Retries](#module-9--error-handling-and-retries)
10. [Module 10 — Exactly-Once Semantics and Transactions](#module-10--exactly-once-semantics-and-transactions)
11. [Module 11 — Admin Client and Topic Management](#module-11--admin-client-and-topic-management)
12. [Module 12 — Schema Registry](#module-12--schema-registry)
13. [Module 13 — Kafka Connect (Overview for Python Developers)](#module-13--kafka-connect-overview-for-python-developers)
14. [Module 14 — Stream Processing with Faust](#module-14--stream-processing-with-faust)
15. [Module 15 — Testing Kafka Applications](#module-15--testing-kafka-applications)
16. [Module 16 — Monitoring and Observability](#module-16--monitoring-and-observability)
17. [Module 17 — Security](#module-17--security)
18. [Module 18 — Performance Tuning](#module-18--performance-tuning)
19. [Module 19 — Common Patterns and Architectures](#module-19--common-patterns-and-architectures)
20. [Module 20 — Production Deployment Considerations](#module-20--production-deployment-considerations)

---

## Module 1 — Kafka Fundamentals

Before writing any Python code, build a solid mental model of what Kafka is and how it works.

### 1.1 What Is Apache Kafka?

- Distributed event-streaming platform
- Originally developed at LinkedIn, open-sourced via Apache Software Foundation
- Designed for high-throughput, fault-tolerant, durable message delivery
- Use cases: event sourcing, log aggregation, real-time analytics, data pipelines, microservice communication

### 1.2 Core Concepts

| Concept | Description |
|---|---|
| **Broker** | A single Kafka server that stores data and serves clients |
| **Cluster** | A group of brokers working together |
| **Topic** | A named feed/category to which records are published |
| **Partition** | A topic is split into partitions for parallelism; each partition is an ordered, immutable sequence of records |
| **Offset** | A sequential ID given to each record within a partition |
| **Producer** | A client that publishes records to topics |
| **Consumer** | A client that reads records from topics |
| **Consumer Group** | A set of consumers that cooperate to consume a topic; each partition is consumed by exactly one consumer in the group |
| **Replication Factor** | How many copies of each partition are kept across brokers |
| **Leader / Follower** | Each partition has one leader (handles reads/writes) and zero or more followers (replicas) |
| **ZooKeeper / KRaft** | Cluster coordination layer (ZooKeeper is legacy; KRaft is the modern replacement) |

### 1.3 The Commit Log Abstraction

- Kafka stores messages as an append-only commit log
- Messages are retained for a configurable period (or forever), not deleted on consumption
- Consumers track their own position (offset) in the log

### 1.4 Delivery Guarantees

- **At most once** — messages may be lost, never redelivered
- **At least once** — messages are never lost, but may be duplicated
- **Exactly once** — each message is delivered exactly once (requires transactions)

### 1.5 Kafka vs Traditional Message Queues

- Retention: Kafka retains messages; traditional queues delete on ack
- Consumer groups: Kafka supports multiple independent consumers
- Ordering: Kafka guarantees ordering within a partition
- Throughput: Kafka is designed for millions of messages per second

---

## Module 2 — Environment Setup (macOS with Docker)

### 2.1 Prerequisites

**Step 1: Install Homebrew** (if not already installed)

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

**Step 2: Install Docker Desktop for macOS**


Verify Docker is working:

```bash
docker --version
docker compose version
```

---

### 2.2 Single-Broker Kafka Setup with Docker Compose

**Step 1: Create a project directory**

```bash
mkdir kafka-docker && cd kafka-docker
```

**Step 2: Create `docker-compose.yml`**

Create a file called `docker-compose.yml` with the following contents:

```yaml
services:
  kafka:
    image: apache/kafka:latest
    container_name: kafka
    user: root
    ports:
      - "9092:9092"
    environment:
      KAFKA_NODE_ID: 1
      KAFKA_PROCESS_ROLES: broker,controller
      KAFKA_LISTENERS: INTERNAL://0.0.0.0:9094,EXTERNAL://0.0.0.0:9092,CONTROLLER://0.0.0.0:9093
      KAFKA_ADVERTISED_LISTENERS: INTERNAL://kafka:9094,EXTERNAL://localhost:9092
      KAFKA_INTER_BROKER_LISTENER_NAME: INTERNAL
      KAFKA_CONTROLLER_LISTENER_NAMES: CONTROLLER
      KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: CONTROLLER:PLAINTEXT,INTERNAL:PLAINTEXT,EXTERNAL:PLAINTEXT
      KAFKA_CONTROLLER_QUORUM_VOTERS: 1@kafka:9093
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
      KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR: 1
      KAFKA_TRANSACTION_STATE_LOG_MIN_ISR: 1
      KAFKA_LOG_DIRS: /tmp/kraft-combined-logs
      CLUSTER_ID: "MkU3OEVBNTcwNTJENDM2Qk"
```

> **Notes:**
> - `user: root` is required because the `apache/kafka` image runs as `appuser` (uid 1000) by default, which does not have write permissions to the data directory. Without it, the container will crash with `AccessDeniedException`.
> - Two listeners are configured: `INTERNAL://kafka:9094` for container-to-container communication (e.g. Kafka UI, Schema Registry) and `EXTERNAL://localhost:9092` for access from your Mac (Python scripts, CLI tools). Without this split, other Docker containers would try to reach Kafka at `localhost:9092`, which inside a container points to themselves — not the Kafka broker.

**Step 3: Start Kafka**

```bash
docker compose up -d
```

**Step 4: Verify the broker is running**

```bash
docker compose logs kafka | tail -20
```

Look for a line containing `Kafka Server started`. You can also check the container status:

```bash
docker compose ps
```

The `kafka` container should show `running`.

---

### 2.3 Creating Topics and Testing with CLI (Inside Docker)

Since Kafka CLI tools are inside the container, you run them via `docker exec`.

**Step 1: Create a topic**

```bash
docker exec kafka /opt/kafka/bin/kafka-topics.sh --create \
  --topic my-first-topic \
  --bootstrap-server localhost:9092 \
  --partitions 3 \
  --replication-factor 1
```

**Step 2: List topics**

```bash
docker exec kafka /opt/kafka/bin/kafka-topics.sh --list --bootstrap-server localhost:9092
```

**Step 3: Describe the topic**

```bash
docker exec kafka /opt/kafka/bin/kafka-topics.sh --describe \
  --topic my-first-topic \
  --bootstrap-server localhost:9092
```

**Step 4: Produce messages via CLI**

```bash
docker exec -it kafka /opt/kafka/bin/kafka-console-producer.sh \
  --topic my-first-topic \
  --bootstrap-server localhost:9092
```

Type messages line by line, pressing Enter after each:

```
Hello Kafka
Second message
Third message
```

Press `Ctrl+C` to exit the producer.

**Step 5: Consume messages via CLI**

```bash
docker exec -it kafka /opt/kafka/bin/kafka-console-consumer.sh \
  --topic my-first-topic \
  --bootstrap-server localhost:9092 \
  --from-beginning
```

You should see all three messages. Press `Ctrl+C` to exit.

**Step 6: Test real-time streaming**

Open two terminal windows side by side:
- **Terminal 1** — run the console producer (Step 4)
- **Terminal 2** — run the console consumer without `--from-beginning`

Type messages in Terminal 1 and watch them appear instantly in Terminal 2.

---

### 2.4 Adding Kafka UI (Web Dashboard)

Kafka UI gives you a browser-based interface to inspect topics, messages, consumer groups, and brokers.

**Step 1: Update `docker-compose.yml`**

Add the `kafka-ui` service below the existing `kafka` service:

```yaml
services:
  kafka:
    image: apache/kafka:latest
    container_name: kafka
    user: root
    ports:
      - "9092:9092"
    environment:
      KAFKA_NODE_ID: 1
      KAFKA_PROCESS_ROLES: broker,controller
      KAFKA_LISTENERS: INTERNAL://0.0.0.0:9094,EXTERNAL://0.0.0.0:9092,CONTROLLER://0.0.0.0:9093
      KAFKA_ADVERTISED_LISTENERS: INTERNAL://kafka:9094,EXTERNAL://localhost:9092
      KAFKA_INTER_BROKER_LISTENER_NAME: INTERNAL
      KAFKA_CONTROLLER_LISTENER_NAMES: CONTROLLER
      KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: CONTROLLER:PLAINTEXT,INTERNAL:PLAINTEXT,EXTERNAL:PLAINTEXT
      KAFKA_CONTROLLER_QUORUM_VOTERS: 1@kafka:9093
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
      KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR: 1
      KAFKA_TRANSACTION_STATE_LOG_MIN_ISR: 1
      KAFKA_LOG_DIRS: /tmp/kraft-combined-logs
      CLUSTER_ID: "MkU3OEVBNTcwNTJENDM2Qk"

  kafka-ui:
    image: provectuslabs/kafka-ui:latest
    container_name: kafka-ui
    ports:
      - "8080:8080"
    environment:
      KAFKA_CLUSTERS_0_NAME: local
      KAFKA_CLUSTERS_0_BOOTSTRAPSERVERS: kafka:9094
    depends_on:
      - kafka
```

**Step 2: Restart the stack**

```bash
docker compose up -d
```

**Step 3: Open Kafka UI**

Open your browser and go to: `http://localhost:8080`

You can now:
- Browse topics and their messages
- View partitions and offsets
- Monitor consumer groups and lag
- Create new topics from the UI

---

### 2.5 Adding Schema Registry (Optional)

Schema Registry enforces data contracts for your Kafka messages. Add it to your `docker-compose.yml`:

```yaml
  schema-registry:
    image: confluentinc/cp-schema-registry:7.6.0
    container_name: schema-registry
    ports:
      - "8081:8081"
    environment:
      SCHEMA_REGISTRY_HOST_NAME: schema-registry
      SCHEMA_REGISTRY_KAFKASTORE_BOOTSTRAP_SERVERS: kafka:9094
      SCHEMA_REGISTRY_LISTENERS: http://0.0.0.0:8081
    depends_on:
      - kafka
```

After adding, run `docker compose up -d`. The Schema Registry API is available at `http://localhost:8081`.

To also connect it to Kafka UI, add this env var to the `kafka-ui` service:

```yaml
      KAFKA_CLUSTERS_0_SCHEMAREGISTRY: http://schema-registry:8081
```

---

### 2.6 Multi-Broker Setup (3 Brokers)

For a more realistic local cluster, use three brokers:

```yaml
services:
  kafka-1:
    image: apache/kafka:latest
    container_name: kafka-1
    user: root
    ports:
      - "9092:9092"
    environment:
      KAFKA_NODE_ID: 1
      KAFKA_PROCESS_ROLES: broker,controller
      KAFKA_LISTENERS: INTERNAL://0.0.0.0:9094,EXTERNAL://0.0.0.0:9092,CONTROLLER://0.0.0.0:9093
      KAFKA_ADVERTISED_LISTENERS: INTERNAL://kafka-1:9094,EXTERNAL://localhost:9092
      KAFKA_INTER_BROKER_LISTENER_NAME: INTERNAL
      KAFKA_CONTROLLER_LISTENER_NAMES: CONTROLLER
      KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: CONTROLLER:PLAINTEXT,INTERNAL:PLAINTEXT,EXTERNAL:PLAINTEXT
      KAFKA_CONTROLLER_QUORUM_VOTERS: 1@kafka-1:9093,2@kafka-2:9093,3@kafka-3:9093
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 3
      KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR: 3
      KAFKA_TRANSACTION_STATE_LOG_MIN_ISR: 2
      KAFKA_LOG_DIRS: /tmp/kraft-combined-logs
      CLUSTER_ID: "MkU3OEVBNTcwNTJENDM2Qk"

  kafka-2:
    image: apache/kafka:latest
    container_name: kafka-2
    user: root
    ports:
      - "9095:9092"
    environment:
      KAFKA_NODE_ID: 2
      KAFKA_PROCESS_ROLES: broker,controller
      KAFKA_LISTENERS: INTERNAL://0.0.0.0:9094,EXTERNAL://0.0.0.0:9092,CONTROLLER://0.0.0.0:9093
      KAFKA_ADVERTISED_LISTENERS: INTERNAL://kafka-2:9094,EXTERNAL://localhost:9095
      KAFKA_INTER_BROKER_LISTENER_NAME: INTERNAL
      KAFKA_CONTROLLER_LISTENER_NAMES: CONTROLLER
      KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: CONTROLLER:PLAINTEXT,INTERNAL:PLAINTEXT,EXTERNAL:PLAINTEXT
      KAFKA_CONTROLLER_QUORUM_VOTERS: 1@kafka-1:9093,2@kafka-2:9093,3@kafka-3:9093
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 3
      KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR: 3
      KAFKA_TRANSACTION_STATE_LOG_MIN_ISR: 2
      KAFKA_LOG_DIRS: /tmp/kraft-combined-logs
      CLUSTER_ID: "MkU3OEVBNTcwNTJENDM2Qk"

  kafka-3:
    image: apache/kafka:latest
    container_name: kafka-3
    user: root
    ports:
      - "9096:9092"
    environment:
      KAFKA_NODE_ID: 3
      KAFKA_PROCESS_ROLES: broker,controller
      KAFKA_LISTENERS: INTERNAL://0.0.0.0:9094,EXTERNAL://0.0.0.0:9092,CONTROLLER://0.0.0.0:9093
      KAFKA_ADVERTISED_LISTENERS: INTERNAL://kafka-3:9094,EXTERNAL://localhost:9096
      KAFKA_INTER_BROKER_LISTENER_NAME: INTERNAL
      KAFKA_CONTROLLER_LISTENER_NAMES: CONTROLLER
      KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: CONTROLLER:PLAINTEXT,INTERNAL:PLAINTEXT,EXTERNAL:PLAINTEXT
      KAFKA_CONTROLLER_QUORUM_VOTERS: 1@kafka-1:9093,2@kafka-2:9093,3@kafka-3:9093
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 3
      KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR: 3
      KAFKA_TRANSACTION_STATE_LOG_MIN_ISR: 2
      KAFKA_LOG_DIRS: /tmp/kraft-combined-logs
      CLUSTER_ID: "MkU3OEVBNTcwNTJENDM2Qk"

  kafka-ui:
    image: provectuslabs/kafka-ui:latest
    container_name: kafka-ui
    ports:
      - "8080:8080"
    environment:
      KAFKA_CLUSTERS_0_NAME: local
      KAFKA_CLUSTERS_0_BOOTSTRAPSERVERS: kafka-1:9094,kafka-2:9094,kafka-3:9094
    depends_on:
      - kafka-1
      - kafka-2
      - kafka-3
```

With this setup, you can create topics with `--replication-factor 3` and test broker failure scenarios by stopping individual containers:

```bash
docker compose stop kafka-2
```

Your Python clients connect using all three brokers:

```python
bootstrap_servers = "localhost:9092,localhost:9095,localhost:9096"
```

---

### 2.7 Python Environment Setup

**Step 1: Create a project directory**

```bash
mkdir kafka-python-project && cd kafka-python-project
```

**Step 2: Create a virtual environment**

Using `venv`:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Or using `uv` (faster alternative):

```bash
brew install uv
uv venv
source .venv/bin/activate
```

**Step 3: Install Kafka Python libraries**

```bash
# confluent-kafka (recommended for production)
pip install confluent-kafka

# kafka-python (maintained fork — good for learning)
pip install kafka-python-ng

# aiokafka (for async applications)
pip install aiokafka
```

**Step 4: Verify the installation**

```bash
python3 -c "from confluent_kafka import Producer; print('confluent-kafka OK')"
python3 -c "from kafka import KafkaProducer; print('kafka-python OK')"
python3 -c "from aiokafka import AIOKafkaProducer; print('aiokafka OK')"
```

---

### 2.8 Smoke Test — Produce and Consume from Python

Make sure your Docker Kafka stack is running (`docker compose up -d`), then create `test_kafka.py`:

```python
from confluent_kafka import Producer, Consumer
import time

# --- Producer ---
producer = Producer({"bootstrap.servers": "localhost:9092"})

for i in range(5):
    producer.produce("my-first-topic", value=f"Message {i}".encode("utf-8"))
    print(f"Produced: Message {i}")

producer.flush()

# Give the broker a moment
time.sleep(1)

# --- Consumer ---
consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "my-test-group",
    "auto.offset.reset": "earliest",
})

consumer.subscribe(["my-first-topic"])

print("\nConsuming messages:")
for _ in range(10):
    msg = consumer.poll(timeout=2.0)
    if msg is None:
        break
    if msg.error():
        print(f"Error: {msg.error()}")
    else:
        print(f"Received: {msg.value().decode('utf-8')} "
              f"[partition={msg.partition()}, offset={msg.offset()}]")

consumer.close()
```

Run it:

```bash
python3 test_kafka.py
```

You should see produced messages followed by consumed messages with partition and offset info.

---

### 2.9 Managing Your Docker Kafka Stack

| Task | Command |
|---|---|
| Start the stack | `docker compose up -d` |
| Stop the stack (keep data) | `docker compose stop` |
| Stop and remove containers (keeps data) | `docker compose down` |
| Stop and remove everything (destroys all data) | `docker compose down -v` |
| View logs | `docker compose logs -f kafka` |
| Check running containers | `docker compose ps` |
| Restart a single service | `docker compose restart kafka` |
| Shell into the Kafka container | `docker exec -it kafka bash` |

> **Understanding data persistence with Docker Kafka:**
>
> Kafka stores all its data (topics, messages, offsets, consumer groups) inside the
> container's filesystem or in Docker volumes. This means:
>
> - `docker compose stop` — pauses containers. All data survives. Use this when you're
>   done for the day and want to resume later with `docker compose up -d`.
> - `docker compose down` — removes containers and networks, but **preserves named
>   volumes**. If your compose file defines a named volume, topic data survives.
>   However, if no named volume is configured (as in the setup above), data is stored
>   inside the container and **will be lost**.
> - `docker compose down -v` — removes containers, networks, **and all volumes**.
>   This is a full reset: all topics, messages, consumer group offsets, and broker
>   state are permanently deleted. The next `docker compose up -d` starts a
>   completely fresh cluster. **Only use this when you intentionally want to wipe
>   everything and start over.**
>
> **Rule of thumb:** Use `docker compose stop` / `docker compose up -d` for daily
> start/stop. Only use `docker compose down -v` when you want a clean slate.

---

### 2.10 Troubleshooting Common Issues on macOS

**Port 9092 already in use:**

```bash
lsof -i :9092
```

Kill the conflicting process or change the port mapping in `docker-compose.yml`.

**Docker Desktop not running:**

If you see `Cannot connect to the Docker daemon`, open Docker Desktop and wait for it to fully start.

**Container exits immediately:**

Check logs for errors:

```bash
docker compose logs kafka
```

Common cause: invalid `CLUSTER_ID` or conflicting data in the volume. Fix by removing volumes:

```bash
docker compose down -v
docker compose up -d
```

> **Warning:** `docker compose down -v` destroys all Kafka data — topics, messages,
> consumer group offsets, everything. Only use this as a last resort when the broker
> won't start. If you have data you care about, try `docker compose down` (without
> `-v`) followed by `docker compose up -d` first.

**Kafka UI not loading:**

The Kafka broker may still be starting. Wait 10-15 seconds and refresh. Check that the `kafka` container is healthy first:

```bash
docker compose ps
```

**Apple Silicon (M1/M2/M3) compatibility:**

The `apache/kafka` and `provectuslabs/kafka-ui` images support ARM64 natively. If you encounter issues with other images, add `platform: linux/amd64` to the service in `docker-compose.yml`.

---

## Module 3 — Python Kafka Libraries

### 3.1 Library Comparison

| Library | Backed By | Protocol | Async Support | Performance | Notes |
|---|---|---|---|---|---|
| `confluent-kafka` | Confluent | librdkafka (C) | No (sync) | Excellent | Production-grade, most feature-complete |
| `kafka-python` | Community | Pure Python | No (sync) | Good | Easy to install, no C dependencies |
| `aiokafka` | Community | Pure Python | Yes (asyncio) | Good | Best choice for async applications |
| `faust` | Robinhood | aiokafka | Yes (asyncio) | Good | Stream processing framework |

### 3.2 Choosing the Right Library

- `confluent-kafka` for most production workloads
- `aiokafka` when your application is async (FastAPI, aiohttp, etc.)
- `kafka-python` for learning and quick prototypes
- `faust` when you need stream processing

### 3.3 Installation

```bash
# confluent-kafka (recommended for production)
pip install confluent-kafka

# kafka-python
pip install kafka-python-ng   # maintained fork of kafka-python

# aiokafka
pip install aiokafka
```

---

## Module 4 — Producers

### 4.1 Basic Producer

- Creating a producer instance and connecting to a broker
- Sending a simple message to a topic
- Synchronous vs asynchronous produce calls
- Flushing and closing the producer

### 4.2 Producer Configuration

| Config | Purpose |
|---|---|
| `bootstrap.servers` | Broker addresses |
| `acks` | `0`, `1`, or `all` — durability guarantee level |
| `retries` | Number of retries on transient failures |
| `linger.ms` | How long to wait before sending a batch |
| `batch.size` | Maximum batch size in bytes |
| `compression.type` | `none`, `gzip`, `snappy`, `lz4`, `zstd` |
| `max.in.flight.requests.per.connection` | Controls ordering guarantees |
| `enable.idempotence` | Prevents duplicate messages |

### 4.3 Callbacks and Delivery Reports

- Using delivery callbacks to confirm message delivery
- Handling delivery errors in callbacks
- Logging and metrics from callbacks

### 4.4 Message Keys

- Why keys matter: partitioning and ordering
- How Kafka hashes keys to determine partition assignment
- Choosing good keys for your use case

### 4.5 Message Headers

- Attaching metadata to messages without modifying the payload
- Common uses: tracing IDs, content type, source service

### 4.6 Partitioner Strategies

- Default partitioner (murmur2 hash of the key)
- Round-robin (when no key is provided)
- Custom partitioners
- Sticky partitioner behavior

### 4.7 Timestamps

- `CreateTime` vs `LogAppendTime`
- Setting timestamps explicitly

### 4.8 Producer Patterns

- Fire-and-forget
- Synchronous send (blocking until ack)
- Asynchronous send with callback
- Buffered/batched sending for throughput

---

## Module 5 — Consumers

### 5.1 Basic Consumer

- Creating a consumer instance
- Subscribing to a topic
- The poll loop
- Deserializing messages
- Graceful shutdown

### 5.2 Consumer Configuration

| Config | Purpose |
|---|---|
| `bootstrap.servers` | Broker addresses |
| `group.id` | Consumer group identity |
| `auto.offset.reset` | `earliest`, `latest`, or `none` — what to do when no committed offset exists |
| `enable.auto.commit` | Whether offsets are committed automatically |
| `auto.commit.interval.ms` | How often auto-commit runs |
| `max.poll.records` | Max records returned per poll |
| `max.poll.interval.ms` | Max time between polls before the consumer is considered dead |
| `session.timeout.ms` | Heartbeat timeout |
| `fetch.min.bytes` / `fetch.max.bytes` | Fetch size tuning |

### 5.3 Offset Management

- What offsets are and why they matter
- Auto-commit: how it works and its risks (data loss, duplicates)
- Manual commit: `commit()` (sync) and `commit_async()`
- Committing after each message vs after each batch
- Seeking to a specific offset
- Rewinding / replaying messages

### 5.4 Consumer Lifecycle

- Subscribe vs assign
- The poll-process-commit loop
- Handling `on_revoke` and `on_assign` callbacks during rebalancing
- Graceful shutdown with `consumer.close()`

### 5.5 Consuming from Specific Partitions

- `assign()` for manual partition assignment
- When to use assign vs subscribe
- Consuming from a specific offset

### 5.6 Async Consumer (aiokafka)

- Using `AIOKafkaConsumer` with `asyncio`
- Integrating with async web frameworks (FastAPI, aiohttp)
- Handling backpressure in async consumers

---

## Module 6 — Topics and Partitions In Depth

### 6.1 Topic Design

- Naming conventions
- How many topics do you need? (one topic per event type vs fewer broad topics)
- Topic as a contract between services

### 6.2 Partition Count

- How to choose the number of partitions
- Relationship between partitions and consumer parallelism
- Impact on ordering guarantees
- Repartitioning pitfalls (Kafka does not redistribute existing data)

### 6.3 Replication

- Replication factor and its impact on durability
- In-sync replicas (ISR)
- `min.insync.replicas` configuration
- What happens when a broker goes down

### 6.4 Retention Policies

- Time-based retention (`retention.ms`)
- Size-based retention (`retention.bytes`)
- Compacted topics: keeping only the latest value per key
- Tombstones (null values) in compacted topics
- Infinite retention

### 6.5 Log Segments

- How Kafka stores data on disk
- Segment files, indexes, and cleanup

---

## Module 7 — Serialization and Schemas

### 7.1 String and Bytes

- Default serialization: strings and bytes
- UTF-8 encoding considerations

### 7.2 JSON Serialization

- Serializing/deserializing Python dicts to/from JSON
- Pros: human-readable, flexible
- Cons: no schema enforcement, larger payload size, slower

### 7.3 Avro Serialization

- What is Avro and why it is popular with Kafka
- Defining Avro schemas
- Using `confluent-kafka` with `AvroSerializer` / `AvroDeserializer`
- Schema evolution rules (backward, forward, full compatibility)

### 7.4 Protobuf Serialization

- Using Protocol Buffers with Kafka
- Generating Python code from `.proto` files
- `ProtobufSerializer` / `ProtobufDeserializer`

### 7.5 MessagePack, CBOR, and Other Formats

- When to consider binary formats
- Trade-offs: speed, size, human-readability, schema support

### 7.6 Custom Serializers

- Writing your own serializer/deserializer classes
- Registering them with the producer/consumer

---

## Module 8 — Consumer Groups and Rebalancing

### 8.1 How Consumer Groups Work

- Partition assignment within a group
- One partition per consumer per group (max parallelism = partition count)
- Multiple consumer groups reading the same topic independently

### 8.2 Rebalancing

- What triggers a rebalance (new consumer joins, consumer leaves, consumer crashes, topic partition change)
- Eager rebalancing (stop-the-world)
- Cooperative (incremental) rebalancing
- Configuring the partition assignor (`range`, `roundrobin`, `sticky`, `cooperative-sticky`)

### 8.3 Static Group Membership

- `group.instance.id` for stable consumer identity
- Reducing unnecessary rebalances during rolling deployments

### 8.4 Rebalance Listeners

- Implementing `on_partitions_revoked` and `on_partitions_assigned`
- Committing offsets before revocation
- Cleaning up state during revocation

### 8.5 Consumer Group Lag

- What is consumer lag?
- Monitoring lag with `kafka-consumer-groups.sh`
- Programmatic lag monitoring

---

## Module 9 — Error Handling and Retries

### 9.1 Producer Errors

- Retriable vs non-retriable errors
- Configuring retries and `delivery.timeout.ms`
- Handling `BufferError` (local queue full)
- Dead letter topics for poison pills

### 9.2 Consumer Errors

- Deserialization errors
- Processing errors
- Poison pill messages (messages that always fail processing)
- Dead letter queue pattern

### 9.3 Retry Patterns

- Simple retry with backoff
- Retry topics (e.g., `my-topic-retry-1`, `my-topic-retry-2`, `my-topic-dlq`)
- Exponential backoff with retry topics
- Circuit breaker pattern

### 9.4 Idempotent Processing

- Why idempotency matters in at-least-once delivery
- Designing idempotent consumers
- Using message IDs / deduplication keys

---

## Module 10 — Exactly-Once Semantics and Transactions

### 10.1 Idempotent Producer

- Enabling `enable.idempotence=True`
- How Kafka deduplicates at the broker level
- Producer ID and sequence numbers

### 10.2 Transactional Producer

- `transactional.id` configuration
- `init_transactions()`, `begin_transaction()`, `commit_transaction()`, `abort_transaction()`
- Sending multiple messages atomically

### 10.3 Consume-Transform-Produce Pattern

- Reading from one topic, processing, and writing to another atomically
- `send_offsets_to_transaction()` — committing consumer offsets as part of a transaction
- End-to-end exactly-once semantics

### 10.4 Limitations and Trade-offs

- Performance impact of transactions
- Transactions do not extend beyond Kafka (external systems still need idempotency)
- Transaction timeout configuration

---

## Module 11 — Admin Client and Topic Management

### 11.1 AdminClient Basics

- Creating topics programmatically
- Listing topics
- Deleting topics
- Describing topic configuration

### 11.2 Partition Management

- Increasing partition count
- Reassigning partition replicas

### 11.3 Consumer Group Management

- Listing consumer groups
- Describing consumer group offsets
- Resetting offsets programmatically

### 11.4 ACLs and Quotas

- Viewing and managing ACLs (if using authorization)
- Setting client quotas

---

## Module 12 — Schema Registry

### 12.1 What Is Schema Registry?

- Centralized schema management for Kafka
- Subjects, versions, and compatibility modes
- Why schema enforcement matters in distributed systems

### 12.2 Compatibility Modes

| Mode | Rule |
|---|---|
| BACKWARD | New schema can read data from old schema |
| FORWARD | Old schema can read data from new schema |
| FULL | Both backward and forward compatible |
| NONE | No compatibility check |

### 12.3 Using Schema Registry with Python

- `confluent_kafka.schema_registry` client
- Registering schemas
- `AvroSerializer` / `AvroDeserializer` with automatic schema registration
- Protobuf and JSON Schema support

### 12.4 Schema Evolution in Practice

- Adding optional fields
- Removing fields with defaults
- Renaming fields (and why it is tricky)
- When to create a new topic vs evolving the schema

---

## Module 13 — Kafka Connect (Overview for Python Developers)

### 13.1 What Is Kafka Connect?

- Framework for streaming data between Kafka and external systems
- Source connectors (external system -> Kafka) and sink connectors (Kafka -> external system)
- Why you might not need to write Python code for data ingestion

### 13.2 Common Connectors

- JDBC source/sink (databases)
- Elasticsearch sink
- S3 sink
- Debezium (CDC — change data capture from databases)
- File source/sink

### 13.3 REST API for Connector Management

- Creating, updating, deleting connectors via HTTP
- Monitoring connector status
- Managing connectors from Python using `requests`

### 13.4 Single Message Transforms (SMTs)

- Lightweight message transformations in the connector pipeline
- Common transforms: field renaming, timestamp conversion, routing

---

## Module 14 — Stream Processing with Faust

### 14.1 What Is Faust?

- Python stream processing library inspired by Kafka Streams (Java)
- Built on `asyncio` and `aiokafka`
- Concepts: agents, tables, windows

### 14.2 Agents

- Defining a stream processor as an async generator
- Processing messages one at a time
- Sending results to other topics

### 14.3 Tables (State Stores)

- Maintaining local state (counts, aggregations)
- Backed by changelog topics for fault tolerance
- Windowed tables (tumbling, hopping, sliding)

### 14.4 Windowed Aggregations

- Tumbling windows
- Hopping windows
- Counting events per time window

### 14.5 Web Views

- Exposing table state via HTTP endpoints
- Building real-time dashboards

### 14.6 Alternatives to Faust

- `quixstreams` — modern Python stream processing
- `bytewax` — Rust-backed, Python-native dataflow processing
- Kafka Streams (Java/Scala) — when Python is not the right choice

---

## Module 15 — Testing Kafka Applications

### 15.1 Unit Testing

- Mocking the producer and consumer
- Testing serialization/deserialization logic independently
- Testing message processing logic without Kafka

### 15.2 Integration Testing

- Using `testcontainers-python` to spin up Kafka in Docker for tests
- Writing integration tests that produce and consume real messages
- Waiting for messages with timeouts

### 15.3 Testing Patterns

- Producing test events and asserting on consumed results
- Testing consumer group rebalancing behavior
- Testing error handling and dead letter queues
- Testing schema evolution (old producer, new consumer and vice versa)

### 15.4 Load Testing

- Tools: `kafka-producer-perf-test.sh`, `kafka-consumer-perf-test.sh`
- Writing custom Python load generators
- Measuring throughput, latency, and consumer lag under load

---

## Module 16 — Monitoring and Observability

### 16.1 Key Metrics

**Producer metrics:**
- Record send rate, byte rate
- Record error rate
- Request latency
- Batch size
- Buffer available bytes

**Consumer metrics:**
- Records consumed rate
- Consumer lag (most critical metric)
- Commit rate
- Rebalance rate
- Poll interval

**Broker metrics:**
- Under-replicated partitions
- ISR shrink/expand rate
- Request handler utilization
- Log flush latency

### 16.2 Monitoring Tools

- Kafka UI / AKHQ / Kafdrop for visual topic inspection
- Prometheus + Grafana for metrics
- JMX metrics (broker-side)
- `confluent-kafka` statistics callback for client-side metrics

### 16.3 Logging Best Practices

- Structured logging with correlation IDs
- Logging message metadata (topic, partition, offset) without logging message payloads
- Log levels for Kafka client libraries

### 16.4 Distributed Tracing

- Propagating trace IDs through message headers
- OpenTelemetry integration with Kafka producers and consumers
- Visualizing message flow across services

---

## Module 17 — Security

### 17.1 Authentication

- PLAINTEXT (no auth — development only)
- SASL/PLAIN (username/password)
- SASL/SCRAM (salted challenge-response)
- SASL/OAUTHBEARER (OAuth 2.0 tokens)
- mTLS (mutual TLS with client certificates)

### 17.2 Encryption

- TLS/SSL for encryption in transit
- Configuring `ssl.ca.location`, `ssl.certificate.location`, `ssl.key.location`
- Encryption at rest (broker-side disk encryption)

### 17.3 Authorization

- Kafka ACLs (Access Control Lists)
- Principle of least privilege: restricting topic access per service
- RBAC with Confluent Platform

### 17.4 Python Configuration Examples

- Configuring `confluent-kafka` with SASL/SCRAM + TLS
- Configuring `aiokafka` with SSL
- Storing credentials securely (environment variables, secret managers)

---

## Module 18 — Performance Tuning

### 18.1 Producer Tuning

- Batching: `linger.ms` and `batch.size` trade-offs
- Compression: choosing the right codec (`lz4` for speed, `zstd` for ratio)
- `acks=all` vs `acks=1` — durability vs latency
- Buffer memory and `max.block.ms`

### 18.2 Consumer Tuning

- `fetch.min.bytes` and `fetch.max.wait.ms` — batching fetches
- `max.poll.records` — controlling processing batch size
- `max.partition.fetch.bytes` — memory management
- Threading model: one consumer per thread (confluent-kafka) vs async (aiokafka)

### 18.3 Partition Tuning

- More partitions = more parallelism (but also more overhead)
- Rule of thumb for partition count
- Key distribution and hot partitions

### 18.4 Network and OS Tuning

- Increasing file descriptor limits
- TCP tuning (send/receive buffer sizes)
- Page cache: why Kafka loves RAM

### 18.5 Benchmarking

- Running producer and consumer performance tests
- Measuring end-to-end latency
- Identifying bottlenecks (network, disk, CPU, consumer processing time)

---

## Module 19 — Common Patterns and Architectures

### 19.1 Event-Driven Architecture

- Events as first-class citizens
- Event notification vs event-carried state transfer
- Event sourcing with Kafka as the event store

### 19.2 CQRS (Command Query Responsibility Segregation)

- Using Kafka to separate write and read models
- Building read-optimized projections from Kafka events

### 19.3 Saga / Choreography Pattern

- Coordinating distributed transactions across microservices via events
- Compensating actions on failure

### 19.4 Change Data Capture (CDC)

- Streaming database changes to Kafka (Debezium)
- Keeping services in sync without tight coupling
- Outbox pattern

### 19.5 Request-Reply Pattern

- Implementing synchronous-style request-reply over Kafka
- Correlation IDs and reply topics
- When to use this (and when not to)

### 19.6 Fan-Out and Fan-In

- One producer, many consumer groups (fan-out)
- Many producers, one consumer group (fan-in)

### 19.7 Data Pipeline Architecture

- Kafka as the backbone of a data platform
- Connecting operational systems to analytics (data lake, data warehouse)
- Lambda and Kappa architectures

---

## Module 20 — Production Deployment Considerations

### 20.1 Cluster Sizing

- Number of brokers
- Disk capacity planning (based on retention and throughput)
- Memory and CPU requirements

### 20.2 High Availability

- Multi-broker clusters
- Rack-awareness for replica placement
- Cross-datacenter replication (MirrorMaker 2, Confluent Replicator)

### 20.3 Upgrades and Maintenance

- Rolling broker upgrades
- Client compatibility with broker versions
- Consumer group offset migration

### 20.4 Disaster Recovery

- Backup and restore strategies
- Multi-region active-active and active-passive setups
- RPO and RTO considerations

### 20.5 Operational Runbooks

- What to do when a broker goes down
- What to do when consumer lag is growing
- What to do when a topic runs out of disk space
- Handling stuck consumers and rebalance storms

### 20.6 Capacity Planning

- Estimating throughput: messages/sec x message size
- Retention requirements: throughput x retention period x replication factor
- Network bandwidth: inter-broker replication, producer/consumer traffic

---

## Suggested Learning Path

### Beginner (Weeks 1-2)
1. Module 1 — Kafka Fundamentals
2. Module 2 — Environment Setup
3. Module 3 — Python Kafka Libraries
4. Module 4 — Producers (sections 4.1–4.4)
5. Module 5 — Consumers (sections 5.1–5.3)

### Intermediate (Weeks 3-4)
6. Module 6 — Topics and Partitions In Depth
7. Module 7 — Serialization and Schemas (JSON + Avro)
8. Module 8 — Consumer Groups and Rebalancing
9. Module 9 — Error Handling and Retries
10. Module 4 — Producers (remaining sections)
11. Module 5 — Consumers (remaining sections)

### Advanced (Weeks 5-8)
12. Module 10 — Exactly-Once Semantics and Transactions
13. Module 11 — Admin Client and Topic Management
14. Module 12 — Schema Registry
15. Module 14 — Stream Processing with Faust
16. Module 15 — Testing Kafka Applications

### Production-Ready (Weeks 9-12)
17. Module 13 — Kafka Connect
18. Module 16 — Monitoring and Observability
19. Module 17 — Security
20. Module 18 — Performance Tuning
21. Module 19 — Common Patterns and Architectures
22. Module 20 — Production Deployment Considerations

---

## Recommended Resources

- **Books:** *Kafka: The Definitive Guide* (O'Reilly), *Designing Event-Driven Systems* (Ben Stopford, free PDF from Confluent)
- **Documentation:** [Apache Kafka docs](https://kafka.apache.org/documentation/), [confluent-kafka-python docs](https://docs.confluent.io/kafka-clients/python/current/overview.html)
- **Courses:** Confluent Developer courses (free), Stephane Maarek's Kafka courses on Udemy
- **Community:** Confluent Community Slack, r/apachekafka, Stack Overflow `apache-kafka` tag
