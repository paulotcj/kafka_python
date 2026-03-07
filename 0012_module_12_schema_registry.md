# Module 12 — Schema Registry

## Overview

Kafka messages are raw bytes. The broker stores them without understanding their structure. This works fine until a team changes the message format — adds a field, renames a field, changes a type — and breaks every consumer that depends on the old format.

**Schema Registry** is a service that sits alongside Kafka and solves this:

1. **Centralized schema storage** — producers register their message schema once; consumers fetch it by ID.
2. **Schema enforcement** — producers that try to register a schema incompatible with the existing version get rejected before any messages are produced.
3. **Compact wire format** — instead of embedding the full schema in every message, only a 4-byte schema ID is embedded. Consumers look up the schema from the registry.

This module assumes you have the Confluent Schema Registry running. See Module 2, section 2.5 for the Docker Compose setup.

```bash
# Verify Schema Registry is running
curl http://localhost:8081/subjects
# Expected output: [] (empty list if no schemas registered yet)
```

```bash
pip install confluent-kafka[avro] fastavro
# For Protobuf support:
pip install confluent-kafka[protobuf] grpcio-tools
# For JSON Schema support:
pip install confluent-kafka[json] jsonschema
```

---

## 12.1 Architecture and Concepts

### Wire Format

Every message produced with a Schema Registry serializer has this binary layout:

```
Byte 0:   Magic byte (always 0x00)
Bytes 1-4: Schema ID (4-byte big-endian integer)
Bytes 5+: Encoded message payload (Avro/Protobuf/JSON bytes)
```

The consumer reads the schema ID from bytes 1–4, fetches the schema from the registry (cached after the first fetch), and uses it to decode bytes 5 onwards.

This is called the **Confluent wire format**. Both producer and consumer must use it — you cannot mix Schema Registry serializers on one side with raw deserializers on the other.

### Subjects

A **subject** is the name under which a schema is registered. By default:

| Subject | Schema for |
|---|---|
| `orders-value` | Values in the `orders` topic |
| `orders-key` | Keys in the `orders` topic |

Subject names are configurable. The subject name strategy determines how the subject name is derived (covered in section 12.9).

### Schema Versions

Every time you register a new schema under a subject, it gets a new **version** (1, 2, 3, ...) and a globally unique **schema ID**. The schema ID is what gets embedded in messages.

```
Subject: orders-value
  Version 1: schema ID 1  { "name": "Order", "fields": ["order_id", "amount"] }
  Version 2: schema ID 3  { "name": "Order", "fields": ["order_id", "amount", "currency"] }
  Version 3: schema ID 7  { ... }
```

Schema IDs are assigned sequentially across all subjects in the registry, so there may be gaps within a subject (IDs 2, 4, 5, 6 belong to other subjects).

---

## 12.2 Schema Registry REST API

The Schema Registry exposes a REST API. This is useful for inspection, scripting, and CI/CD pipelines.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/subjects` | List all subjects |
| `GET` | `/subjects/{subject}/versions` | List versions for a subject |
| `GET` | `/subjects/{subject}/versions/{version}` | Get a specific version |
| `GET` | `/subjects/{subject}/versions/latest` | Get the latest version |
| `POST` | `/subjects/{subject}/versions` | Register a new schema |
| `POST` | `/subjects/{subject}` | Check if schema is already registered |
| `GET` | `/schemas/ids/{id}` | Get schema by global ID |
| `GET` | `/config/{subject}` | Get compatibility mode for a subject |
| `PUT` | `/config/{subject}` | Set compatibility mode for a subject |
| `DELETE` | `/subjects/{subject}` | Delete all versions of a subject |
| `DELETE` | `/subjects/{subject}/versions/{version}` | Delete a specific version |

### Quick REST Examples

```bash
# List all subjects
curl http://localhost:8081/subjects

# Register an Avro schema
curl -X POST http://localhost:8081/subjects/orders-value/versions \
  -H "Content-Type: application/vnd.schemaregistry.v1+json" \
  -d '{
    "schema": "{\"type\":\"record\",\"name\":\"Order\",\"fields\":[{\"name\":\"order_id\",\"type\":\"string\"},{\"name\":\"amount\",\"type\":\"double\"}]}"
  }'
# Returns: {"id": 1}

# Get the latest schema for a subject
curl http://localhost:8081/subjects/orders-value/versions/latest

# Get schema by global ID
curl http://localhost:8081/schemas/ids/1

# Check compatibility before registering
curl -X POST http://localhost:8081/compatibility/subjects/orders-value/versions/latest \
  -H "Content-Type: application/vnd.schemaregistry.v1+json" \
  -d '{"schema": "<new schema JSON>"}'
# Returns: {"is_compatible": true}

# Get compatibility mode
curl http://localhost:8081/config/orders-value

# Set compatibility mode
curl -X PUT http://localhost:8081/config/orders-value \
  -H "Content-Type: application/vnd.schemaregistry.v1+json" \
  -d '{"compatibility": "FULL"}'
```

---

## 12.3 Compatibility Modes

Compatibility modes define which schema changes are allowed when a new version is registered.

| Mode | Guarantee | Who can update first |
|---|---|---|
| `BACKWARD` | New schema can read data written by old schema | Update consumers first |
| `FORWARD` | Old schema can read data written by new schema | Update producers first |
| `FULL` | Both backward and forward compatible | Either order |
| `BACKWARD_TRANSITIVE` | Backward compatible with all previous versions | Update consumers first |
| `FORWARD_TRANSITIVE` | Forward compatible with all previous versions | Update producers first |
| `FULL_TRANSITIVE` | Full compatibility with all previous versions | Either order |
| `NONE` | No compatibility check — any schema accepted | Any order (dangerous) |

### What "Can Read" Means in Practice

**BACKWARD** — a consumer using the new schema can read messages that were produced with the old schema.

```
Old schema: { order_id: string, amount: double }
New schema: { order_id: string, amount: double, currency: string (default "USD") }

Consumer with new schema reads old message (no currency field)
--> currency defaults to "USD"  -- OK, backward compatible
```

**FORWARD** — a consumer using the old schema can read messages produced with the new schema.

```
Old schema: { order_id: string, amount: double }
New schema: { order_id: string, amount: double, currency: string }

Consumer with old schema reads new message (has currency field)
--> currency is ignored  -- OK, forward compatible
```

### Safe vs Breaking Changes for Avro

| Change | BACKWARD | FORWARD | FULL |
|---|---|---|---|
| Add optional field with default | Safe | Safe | Safe |
| Remove optional field with default | Safe | Safe | Safe |
| Add required field (no default) | **BREAKS** | Safe | **BREAKS** |
| Remove required field | Safe | **BREAKS** | **BREAKS** |
| Change field type (e.g. int to long) | **BREAKS** | **BREAKS** | **BREAKS** |
| Rename field | **BREAKS** | **BREAKS** | **BREAKS** |

The safest approach: use `FULL` or `FULL_TRANSITIVE` and only add optional fields with defaults.

### Setting Compatibility Mode

```bash
# Set globally (applies to all new subjects)
curl -X PUT http://localhost:8081/config \
  -H "Content-Type: application/vnd.schemaregistry.v1+json" \
  -d '{"compatibility": "FULL"}'

# Set for a specific subject
curl -X PUT http://localhost:8081/config/orders-value \
  -H "Content-Type: application/vnd.schemaregistry.v1+json" \
  -d '{"compatibility": "BACKWARD"}'
```

---

## 12.4 Python Client Setup

```python
from confluent_kafka.schema_registry import SchemaRegistryClient

# Connect to Schema Registry
schema_registry_client = SchemaRegistryClient({
    "url": "http://localhost:8081",
    # If authentication is required:
    # "basic.auth.user.info": "username:password",
})

# List subjects
subjects = schema_registry_client.get_subjects()
print(f"Registered subjects: {subjects}")

# Get latest schema for a subject
schema = schema_registry_client.get_latest_version("orders-value")
print(f"Schema ID: {schema.schema_id}")
print(f"Schema: {schema.schema.schema_str}")

# Register a schema manually
from confluent_kafka.schema_registry import Schema
new_schema = Schema(
    '{"type":"record","name":"Order","fields":[{"name":"order_id","type":"string"}]}',
    schema_type="AVRO",
)
schema_id = schema_registry_client.register_schema("orders-value", new_schema)
print(f"Registered schema ID: {schema_id}")
```

---

## 12.5 Avro with Schema Registry

Avro is the most common format used with Schema Registry. See Module 7 for an introduction to Avro. This section focuses on the Schema Registry integration.

### Define the Schema

```python
# schemas/order.py

ORDER_SCHEMA_V1 = """
{
    "type": "record",
    "name": "Order",
    "namespace": "com.example.shop",
    "fields": [
        {"name": "order_id",   "type": "string"},
        {"name": "user_id",    "type": "string"},
        {"name": "amount",     "type": "double"},
        {"name": "status",     "type": {"type": "enum", "name": "Status",
                                        "symbols": ["pending", "paid", "shipped", "cancelled"]}}
    ]
}
"""

# V2 adds an optional 'currency' field with a default — BACKWARD compatible
ORDER_SCHEMA_V2 = """
{
    "type": "record",
    "name": "Order",
    "namespace": "com.example.shop",
    "fields": [
        {"name": "order_id",   "type": "string"},
        {"name": "user_id",    "type": "string"},
        {"name": "amount",     "type": "double"},
        {"name": "status",     "type": {"type": "enum", "name": "Status",
                                        "symbols": ["pending", "paid", "shipped", "cancelled"]}},
        {"name": "currency",   "type": "string", "default": "USD"}
    ]
}
"""
```

### Avro Producer with Auto-Registration

```python
from confluent_kafka import Producer
from confluent_kafka.schema_registry import SchemaRegistryClient, Schema
from confluent_kafka.schema_registry.avro import AvroSerializer
from confluent_kafka.serialization import SerializationContext, MessageField

BOOTSTRAP_SERVERS = "localhost:9092"
SCHEMA_REGISTRY_URL = "http://localhost:8081"
TOPIC = "orders"

ORDER_SCHEMA_STR = """
{
    "type": "record",
    "name": "Order",
    "namespace": "com.example.shop",
    "fields": [
        {"name": "order_id", "type": "string"},
        {"name": "user_id",  "type": "string"},
        {"name": "amount",   "type": "double"},
        {"name": "currency", "type": "string", "default": "USD"}
    ]
}
"""

# Schema Registry client
schema_registry_client = SchemaRegistryClient({"url": SCHEMA_REGISTRY_URL})

# AvroSerializer converts Python dicts to Avro bytes and registers the schema
# automatically on first use. Subsequent calls reuse the cached schema ID.
avro_serializer = AvroSerializer(
    schema_registry_client,
    ORDER_SCHEMA_STR,
    # Optional: provide a function to convert your domain object to a dict.
    # If omitted, the value is expected to already be a dict.
    # to_dict=lambda order, ctx: order.to_dict(),
)

producer = Producer({"bootstrap.servers": BOOTSTRAP_SERVERS})

def delivery_callback(err, msg):
    if err:
        print(f"Delivery failed: {err}")
    else:
        print(f"Delivered to {msg.topic()} partition {msg.partition()} offset {msg.offset()}")

orders = [
    {"order_id": "order-001", "user_id": "user-42", "amount": 99.99,  "currency": "USD"},
    {"order_id": "order-002", "user_id": "user-17", "amount": 149.00, "currency": "EUR"},
    {"order_id": "order-003", "user_id": "user-99", "amount": 24.50,  "currency": "USD"},
]

for order in orders:
    # SerializationContext tells the serializer which field (KEY or VALUE) it is encoding
    producer.produce(
        topic=TOPIC,
        key=order["order_id"],
        value=avro_serializer(order, SerializationContext(TOPIC, MessageField.VALUE)),
        callback=delivery_callback,
    )

producer.flush()
print("All orders produced.")
```

After running this, inspect the registry:
```bash
curl http://localhost:8081/subjects
# ["orders-value"]

curl http://localhost:8081/subjects/orders-value/versions/latest
# {"subject":"orders-value","version":1,"id":1,"schema":"..."}
```

### Avro Consumer

```python
from confluent_kafka import Consumer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroDeserializer
from confluent_kafka.serialization import SerializationContext, MessageField

BOOTSTRAP_SERVERS = "localhost:9092"
SCHEMA_REGISTRY_URL = "http://localhost:8081"
TOPIC = "orders"

schema_registry_client = SchemaRegistryClient({"url": SCHEMA_REGISTRY_URL})

# AvroDeserializer reads the schema ID from the message wire format,
# fetches the schema from the registry (cached), and decodes the bytes.
# You can optionally pass the reader schema (for schema evolution):
avro_deserializer = AvroDeserializer(
    schema_registry_client,
    # Optional: reader schema (the schema your consumer understands).
    # If omitted, the writer schema (from the message) is used.
    # ORDER_SCHEMA_STR,
    # Optional: convert the decoded dict to a domain object:
    # from_dict=lambda data, ctx: Order(**data),
)

consumer = Consumer({
    "bootstrap.servers": BOOTSTRAP_SERVERS,
    "group.id": "orders-consumer-group",
    "auto.offset.reset": "earliest",
})
consumer.subscribe([TOPIC])

print("Waiting for orders...")
empty_polls = 0
while empty_polls < 5:
    msg = consumer.poll(timeout=2.0)
    if msg is None:
        empty_polls += 1
        continue
    empty_polls = 0

    if msg.error():
        print(f"Error: {msg.error()}")
        continue

    # Deserialize — returns a Python dict (or your domain object if from_dict was set)
    order = avro_deserializer(msg.value(), SerializationContext(TOPIC, MessageField.VALUE))
    print(f"Received order: {order}")

consumer.close()
```

---

## 12.6 Schema Evolution in Practice

### Scenario: Adding a New Field

Suppose you need to add `shipping_address` to the Order schema. You want this to be backward compatible — existing consumers that don't know about `shipping_address` should still work.

**Step 1: Verify the current compatibility mode**

```python
from confluent_kafka.schema_registry import SchemaRegistryClient

client = SchemaRegistryClient({"url": "http://localhost:8081"})
config = client.get_compatibility("orders-value")
print(f"Compatibility mode: {config}")  # e.g. "BACKWARD"
```

**Step 2: Check if the new schema is compatible before deploying**

```python
from confluent_kafka.schema_registry import SchemaRegistryClient, Schema

client = SchemaRegistryClient({"url": "http://localhost:8081"})

NEW_SCHEMA = """
{
    "type": "record",
    "name": "Order",
    "namespace": "com.example.shop",
    "fields": [
        {"name": "order_id",          "type": "string"},
        {"name": "user_id",           "type": "string"},
        {"name": "amount",            "type": "double"},
        {"name": "currency",          "type": "string", "default": "USD"},
        {"name": "shipping_address",  "type": ["null", "string"], "default": null}
    ]
}
"""

# Test compatibility against the latest registered version
is_compatible = client.test_compatibility(
    "orders-value",
    Schema(NEW_SCHEMA, "AVRO"),
)
print(f"New schema is compatible: {is_compatible}")
```

> If `is_compatible` is `False`, the schema change is breaking and would be rejected during registration. Fix the schema before proceeding.

**Step 3: Deploy consumers first (for BACKWARD compatibility)**

Because this is a BACKWARD compatible change, old messages don't have `shipping_address`, but the new consumer handles that via the `null` default. Update consumers to use the new schema first.

**Step 4: Deploy producers**

Once all consumers are updated, deploy producers that start including `shipping_address`. Old consumers (if any remain) will simply ignore the field.

### Scenario: Renaming a Field (the Hard Case)

Avro has no native "rename" concept. Renaming `amount` to `total_amount` means:

1. Old schema has `amount`
2. New schema has `total_amount`

This is **not compatible** — there is no way to tell the deserializer that `amount` is now `total_amount`.

**Options:**

**Option A: Alias** — Avro supports `aliases` on fields. The new field `total_amount` lists `amount` as an alias. When reading old data with the new schema, the decoder maps `amount` to `total_amount`.

```json
{
    "name": "total_amount",
    "type": "double",
    "aliases": ["amount"]
}
```

Note: aliases only work for reading old data with a new schema (backward), not the reverse. This is a single-direction bridge.

**Option B: Dual-field transition** — add `total_amount` alongside `amount` (both optional), migrate over time, then remove `amount` in a later version. This is the safest and most explicit approach for production.

**Option C: New topic** — if the rename represents a significant semantic change, consider creating a new topic with the new schema and running both topics in parallel during the migration period.

### What to Do When You Cannot Evolve the Schema

Sometimes a change is truly breaking (field type change, removal of required field, structural change). In that case:

1. Create a new topic (e.g. `orders-v2`) with the new schema.
2. Run a migration job to backfill historical data from `orders` into `orders-v2`.
3. Switch producers to write to `orders-v2`.
4. Switch consumers to read from `orders-v2`.
5. Retire `orders` once all consumers have migrated.

---

## 12.7 JSON Schema Support

If your team prefers JSON Schema over Avro:

```python
from confluent_kafka import Producer, Consumer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.json_schema import JSONSerializer, JSONDeserializer
from confluent_kafka.serialization import SerializationContext, MessageField
import json

BOOTSTRAP_SERVERS = "localhost:9092"
SCHEMA_REGISTRY_URL = "http://localhost:8081"
TOPIC = "events"

JSON_SCHEMA_STR = json.dumps({
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "Event",
    "type": "object",
    "properties": {
        "event_id":   {"type": "string"},
        "event_type": {"type": "string"},
        "payload":    {"type": "object"},
        "timestamp":  {"type": "number"},
    },
    "required": ["event_id", "event_type"],
})

schema_registry_client = SchemaRegistryClient({"url": SCHEMA_REGISTRY_URL})

# Producer
json_serializer = JSONSerializer(JSON_SCHEMA_STR, schema_registry_client)
producer = Producer({"bootstrap.servers": BOOTSTRAP_SERVERS})

event = {
    "event_id": "evt-001",
    "event_type": "user.signed_up",
    "payload": {"email": "alice@example.com"},
    "timestamp": 1700000000.0,
}
producer.produce(
    topic=TOPIC,
    value=json_serializer(event, SerializationContext(TOPIC, MessageField.VALUE)),
)
producer.flush()

# Consumer
json_deserializer = JSONDeserializer(JSON_SCHEMA_STR)
consumer = Consumer({
    "bootstrap.servers": BOOTSTRAP_SERVERS,
    "group.id": "events-group",
    "auto.offset.reset": "earliest",
})
consumer.subscribe([TOPIC])

empty_polls = 0
while empty_polls < 3:
    msg = consumer.poll(timeout=2.0)
    if msg is None:
        empty_polls += 1
        continue
    empty_polls = 0
    if not msg.error():
        decoded = json_deserializer(msg.value(), SerializationContext(TOPIC, MessageField.VALUE))
        print(f"Received event: {decoded}")

consumer.close()
```

---

## 12.8 Protobuf Support

For Protobuf with Schema Registry, see Module 7 for the `.proto` file setup and code generation. The Schema Registry integration is similar:

```python
from confluent_kafka.schema_registry.protobuf import ProtobufSerializer, ProtobufDeserializer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.serialization import SerializationContext, MessageField

# Assuming order_pb2 was generated from order.proto
from order_pb2 import Order

schema_registry_client = SchemaRegistryClient({"url": "http://localhost:8081"})

protobuf_serializer = ProtobufSerializer(
    Order,
    schema_registry_client,
    {"use.deprecated.format": False},
)

protobuf_deserializer = ProtobufDeserializer(
    Order,
    {"use.deprecated.format": False},
)

# Usage is identical to Avro: pass through SerializationContext
order = Order(order_id="ord-001", amount=99.99)
# producer.produce(topic, value=protobuf_serializer(order, SerializationContext(...)))
```

---

## 12.9 Subject Name Strategies

The subject name determines where a schema is registered in the registry. Three strategies are available:

### TopicNameStrategy (default)

Subject = `{topic}-value` or `{topic}-key`

```python
from confluent_kafka.schema_registry.avro import AvroSerializer

# Default — registers under "orders-value"
serializer = AvroSerializer(schema_registry_client, schema_str)
```

Use case: one schema per topic. Simple and common.

### RecordNameStrategy

Subject = `{fully-qualified-record-name}` (e.g. `com.example.shop.Order`)

```python
from confluent_kafka.schema_registry.avro import AvroSerializer

serializer = AvroSerializer(
    schema_registry_client,
    schema_str,
    conf={"auto.register.schemas": True, "subject.name.strategy": "RecordNameStrategy"},
)
```

Use case: same schema used across multiple topics. The schema is registered once and referenced everywhere.

### TopicRecordNameStrategy

Subject = `{topic}-{fully-qualified-record-name}` (e.g. `orders-com.example.shop.Order`)

Use case: multiple different record types in the same topic (polymorphic topics). Each record type has its own subject and schema ID, while still being scoped to the topic.

### Disabling Auto-Registration

By default, `AvroSerializer` registers the schema automatically on first use. In production you typically want to register schemas as part of your CI/CD pipeline and disable auto-registration in the application:

```python
serializer = AvroSerializer(
    schema_registry_client,
    schema_str,
    conf={"auto.register.schemas": False},  # schema must already be registered
)
```

This prevents accidental schema registration in production and forces all schema changes through a controlled review process.

---

## 12.10 Schema Registry in CI/CD

A typical workflow:

```bash
# In CI pipeline — check compatibility before merging
NEW_SCHEMA=$(cat schemas/order_v3.json | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))")

curl -X POST http://schema-registry:8081/compatibility/subjects/orders-value/versions/latest \
  -H "Content-Type: application/vnd.schemaregistry.v1+json" \
  -d "{\"schema\": $NEW_SCHEMA}"
# If {"is_compatible": false} -> fail the pipeline

# In deployment — register schema before deploying the producer
curl -X POST http://schema-registry:8081/subjects/orders-value/versions \
  -H "Content-Type: application/vnd.schemaregistry.v1+json" \
  -d "{\"schema\": $NEW_SCHEMA}"
```

Or from Python (suitable for a migration script):

```python
from confluent_kafka.schema_registry import SchemaRegistryClient, Schema

client = SchemaRegistryClient({"url": "http://schema-registry:8081"})

with open("schemas/order_v3.avsc") as f:
    schema_str = f.read()

schema = Schema(schema_str, "AVRO")

# Check compatibility first
is_compatible = client.test_compatibility("orders-value", schema)
if not is_compatible:
    raise RuntimeError("Schema is not compatible with registered version. Aborting.")

# Register
schema_id = client.register_schema("orders-value", schema)
print(f"Registered schema with ID: {schema_id}")
```
