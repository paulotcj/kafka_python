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

#### What Is Schema Registry and Why Do You Need It?

When producers send messages to Kafka, they are just bytes — Kafka itself does not
know or care about the structure of your data. This creates a problem: if a producer
changes the message format (e.g., renames a field, removes a field, changes a type),
consumers that depend on the old format will break.

**Schema Registry** solves this by acting as a central authority for message formats.
It stores versioned schemas (Avro, Protobuf, or JSON Schema) and enforces
compatibility rules so that producers and consumers can evolve independently without
breaking each other.

**How it works:**

1. A producer registers a schema (e.g., "a User has fields: name, email, age") with
   Schema Registry before sending messages.
2. Schema Registry assigns the schema an ID and stores it.
3. The producer embeds the schema ID in each message (just a few extra bytes), then
   sends the message to Kafka.
4. When a consumer reads the message, it uses the schema ID to fetch the schema from
   Schema Registry and deserialize the data correctly.
5. If a producer tries to register a new version of the schema that would break
   existing consumers (e.g., removing a required field), Schema Registry **rejects
   it** based on the configured compatibility rules.

**Without Schema Registry:**

```python
# Producer sends a dict as JSON — no contract, no validation
producer.produce("users", value=json.dumps({"name": "Alice", "age": 30}).encode())

# Later, producer changes the format — removes "age", adds "email"
producer.produce("users", value=json.dumps({"name": "Bob", "email": "bob@example.com"}).encode())

# Consumer expects "age" field — breaks at runtime with a KeyError
user = json.loads(msg.value())
print(user["age"])  # KeyError!
```

**With Schema Registry:**

```python
# Schema is defined and registered — all messages must conform to it
schema_str = """
{
  "type": "record",
  "name": "User",
  "fields": [
    {"name": "name", "type": "string"},
    {"name": "email", "type": "string"},
    {"name": "age", "type": "int"}
  ]
}
"""

# If a producer tries to send a message missing "age", serialization fails
# immediately — the bad data never reaches Kafka
# If a producer tries to register a new schema that removes "age" without
# a default value, Schema Registry rejects it before any messages are sent
```

**When you don't need it:** For learning, prototyping, or simple projects where you
control both producer and consumer code. JSON serialization is fine for these cases.

**When you do need it:** In production with multiple teams or services writing to and
reading from the same topics. Schema Registry prevents one team's changes from
silently breaking another team's consumers.

#### The Schema Registry REST API

Schema Registry exposes a REST API at `http://localhost:8081`. When you visit it in
your browser and see `{}`, that is the expected response — it means the server is
running but you have not registered any schemas yet. Here are some useful endpoints:

| Endpoint | Description |
|---|---|
| `GET http://localhost:8081/subjects` | List all registered subjects (schemas). Returns `[]` when empty. |
| `GET http://localhost:8081/subjects/<name>/versions` | List all versions of a schema |
| `GET http://localhost:8081/schemas/ids/<id>` | Get a schema by its numeric ID |
| `GET http://localhost:8081/config` | View the global compatibility setting |

Try it now:

```bash
curl http://localhost:8081/subjects
```

This will return `[]` — an empty list, because no schemas have been registered yet.
Schemas get registered automatically when you use a schema-aware serializer (covered
in Module 7 and Module 12), or you can register them manually via the API.

#### Setup Steps

**Step 1: Add the `schema-registry` service to your `docker-compose.yml`**

Paste this block at the bottom of your `services:` section (at the same indentation level as `kafka` and `kafka-ui`):

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

**Step 2: Connect Schema Registry to Kafka UI**

In your `kafka-ui` service, add the `KAFKA_CLUSTERS_0_SCHEMAREGISTRY` line **inside the `environment:` block** and add `schema-registry` to `depends_on`. The full `kafka-ui` service should look like this:

```yaml
  kafka-ui:
    image: provectuslabs/kafka-ui:latest
    container_name: kafka-ui
    ports:
      - "8080:8080"
    environment:
      KAFKA_CLUSTERS_0_NAME: local
      KAFKA_CLUSTERS_0_BOOTSTRAPSERVERS: kafka:9094
      KAFKA_CLUSTERS_0_SCHEMAREGISTRY: http://schema-registry:8081
    depends_on:
      - kafka
      - schema-registry
```

> **Important:** The `KAFKA_CLUSTERS_0_SCHEMAREGISTRY` line must be indented under
> `environment:`, not placed at the service level. Placing it outside `environment:`
> causes a Docker Compose validation error:
> `additional properties 'KAFKA_CLUSTERS_0_SCHEMAREGISTRY' not allowed`.

**Step 3: Start the updated stack**

```bash
docker compose up -d
```

**Step 4: Verify Schema Registry is running**

```bash
docker compose ps
```

All three containers (`kafka`, `kafka-ui`, `schema-registry`) should show as running.

**Step 5: Verify Schema Registry is responding**

```bash
curl http://localhost:8081/subjects
```

This should return `[]` (empty list — no schemas registered yet). If you visit
`http://localhost:8081` in your browser, you will see `{}` — this is normal and means
the server is running. Schemas will appear here once you start using schema-aware
serializers (covered in Module 7 and Module 12).

You should also see a "Schema Registry" section in Kafka UI at `http://localhost:8080`.

---

### 2.6 Multi-Broker Setup (3 Brokers)

For a more realistic local cluster, use three brokers. This is a **completely
separate `docker-compose.yml`** — not a modification of the single-broker file.
If you want to try this, stop your current single-broker stack first
(`docker compose down`), then create a new directory and file for this setup.

Below is the **complete `docker-compose.yml`** with all services included
(3 brokers, Kafka UI, and Schema Registry):

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
      KAFKA_CLUSTERS_0_SCHEMAREGISTRY: http://schema-registry:8081
    depends_on:
      - kafka-1
      - kafka-2
      - kafka-3
      - schema-registry

  schema-registry:
    image: confluentinc/cp-schema-registry:7.6.0
    container_name: schema-registry
    ports:
      - "8081:8081"
    environment:
      SCHEMA_REGISTRY_HOST_NAME: schema-registry
      SCHEMA_REGISTRY_KAFKASTORE_BOOTSTRAP_SERVERS: kafka-1:9094,kafka-2:9094,kafka-3:9094
      SCHEMA_REGISTRY_LISTENERS: http://0.0.0.0:8081
    depends_on:
      - kafka-1
      - kafka-2
      - kafka-3
```

#### Step-by-step: Running and testing the multi-broker cluster

**Step 1: Stop your single-broker stack (if running)**

If you still have the single-broker setup running from earlier sections, stop it
first to free up ports:

```bash
cd /path/to/your/single-broker-directory
docker compose down
```

**Step 2: Create a new directory and file for the multi-broker setup**

```bash
mkdir kafka-multi-broker && cd kafka-multi-broker
```

Copy the full `docker-compose.yml` above into this directory.

**Step 3: Start the cluster**

```bash
docker compose up -d
```

**Step 4: Verify all containers are running**

```bash
docker compose ps
```

You should see 5 containers running: `kafka-1`, `kafka-2`, `kafka-3`, `kafka-ui`,
and `schema-registry`. Wait 15-20 seconds for all brokers to finish electing a
controller and forming the cluster.

**Step 5: Open Kafka UI and check the cluster**

Open `http://localhost:8080` in your browser. You should see:
- **Online: 1 cluster**
- **Brokers: 3**

Click on "Brokers" to see all three listed with their IDs (1, 2, 3).

> **Internal vs external bootstrap server for CLI commands:**
>
> There are two ways to run CLI commands against the cluster:
> - **From inside a container** (`docker exec kafka-1 ...`): use the **internal**
>   listener address `kafka-1:9094`. Inside a container, `localhost` refers to that
>   container itself, not your Mac. Using `localhost:9092` will cause a warning like
>   `Connection to node 3 (localhost/127.0.0.1:9096) could not be established` because
>   the other brokers are not reachable at `localhost` from inside `kafka-1`. The
>   command may still succeed (the topic gets created), but the warning is misleading.
> - **From your Mac terminal** (without `docker exec`): use `localhost:9092` as usual.
>
> All CLI steps below use `kafka-1:9094` to avoid this warning.

**Step 6: Create a topic with replication**

```bash
docker exec kafka-1 /opt/kafka/bin/kafka-topics.sh --create \
  --topic replicated-topic \
  --bootstrap-server kafka-1:9094 \
  --partitions 3 \
  --replication-factor 3
```

The `--replication-factor 3` means each partition's data is copied to all 3 brokers.
If any single broker goes down, the other two still have a complete copy of the data
and can continue serving reads and writes.

**Step 7: Verify the topic's replication**

```bash
docker exec kafka-1 /opt/kafka/bin/kafka-topics.sh --describe \
  --topic replicated-topic \
  --bootstrap-server kafka-1:9094
```

You should see output like:

```
Topic: replicated-topic  Partition: 0  Leader: 1  Replicas: 1,2,3  Isr: 1,2,3
Topic: replicated-topic  Partition: 1  Leader: 2  Replicas: 2,3,1  Isr: 2,3,1
Topic: replicated-topic  Partition: 2  Leader: 3  Replicas: 3,1,2  Isr: 3,1,2
```

- **Leader** — the broker currently handling reads/writes for that partition
- **Replicas** — all brokers that hold a copy of that partition
- **Isr** (In-Sync Replicas) — the brokers whose copies are fully up to date.
  All 3 should appear here when the cluster is healthy.

You can also see this in Kafka UI by clicking on "Topics" > "replicated-topic".

**Step 8: Produce some test messages**

```bash
docker exec -it kafka-1 /opt/kafka/bin/kafka-console-producer.sh \
  --topic replicated-topic \
  --bootstrap-server kafka-1:9094
```

Type a few messages:

```
Message before broker failure
Another important message
Third message for testing
```

Press `Ctrl+C` to exit.

**Step 9: Simulate a broker failure**

Now kill one of the brokers to see how replication protects your data:

```bash
docker compose stop kafka-2
```

**Step 10: Check what happened to the topic**

```bash
docker exec kafka-1 /opt/kafka/bin/kafka-topics.sh --describe \
  --topic replicated-topic \
  --bootstrap-server kafka-1:9094
```

Compare the output to Step 7. You should see:
- **Isr** now lists only 2 brokers instead of 3 (broker 2 is missing)
- Any partition that had broker 2 as the **Leader** has automatically elected a new
  leader from the remaining in-sync replicas
- The cluster is still fully operational

You can also see this in Kafka UI — the "Brokers" page should show 2 online, and
the topic detail page will show the updated ISR.

**Step 11: Verify your data is still accessible**

```bash
docker exec -it kafka-1 /opt/kafka/bin/kafka-console-consumer.sh \
  --topic replicated-topic \
  --bootstrap-server kafka-1:9094 \
  --from-beginning
```

All three messages you produced in Step 8 should appear — nothing was lost, even
though a broker is down. Press `Ctrl+C` to exit.

**Step 12: Produce new messages while a broker is down**

```bash
docker exec -it kafka-1 /opt/kafka/bin/kafka-console-producer.sh \
  --topic replicated-topic \
  --bootstrap-server kafka-1:9094
```

```
Message written while broker 2 is down
```

Press `Ctrl+C`. The cluster continues to accept writes with 2 out of 3 brokers
(because `KAFKA_TRANSACTION_STATE_LOG_MIN_ISR: 2`).

**Step 13: Bring the broker back**

```bash
docker compose start kafka-2
```

Wait 10 seconds, then describe the topic again:

```bash
docker exec kafka-1 /opt/kafka/bin/kafka-topics.sh --describe \
  --topic replicated-topic \
  --bootstrap-server kafka-1:9094
```

Broker 2 should reappear in the **Isr** list — it automatically caught up on the
messages it missed while it was down.

**Step 14: Verify the recovered broker has all messages**

```bash
docker exec -it kafka-2 /opt/kafka/bin/kafka-console-consumer.sh \
  --topic replicated-topic \
  --bootstrap-server kafka-2:9094 \
  --from-beginning
```

All four messages (3 from before the failure + 1 written during the failure) should
appear. The broker fully caught up.

#### Connecting from Python

Your Python clients should list all three brokers so they can failover automatically
if one is unreachable:

```python
bootstrap_servers = "localhost:9092,localhost:9095,localhost:9096"
```

> **Note:** You only need one reachable broker for the initial connection — Kafka
> clients automatically discover all other brokers in the cluster. Listing all three
> is a best practice so the client can still connect even if the first broker in the
> list happens to be down.

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

Make sure your Docker Kafka stack is running (`docker compose up -d`) and that the
topic `my-first-topic` exists (see section 2.3 Step 1). Then create `test_kafka.py`:

#### Understanding bootstrap.servers

Before looking at the code, it is important to understand the `bootstrap.servers`
setting — it is the first thing every Kafka client needs.

`bootstrap.servers` is the entry point your Python client uses to make its **first**
connection to the cluster. It does **not** need to list every broker. Here is what
happens:

1. The client connects to any one broker in the list.
2. That broker responds with the **full cluster metadata** — the address of every
   broker, every topic, and which broker is the leader for each partition.
3. From that point on, the client talks directly to the right broker for each
   operation. The bootstrap address is no longer used.

For the **multi-broker setup** (section 2.6), the three brokers are mapped to your
Mac at:

| Broker | Mac address |
|---|---|
| kafka-1 | `localhost:9092` |
| kafka-2 | `localhost:9095` |
| kafka-3 | `localhost:9096` |

Any single address works as the bootstrap server. However, listing all three is best
practice — if the first one happens to be down, the client tries the next:

```python
BOOTSTRAP_SERVERS = "localhost:9092,localhost:9095,localhost:9096"
```

For the **single-broker setup** (section 2.2), use:

```python
BOOTSTRAP_SERVERS = "localhost:9092"
```

> There is no single "leader" broker that clients must connect to. Leadership is a
> per-partition concept — each partition has one leader broker that handles its reads
> and writes. The client learns this from the metadata response and routes requests
> automatically. From the client's perspective, any broker can answer a bootstrap
> request.

#### The code

```python
from confluent_kafka import Producer, Consumer

# Entry point to the Kafka cluster — see explanation above.
# List all brokers for resilience; the client only needs one to be reachable.
BOOTSTRAP_SERVERS = "localhost:9092,localhost:9095,localhost:9096"

TOPIC = "my-first-topic"


# =============================================================================
# PRODUCER
# =============================================================================
#
# producer.produce() does NOT immediately send the message to the broker.
# It places the message into a local in-memory buffer. The client batches
# messages and sends them in the background for efficiency.
#
# producer.flush() blocks until all buffered messages have been delivered to
# the broker. Always call flush() before your program exits — without it,
# buffered messages can be silently lost.

producer = Producer({"bootstrap.servers": BOOTSTRAP_SERVERS})

for i in range(5):
    producer.produce(
        TOPIC,
        value=f"Message {i}".encode("utf-8"),  # Kafka messages are raw bytes
    )
    print(f"Buffered: Message {i}")

print("Flushing — waiting for all messages to reach the broker...")
producer.flush()
print("All messages delivered.\n")


# =============================================================================
# CONSUMER
# =============================================================================
#
# group.id — identifies this consumer as part of a consumer group. Kafka uses
#   this to track which messages have already been processed (via offsets).
#   If you run this script twice with the same group.id, the second run will
#   NOT re-read the messages — Kafka remembers the group already read them.
#   Change the group.id (e.g. "my-test-group-2") to read from the beginning again.
#
# auto.offset.reset — what to do when this group has no committed offset yet
#   (i.e. the first time this group.id reads this topic):
#   "earliest" — start from the very first message ever written to the topic.
#   "latest"   — start from now, only receive messages that arrive after the
#                consumer starts.

consumer = Consumer({
    "bootstrap.servers": BOOTSTRAP_SERVERS,
    "group.id": "my-test-group",
    "auto.offset.reset": "earliest",
})

# subscribe() registers interest in a topic and triggers a rebalance — Kafka
# assigns partitions of the topic to this consumer. The rebalance happens
# asynchronously, so the first few poll() calls may return None even though
# messages exist. This is normal, not an error.
consumer.subscribe([TOPIC])
print(f"Subscribed to '{TOPIC}'. Waiting for partition assignment...\n")

# poll(timeout) asks the broker for new messages and waits up to `timeout`
# seconds. It returns a Message object, or None if nothing arrived.
#
# None does NOT mean the topic is empty — during the initial rebalance the
# broker returns None for a brief period. We use empty_polls to wait through
# the rebalance and only stop after 3 consecutive empty polls, which reliably
# means all available messages have been read.
empty_polls = 0
while empty_polls < 3:
    msg = consumer.poll(timeout=2.0)

    if msg is None:
        empty_polls += 1
        continue

    empty_polls = 0  # reset — we are actively receiving messages

    if msg.error():
        print(f"Error: {msg.error()}")
    else:
        # msg.partition() — which partition (0, 1, or 2) this message came from
        # msg.offset()    — position within that partition (starts at 0,
        #                   increments by 1 for each message)
        print(
            f"Received: {msg.value().decode('utf-8')}"
            f"  [topic={msg.topic()}"
            f"  partition={msg.partition()}"
            f"  offset={msg.offset()}]"
        )

# Always close the consumer when done. This commits pending offsets and sends
# a "leave group" signal so Kafka can immediately reassign partitions.
consumer.close()
print("\nConsumer closed.")
```

Run it:

```bash
python3 test_kafka.py
```

Expected output:

```
Buffered: Message 0
Buffered: Message 1
Buffered: Message 2
Buffered: Message 3
Buffered: Message 4
Flushing — waiting for all messages to reach the broker...
All messages delivered.

Subscribed to 'my-first-topic'. Waiting for partition assignment...

Received: Message 0  [topic=my-first-topic  partition=1  offset=0]
Received: Message 1  [topic=my-first-topic  partition=0  offset=0]
Received: Message 2  [topic=my-first-topic  partition=2  offset=0]
Received: Message 3  [topic=my-first-topic  partition=1  offset=1]
Received: Message 4  [topic=my-first-topic  partition=0  offset=1]

Consumer closed.
```

> The messages will not appear in order (0, 1, 2, 3, 4). Kafka only guarantees order
> **within a partition**. Since the topic has 3 partitions and no message key is set,
> messages are distributed across partitions using a round-robin strategy. The
> consumer reads partitions independently, so the final order depends on which
> partition the consumer polls first. This is expected and correct behaviour.

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
