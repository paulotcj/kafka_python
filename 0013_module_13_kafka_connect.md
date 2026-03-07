# Module 13 — Kafka Connect

## Overview

Kafka Connect is a framework for moving data **between Kafka and external systems** without writing custom producer/consumer code. It runs as a separate process (or cluster of processes) and is configured through a REST API.

```
External System  --(Source Connector)-->  Kafka  --(Sink Connector)-->  External System
   (Database)                                                              (S3, Elastic)
```

**Source connectors** read from external systems and write to Kafka topics.
**Sink connectors** read from Kafka topics and write to external systems.

As a Python developer, Kafka Connect replaces the need to write ingestion consumers and data-sink producers for standard integrations. You write Python code when your transformation or source is too custom for a connector; otherwise, Connect handles it.

---

## 13.1 Why Kafka Connect?

Without Connect, moving data from a PostgreSQL table to Kafka requires:
- A Python script that queries Postgres
- A producer that writes to Kafka
- Offset tracking to know which rows were already sent
- Error handling, retries, restart recovery
- Deployment and monitoring

With Connect (using the Debezium PostgreSQL connector), this is a JSON configuration file.

**Use Connect when:**
- Standard integration exists (database, S3, Elasticsearch, HTTP)
- You need CDC (Change Data Capture) from a database
- You need to sink topic data to a data warehouse or object store

**Write Python producers/consumers when:**
- The source/destination has no connector
- Complex business logic is needed during ingestion
- You need fine-grained control over exactly-once semantics

---

## 13.2 Running Kafka Connect with Docker

Add Kafka Connect to your existing `docker-compose.yml`:

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

  kafka-connect:
    image: confluentinc/cp-kafka-connect:7.6.0
    container_name: kafka-connect
    ports:
      - "8083:8083"
    environment:
      CONNECT_BOOTSTRAP_SERVERS: kafka:9094
      CONNECT_REST_ADVERTISED_HOST_NAME: kafka-connect
      CONNECT_REST_PORT: 8083
      CONNECT_GROUP_ID: kafka-connect-group
      # Internal topics Connect uses to store connector state and offsets
      CONNECT_CONFIG_STORAGE_TOPIC: _connect-configs
      CONNECT_OFFSET_STORAGE_TOPIC: _connect-offsets
      CONNECT_STATUS_STORAGE_TOPIC: _connect-status
      CONNECT_CONFIG_STORAGE_REPLICATION_FACTOR: 1
      CONNECT_OFFSET_STORAGE_REPLICATION_FACTOR: 1
      CONNECT_STATUS_STORAGE_REPLICATION_FACTOR: 1
      CONNECT_KEY_CONVERTER: org.apache.kafka.connect.storage.StringConverter
      CONNECT_VALUE_CONVERTER: org.apache.kafka.connect.json.JsonConverter
      CONNECT_VALUE_CONVERTER_SCHEMAS_ENABLE: "false"
      CONNECT_PLUGIN_PATH: /usr/share/java,/usr/share/confluent-hub-components
    depends_on:
      - kafka

  kafka-ui:
    image: provectuslabs/kafka-ui:latest
    container_name: kafka-ui
    ports:
      - "8080:8080"
    environment:
      KAFKA_CLUSTERS_0_NAME: local
      KAFKA_CLUSTERS_0_BOOTSTRAPSERVERS: kafka:9094
      KAFKA_CLUSTERS_0_KAFKACONNECT_0_NAME: connect
      KAFKA_CLUSTERS_0_KAFKACONNECT_0_ADDRESS: http://kafka-connect:8083
    depends_on:
      - kafka
      - kafka-connect
```

Start the stack:
```bash
docker compose up -d
```

Wait about 30 seconds for Connect to start, then verify:
```bash
curl http://localhost:8083/
# {"version":"7.6.0-ce","commit":"...","kafka_cluster_id":"..."}

curl http://localhost:8083/connectors
# []
```

---

## 13.3 Kafka Connect REST API

All connector management happens through the REST API. You can use `curl` or the Python `requests` library.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/connectors` | List all connectors |
| `POST` | `/connectors` | Create a connector |
| `GET` | `/connectors/{name}` | Get connector config |
| `PUT` | `/connectors/{name}/config` | Update connector config |
| `GET` | `/connectors/{name}/status` | Get connector + task status |
| `POST` | `/connectors/{name}/restart` | Restart a connector |
| `PUT` | `/connectors/{name}/pause` | Pause a connector |
| `PUT` | `/connectors/{name}/resume` | Resume a connector |
| `DELETE` | `/connectors/{name}` | Delete a connector |
| `GET` | `/connector-plugins` | List available connector plugins |

### Python REST Client for Kafka Connect

```python
# connect_client.py

import json
import requests
from typing import Optional

class KafkaConnectClient:
    def __init__(self, base_url: str = "http://localhost:8083"):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    def list_connectors(self) -> list[str]:
        r = self.session.get(f"{self.base_url}/connectors")
        r.raise_for_status()
        return r.json()

    def get_connector(self, name: str) -> dict:
        r = self.session.get(f"{self.base_url}/connectors/{name}")
        r.raise_for_status()
        return r.json()

    def get_status(self, name: str) -> dict:
        r = self.session.get(f"{self.base_url}/connectors/{name}/status")
        r.raise_for_status()
        return r.json()

    def create_connector(self, name: str, config: dict) -> dict:
        payload = {"name": name, "config": config}
        r = self.session.post(f"{self.base_url}/connectors", data=json.dumps(payload))
        r.raise_for_status()
        return r.json()

    def update_connector(self, name: str, config: dict) -> dict:
        r = self.session.put(
            f"{self.base_url}/connectors/{name}/config",
            data=json.dumps(config),
        )
        r.raise_for_status()
        return r.json()

    def delete_connector(self, name: str):
        r = self.session.delete(f"{self.base_url}/connectors/{name}")
        r.raise_for_status()

    def restart_connector(self, name: str):
        r = self.session.post(f"{self.base_url}/connectors/{name}/restart")
        r.raise_for_status()

    def pause_connector(self, name: str):
        self.session.put(f"{self.base_url}/connectors/{name}/pause").raise_for_status()

    def resume_connector(self, name: str):
        self.session.put(f"{self.base_url}/connectors/{name}/resume").raise_for_status()

    def list_plugins(self) -> list[dict]:
        r = self.session.get(f"{self.base_url}/connector-plugins")
        r.raise_for_status()
        return r.json()

    def print_all_statuses(self):
        for name in self.list_connectors():
            status = self.get_status(name)
            connector_state = status["connector"]["state"]
            tasks = status.get("tasks", [])
            task_states = [t["state"] for t in tasks]
            print(f"  {name}: {connector_state} | tasks: {task_states}")


if __name__ == "__main__":
    client = KafkaConnectClient()
    print("Available plugins:")
    for plugin in client.list_plugins():
        print(f"  {plugin['class']}")
    print("\nConnectors:")
    client.print_all_statuses()
```

---

## 13.4 File Source Connector (Built-In)

The FileStreamSource connector reads lines from a file and writes each line as a Kafka message. It is built into the Connect distribution — no installation needed. It is mainly useful for learning and testing.

```python
# create_file_source.py

from connect_client import KafkaConnectClient

client = KafkaConnectClient()

# Create a sample file inside the container
import subprocess
subprocess.run([
    "docker", "exec", "kafka-connect",
    "bash", "-c", "echo -e 'line one\nline two\nline three' > /tmp/test-input.txt"
])

connector_config = {
    "connector.class": "org.apache.kafka.connect.file.FileStreamSource",
    "tasks.max": "1",
    "file": "/tmp/test-input.txt",
    "topic": "file-lines",
}

result = client.create_connector("file-source-demo", connector_config)
print(f"Created connector: {result['name']}")
```

After a few seconds:
```bash
# Check status
curl http://localhost:8083/connectors/file-source-demo/status

# Read the messages that were produced
docker exec -it kafka /opt/kafka/bin/kafka-console-consumer.sh \
  --topic file-lines \
  --bootstrap-server localhost:9092 \
  --from-beginning
```

---

## 13.5 Installing Connectors

Most connectors are not included by default. Install them into the Connect container using `confluent-hub`:

```bash
# Install the Debezium PostgreSQL connector
docker exec kafka-connect confluent-hub install debezium/debezium-connector-postgresql:latest --no-prompt

# Install the S3 Sink connector
docker exec kafka-connect confluent-hub install confluentinc/kafka-connect-s3:latest --no-prompt

# Install the Elasticsearch Sink connector
docker exec kafka-connect confluent-hub install confluentinc/kafka-connect-elasticsearch:latest --no-prompt

# Restart Connect to load the new plugins
docker compose restart kafka-connect
```

Verify the connector is available:
```bash
curl http://localhost:8083/connector-plugins | python3 -m json.tool | grep "debezium"
```

---

## 13.6 Debezium — Change Data Capture from PostgreSQL

Debezium captures every INSERT, UPDATE, and DELETE from a database's transaction log and streams them to Kafka as events. This is called **Change Data Capture (CDC)**.

### Add PostgreSQL to Docker Compose

```yaml
  postgres:
    image: postgres:16
    container_name: postgres
    ports:
      - "5432:5432"
    environment:
      POSTGRES_DB: shop
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
    command: ["postgres", "-c", "wal_level=logical"]  # required for CDC
```

### Create Test Data

```bash
docker exec -it postgres psql -U postgres -d shop -c "
CREATE TABLE orders (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(50),
    amount DECIMAL(10,2),
    status VARCHAR(20) DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT NOW()
);
"
```

### Create the Debezium Connector

```python
from connect_client import KafkaConnectClient

client = KafkaConnectClient()

result = client.create_connector("postgres-orders-cdc", {
    "connector.class": "io.debezium.connector.postgresql.PostgresConnector",
    "tasks.max": "1",

    # Database connection
    "database.hostname": "postgres",
    "database.port": "5432",
    "database.user": "postgres",
    "database.password": "postgres",
    "database.dbname": "shop",
    "database.server.name": "shop-db",  # prefix for topic names

    # Which tables to capture
    "table.include.list": "public.orders",

    # Snapshot mode: "initial" reads existing rows first, then streams changes
    "snapshot.mode": "initial",

    # Topic naming: {server.name}.{schema}.{table}
    # This connector will produce to: shop-db.public.orders
    "topic.prefix": "shop-db",

    # Publication name in PostgreSQL
    "publication.name": "dbz_publication",

    # Slot name for PostgreSQL replication
    "slot.name": "debezium_slot",
})

print(f"Connector created: {result['name']}")
```

### Observe CDC Events

```bash
docker exec -it kafka /opt/kafka/bin/kafka-console-consumer.sh \
  --topic shop-db.public.orders \
  --bootstrap-server localhost:9092 \
  --from-beginning
```

Now insert/update rows and watch events flow:

```bash
docker exec -it postgres psql -U postgres -d shop -c "
INSERT INTO orders (user_id, amount) VALUES ('user-1', 99.99);
INSERT INTO orders (user_id, amount) VALUES ('user-2', 149.00);
UPDATE orders SET status='paid' WHERE id=1;
DELETE FROM orders WHERE id=2;
"
```

Each operation produces a Kafka message with this structure:

```json
{
  "before": null,
  "after": {
    "id": 1, "user_id": "user-1", "amount": 99.99, "status": "pending"
  },
  "op": "c",
  "ts_ms": 1700000000000,
  "source": { "table": "orders", "db": "shop" }
}
```

- `op: "c"` = create (INSERT)
- `op: "u"` = update (UPDATE)
- `op: "d"` = delete (DELETE), `after` is null, `before` has the deleted row
- `op: "r"` = read (snapshot)

### Consuming CDC Events in Python

```python
import json
from confluent_kafka import Consumer

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "orders-cdc-consumer",
    "auto.offset.reset": "earliest",
})
consumer.subscribe(["shop-db.public.orders"])

print("Listening for database changes...")
empty = 0
while empty < 5:
    msg = consumer.poll(timeout=2.0)
    if msg is None:
        empty += 1
        continue
    empty = 0
    if msg.error():
        continue

    event = json.loads(msg.value().decode("utf-8"))
    op = event.get("payload", event).get("op")  # Debezium wraps in payload
    before = event.get("payload", event).get("before")
    after = event.get("payload", event).get("after")

    if op == "c":
        print(f"INSERT: {after}")
    elif op == "u":
        print(f"UPDATE: {before} -> {after}")
    elif op == "d":
        print(f"DELETE: {before}")
    elif op == "r":
        print(f"SNAPSHOT: {after}")

consumer.close()
```

---

## 13.7 Single Message Transforms (SMTs)

SMTs are lightweight transformations applied inside the connector pipeline — no extra service needed. They run between reading from the source and writing to Kafka (for source connectors), or between reading from Kafka and writing to the sink (for sink connectors).

Common SMTs:

| SMT Class | What It Does |
|---|---|
| `ReplaceField` | Add, remove, or rename fields |
| `MaskField` | Replace field value with a null or fixed value (PII masking) |
| `TimestampConverter` | Convert timestamp formats |
| `ExtractField` | Extract a nested field to the top level |
| `ValueToKey` | Set the message key from a value field |
| `HoistField` | Wrap the entire value in a named field |
| `Filter` | Drop messages that match a condition |
| `InsertField` | Add a static or computed field |

### Example: Mask Sensitive Fields and Add Metadata

```python
client.create_connector("postgres-masked", {
    "connector.class": "io.debezium.connector.postgresql.PostgresConnector",
    "tasks.max": "1",
    "database.hostname": "postgres",
    "database.port": "5432",
    "database.user": "postgres",
    "database.password": "postgres",
    "database.dbname": "shop",
    "topic.prefix": "shop-db",
    "table.include.list": "public.orders",
    "snapshot.mode": "initial",

    # Apply a chain of SMTs
    "transforms": "maskPII,addIngestionTime",

    # SMT 1: mask the user_id field (GDPR compliance)
    "transforms.maskPII.type": "org.apache.kafka.connect.transforms.MaskField$Value",
    "transforms.maskPII.fields": "user_id",

    # SMT 2: add an ingestion timestamp field
    "transforms.addIngestionTime.type": "org.apache.kafka.connect.transforms.InsertField$Value",
    "transforms.addIngestionTime.timestamp.field": "ingested_at",
})
```

---

## 13.8 Managing Connectors from Python

A complete management script:

```python
# manage_connectors.py

import sys
from connect_client import KafkaConnectClient

def main():
    client = KafkaConnectClient()

    command = sys.argv[1] if len(sys.argv) > 1 else "status"

    if command == "status":
        connectors = client.list_connectors()
        if not connectors:
            print("No connectors running.")
            return
        print(f"{'NAME':<40} {'STATE':<15} TASKS")
        print("-" * 70)
        for name in connectors:
            status = client.get_status(name)
            state = status["connector"]["state"]
            tasks = [f"{t['id']}:{t['state']}" for t in status.get("tasks", [])]
            print(f"{name:<40} {state:<15} {', '.join(tasks)}")

    elif command == "restart-failed":
        for name in client.list_connectors():
            status = client.get_status(name)
            connector_state = status["connector"]["state"]
            failed_tasks = [t for t in status.get("tasks", []) if t["state"] == "FAILED"]
            if connector_state == "FAILED" or failed_tasks:
                print(f"Restarting {name} (state={connector_state}, failed_tasks={len(failed_tasks)})")
                client.restart_connector(name)

    elif command == "delete" and len(sys.argv) > 2:
        name = sys.argv[2]
        client.delete_connector(name)
        print(f"Deleted connector: {name}")

    elif command == "plugins":
        for plugin in client.list_plugins():
            print(plugin["class"])

if __name__ == "__main__":
    main()
```

```bash
python3 manage_connectors.py status
python3 manage_connectors.py restart-failed
python3 manage_connectors.py plugins
python3 manage_connectors.py delete file-source-demo
```
