# Module 7 — Serialization and Schemas

Serialization is the process of converting a Python object into bytes for Kafka to
store, and deserialization is the reverse — turning those bytes back into a Python
object on the consumer side. Every Kafka message is ultimately raw bytes; the
serialization format is a contract between producer and consumer.

This module is Python-first. Every section includes a working producer and consumer
so you can run each format end to end and see the trade-offs for yourself.

**Prerequisites:**
- Kafka is running locally via Docker (see Module 2)
- Schema Registry is running (section 2.5) for sections 7.3 and 7.4
- Install dependencies as they appear in each section

---

## 7.1 String and Bytes

### The default: raw bytes

Kafka itself knows nothing about message format. It stores and delivers bytes. The
simplest possible approach is to encode strings as UTF-8 bytes on the producer side
and decode on the consumer side.

```
Python string  →  .encode("utf-8")  →  bytes  →  Kafka
Kafka          →  bytes  →  .decode("utf-8")  →  Python string
```

### Producer — raw bytes

```python
from confluent_kafka import Producer

producer = Producer({"bootstrap.servers": "localhost:9092"})

# A plain string — must be encoded to bytes before sending
message = "Hello, Kafka!"
producer.produce("bytes-demo", value=message.encode("utf-8"))

# Already bytes — send as-is
producer.produce("bytes-demo", value=b"\x00\x01\x02\x03")

# Key is also bytes
producer.produce(
    "bytes-demo",
    key="sensor-42".encode("utf-8"),
    value="temperature:21.5".encode("utf-8"),
)

producer.flush()
print("Raw bytes messages sent.")
```

### Consumer — raw bytes

```python
from confluent_kafka import Consumer

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "bytes-demo-consumer",
    "auto.offset.reset": "earliest",
})
consumer.subscribe(["bytes-demo"])

empty_polls = 0
while empty_polls < 3:
    msg = consumer.poll(timeout=2.0)
    if msg is None:
        empty_polls += 1
        continue
    empty_polls = 0
    if msg.error():
        continue

    raw = msg.value()
    print(f"Raw bytes: {raw}")

    # Safe decode — handle the case where the bytes are not valid UTF-8
    try:
        text = raw.decode("utf-8")
        print(f"As string: {text}")
    except UnicodeDecodeError:
        print("Not valid UTF-8 — this is binary data")

consumer.close()
```

### When to use raw bytes

- Simple string messages (log lines, IDs, short text)
- When you control both producer and consumer and the format is trivially obvious
- As the building block inside custom serializers

### UTF-8 encoding edge cases

```python
# Non-ASCII characters are fine in UTF-8
producer.produce("bytes-demo", value="Ação: compra".encode("utf-8"))    # Portuguese
producer.produce("bytes-demo", value="用户注册".encode("utf-8"))          # Chinese
producer.produce("bytes-demo", value="عملية الدفع".encode("utf-8"))     # Arabic

# Avoid latin-1 or other encodings unless both sides agree
# latin-1 can silently corrupt data when consumers expect UTF-8
producer.produce("bytes-demo", value="café".encode("latin-1"))  # risky — don't do this
```

---

## 7.2 JSON Serialization

JSON is the most common serialization format for Kafka messages. It is human-readable,
easy to debug in Kafka UI, and works out of the box with Python's `json` module.

```bash
# No extra packages needed — json is part of the Python standard library
```

### Producer — JSON

```python
import json
from confluent_kafka import Producer

producer = Producer({"bootstrap.servers": "localhost:9092"})

def produce_json(topic, data, key=None):
    """Serialize a Python dict to JSON bytes and produce it."""
    payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
    key_bytes = key.encode("utf-8") if key else None
    producer.produce(topic, key=key_bytes, value=payload)
    producer.poll(0)

# Simple event
produce_json("user-events", {
    "event_type": "user.registered",
    "user_id": 1001,
    "email": "alice@example.com",
    "timestamp": "2026-01-15T10:30:00Z",
})

# Nested structure
produce_json("order-events", {
    "event_type": "order.placed",
    "order_id": "ORD-9988",
    "customer": {"id": 1001, "name": "Alice"},
    "items": [
        {"sku": "WIDGET-A", "qty": 2, "price": 9.99},
        {"sku": "GADGET-B", "qty": 1, "price": 24.99},
    ],
    "total": 44.97,
}, key="ORD-9988")

producer.flush()
print("JSON messages sent.")
```

### Consumer — JSON

```python
import json
from confluent_kafka import Consumer

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "json-consumer",
    "auto.offset.reset": "earliest",
})
consumer.subscribe(["user-events", "order-events"])

empty_polls = 0
while empty_polls < 3:
    msg = consumer.poll(timeout=2.0)
    if msg is None:
        empty_polls += 1
        continue
    empty_polls = 0
    if msg.error():
        continue

    try:
        data = json.loads(msg.value().decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"Failed to deserialize message at offset {msg.offset()}: {e}")
        # In production: send to a dead letter topic instead of crashing
        continue

    event_type = data.get("event_type", "unknown")
    print(f"[{event_type}] partition={msg.partition()} offset={msg.offset()}")
    print(f"  data: {json.dumps(data, indent=2)}")

consumer.close()
```

### Handling schema drift without enforcement

The danger of JSON is that nothing stops a producer from changing the structure.
Here is a defensive consumer pattern:

```python
import json
from confluent_kafka import Consumer
from typing import Optional

def parse_user_event(raw: bytes) -> Optional[dict]:
    """
    Safely parse a user event. Returns None if the message cannot be parsed
    or does not match the expected structure.
    """
    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None

    # Validate required fields exist and have the right types
    required = {"event_type": str, "user_id": int, "email": str}
    for field, expected_type in required.items():
        if field not in data:
            print(f"Missing required field: {field}")
            return None
        if not isinstance(data[field], expected_type):
            print(f"Wrong type for '{field}': expected {expected_type.__name__}, got {type(data[field]).__name__}")
            return None

    return data

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "safe-json-consumer",
    "auto.offset.reset": "earliest",
    "enable.auto.commit": False,
})
consumer.subscribe(["user-events"])

empty_polls = 0
while empty_polls < 3:
    msg = consumer.poll(timeout=2.0)
    if msg is None:
        empty_polls += 1
        continue
    empty_polls = 0
    if msg.error():
        continue

    event = parse_user_event(msg.value())
    if event is None:
        print(f"Bad message at offset {msg.offset()} — skipping")
        consumer.commit(msg)
        continue

    print(f"Valid event: user_id={event['user_id']} type={event['event_type']}")
    consumer.commit(msg)

consumer.close()
```

### JSON trade-offs

| | Pros | Cons |
|---|---|---|
| **Readability** | Human-readable, easy to debug in Kafka UI | — |
| **Flexibility** | No schema required, easy to add fields | No enforcement — breaking changes go undetected |
| **Compatibility** | Works everywhere, no extra tools | — |
| **Performance** | — | Larger payload than binary formats; slower to parse |
| **Schema** | — | No built-in schema; validation is manual |

---

## 7.3 Avro Serialization

Apache Avro is a binary serialization format with a schema defined in JSON. It is the
most popular choice for Kafka in production because:

1. **Compact** — binary encoding is significantly smaller than JSON
2. **Schema-enforced** — the producer cannot send data that violates the schema
3. **Schema evolution** — fields can be added or removed following compatibility rules
4. **Schema Registry integration** — Confluent's Schema Registry stores schemas and
   assigns them IDs, so only a 5-byte prefix (magic byte + schema ID) is embedded in
   each message instead of the full schema

### Install the dependencies

```bash
pip install confluent-kafka[avro]
# This installs confluent-kafka plus fastavro and the schema registry client
```

### Defining an Avro schema

An Avro schema is a JSON document that describes the structure of your data:

```python
# The schema for a "User" record
USER_SCHEMA_STR = """
{
  "type": "record",
  "name": "User",
  "namespace": "com.example",
  "fields": [
    {"name": "user_id",   "type": "int"},
    {"name": "email",     "type": "string"},
    {"name": "full_name", "type": "string"},
    {"name": "age",       "type": ["null", "int"], "default": null}
  ]
}
"""
# The "age" field uses a union type ["null", "int"] with a default of null.
# This means age is optional — producers that don't set it send null.
```

### Producer — Avro with Schema Registry

```python
from confluent_kafka import Producer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroSerializer
from confluent_kafka.serialization import SerializationContext, MessageField

# --- Schema Registry client ---
schema_registry_client = SchemaRegistryClient({"url": "http://localhost:8081"})

# --- Avro schema string ---
USER_SCHEMA_STR = """
{
  "type": "record",
  "name": "User",
  "namespace": "com.example",
  "fields": [
    {"name": "user_id",   "type": "int"},
    {"name": "email",     "type": "string"},
    {"name": "full_name", "type": "string"},
    {"name": "age",       "type": ["null", "int"], "default": null}
  ]
}
"""

# AvroSerializer converts Python dicts to Avro bytes and registers the schema
# automatically on first use. Subsequent messages just embed the schema ID.
avro_serializer = AvroSerializer(schema_registry_client, USER_SCHEMA_STR)

producer = Producer({"bootstrap.servers": "localhost:9092"})

TOPIC = "user-avro"

def produce_user(user: dict):
    # SerializationContext tells the serializer which topic and field (key/value)
    # this data is for — Schema Registry uses topic + field to namespace schemas.
    ctx = SerializationContext(TOPIC, MessageField.VALUE)
    serialized = avro_serializer(user, ctx)
    producer.produce(TOPIC, value=serialized)
    producer.poll(0)

# Produce some users
produce_user({"user_id": 1, "email": "alice@example.com", "full_name": "Alice Smith", "age": 30})
produce_user({"user_id": 2, "email": "bob@example.com",   "full_name": "Bob Jones",   "age": None})
produce_user({"user_id": 3, "email": "carol@example.com", "full_name": "Carol White", "age": 25})

producer.flush()
print("Avro messages sent.")

# Verify the schema was registered
import requests
subjects = requests.get("http://localhost:8081/subjects").json()
print(f"Registered subjects: {subjects}")
```

### Consumer — Avro with Schema Registry

```python
from confluent_kafka import Consumer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroDeserializer
from confluent_kafka.serialization import SerializationContext, MessageField

schema_registry_client = SchemaRegistryClient({"url": "http://localhost:8081"})

# AvroDeserializer fetches the schema from Schema Registry using the ID
# embedded in each message. You do NOT need to know the schema upfront —
# the deserializer looks it up automatically.
avro_deserializer = AvroDeserializer(schema_registry_client)

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "avro-consumer",
    "auto.offset.reset": "earliest",
})
consumer.subscribe(["user-avro"])

TOPIC = "user-avro"

empty_polls = 0
while empty_polls < 3:
    msg = consumer.poll(timeout=2.0)
    if msg is None:
        empty_polls += 1
        continue
    empty_polls = 0
    if msg.error():
        continue

    ctx = SerializationContext(TOPIC, MessageField.VALUE)
    user = avro_deserializer(msg.value(), ctx)

    # user is now a plain Python dict
    print(f"user_id={user['user_id']}  email={user['email']}  age={user['age']}")

consumer.close()
```

### Schema evolution — the rules

One of Avro's most valuable features is controlled schema evolution. You can change
the schema over time without breaking existing producers or consumers, as long as
you follow compatibility rules.

**BACKWARD compatibility** (the default in Schema Registry):
New schema can read data written with the old schema.
→ Consumers can be upgraded before producers.

```python
# Original schema (v1)
V1_SCHEMA = """
{
  "type": "record", "name": "User", "namespace": "com.example",
  "fields": [
    {"name": "user_id", "type": "int"},
    {"name": "email",   "type": "string"}
  ]
}
"""

# v2 — BACKWARD COMPATIBLE: added optional field with a default
# Old messages (without "full_name") can still be read — the field defaults to null
V2_SCHEMA = """
{
  "type": "record", "name": "User", "namespace": "com.example",
  "fields": [
    {"name": "user_id",   "type": "int"},
    {"name": "email",     "type": "string"},
    {"name": "full_name", "type": ["null", "string"], "default": null}
  ]
}
"""

# v3 — NOT BACKWARD COMPATIBLE: removed "email" with no default
# Old messages have "email" — new schema cannot read them → REJECTED by Schema Registry
V3_SCHEMA_INVALID = """
{
  "type": "record", "name": "User", "namespace": "com.example",
  "fields": [
    {"name": "user_id", "type": "int"}
  ]
}
"""
```

**Registering and checking compatibility from Python:**

```python
from confluent_kafka.schema_registry import SchemaRegistryClient, Schema

client = SchemaRegistryClient({"url": "http://localhost:8081"})

subject = "user-avro-value"

# Check if a new schema is compatible before registering it
new_schema = Schema(V2_SCHEMA, schema_type="AVRO")
is_compatible = client.test_compatibility(subject, new_schema)
print(f"Is v2 compatible? {is_compatible}")  # True

new_schema_bad = Schema(V3_SCHEMA_INVALID, schema_type="AVRO")
is_compatible = client.test_compatibility(subject, new_schema_bad)
print(f"Is v3 compatible? {is_compatible}")  # False — Schema Registry would reject it
```

**Schema evolution rules summary:**

| Change | BACKWARD | FORWARD | FULL |
|---|---|---|---|
| Add field with default | ✓ | ✗ | ✗ |
| Add optional field (`["null", "T"]` with default null) | ✓ | ✓ | ✓ |
| Remove field that had a default | ✗ | ✓ | ✗ |
| Rename a field | ✗ | ✗ | ✗ |
| Change a field type | ✗ | ✗ | ✗ |

**Safe rule of thumb:** Always add new fields as optional (union with null) and
with a default. Never rename or remove fields — instead, deprecate them by leaving
them in the schema and adding the new field alongside.

---

## 7.4 Protobuf Serialization

Protocol Buffers (Protobuf) is Google's binary serialization format. It is strongly
typed, very compact, and language-neutral. It is a good alternative to Avro when your
team already uses Protobuf or when you need the smallest possible payload size.

### Install the dependencies

```bash
pip install confluent-kafka[protobuf] protobuf grpcio-tools
```

### Define the schema in a .proto file

Create a file called `user.proto`:

```protobuf
syntax = "proto3";

package com.example;

message User {
  int32  user_id   = 1;
  string email     = 2;
  string full_name = 3;
  int32  age       = 4;  // 0 means not set in proto3 (no null support)
}
```

### Generate Python code from the .proto file

```bash
python -m grpc_tools.protoc \
  --python_out=. \
  --proto_path=. \
  user.proto
```

This generates `user_pb2.py` in the current directory. Import it in your Python code.

### Producer — Protobuf with Schema Registry

```python
from confluent_kafka import Producer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.protobuf import ProtobufSerializer
from confluent_kafka.serialization import SerializationContext, MessageField
import user_pb2  # generated from user.proto

schema_registry_client = SchemaRegistryClient({"url": "http://localhost:8081"})

# ProtobufSerializer registers the .proto schema in Schema Registry
# and embeds the schema ID in each message (same wire format as Avro)
protobuf_serializer = ProtobufSerializer(
    user_pb2.User,
    schema_registry_client,
    {"use.deprecated.format": False},
)

producer = Producer({"bootstrap.servers": "localhost:9092"})
TOPIC = "user-protobuf"

users = [
    user_pb2.User(user_id=1, email="alice@example.com", full_name="Alice Smith", age=30),
    user_pb2.User(user_id=2, email="bob@example.com",   full_name="Bob Jones",   age=0),
    user_pb2.User(user_id=3, email="carol@example.com", full_name="Carol White", age=25),
]

for user in users:
    ctx = SerializationContext(TOPIC, MessageField.VALUE)
    serialized = protobuf_serializer(user, ctx)
    producer.produce(TOPIC, value=serialized)
    producer.poll(0)

producer.flush()
print("Protobuf messages sent.")
```

### Consumer — Protobuf with Schema Registry

```python
from confluent_kafka import Consumer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.protobuf import ProtobufDeserializer
from confluent_kafka.serialization import SerializationContext, MessageField
import user_pb2

schema_registry_client = SchemaRegistryClient({"url": "http://localhost:8081"})

protobuf_deserializer = ProtobufDeserializer(
    user_pb2.User,
    {"use.deprecated.format": False},
)

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "protobuf-consumer",
    "auto.offset.reset": "earliest",
})
consumer.subscribe(["user-protobuf"])

TOPIC = "user-protobuf"

empty_polls = 0
while empty_polls < 3:
    msg = consumer.poll(timeout=2.0)
    if msg is None:
        empty_polls += 1
        continue
    empty_polls = 0
    if msg.error():
        continue

    ctx = SerializationContext(TOPIC, MessageField.VALUE)
    user = protobuf_deserializer(msg.value(), ctx)

    # user is now a user_pb2.User protobuf object — access fields as attributes
    print(f"user_id={user.user_id}  email={user.email}  age={user.age}")

consumer.close()
```

### Protobuf vs Avro — key differences

| | Avro | Protobuf |
|---|---|---|
| **Schema format** | JSON | `.proto` IDL file |
| **Code generation** | Not required (schema is data) | Required (`protoc` generates Python classes) |
| **Null support** | Native union types | No null — use 0 / "" as sentinel values |
| **Schema evolution** | Compatibility enforced by Schema Registry | Forward/backward compatible by field numbering |
| **Ecosystem** | Kafka-native, common in data engineering | Cross-language, common in microservices/gRPC |
| **Payload size** | Very compact | Slightly more compact than Avro in many cases |

---

## 7.5 MessagePack, CBOR, and Other Binary Formats

These formats are binary alternatives to JSON that do not require a schema. They are
compact and faster to parse than JSON but less structured than Avro or Protobuf.

### When to consider them

- You want JSON semantics (dynamic types, no code generation) but smaller payload
- You are migrating from JSON and do not want to adopt a full schema system yet
- Your team is already using MessagePack or CBOR in other parts of the stack

### MessagePack

```bash
pip install msgpack
```

```python
import msgpack
from confluent_kafka import Producer, Consumer

TOPIC = "msgpack-demo"

# --- Producer ---
producer = Producer({"bootstrap.servers": "localhost:9092"})

events = [
    {"user_id": 1, "action": "login",    "timestamp": 1741200000},
    {"user_id": 2, "action": "purchase", "amount": 49.99},
    {"user_id": 1, "action": "logout"},
]

for event in events:
    # msgpack.packb() converts a Python dict to compact binary
    # use_bin_type=True is required for correct bytes handling in Python 3
    payload = msgpack.packb(event, use_bin_type=True)
    producer.produce(TOPIC, value=payload)
    producer.poll(0)

producer.flush()
print("MessagePack messages sent.")

# --- Consumer ---
consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "msgpack-consumer",
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
    if msg.error():
        continue

    # msgpack.unpackb() converts binary back to a Python dict
    event = msgpack.unpackb(msg.value(), raw=False)
    print(f"Received: {event}")

consumer.close()
```

### Format size comparison

The following script produces the same data in all formats and compares payload sizes:

```python
import json
import msgpack
import struct

data = {
    "user_id": 12345,
    "email": "alice@example.com",
    "full_name": "Alice Smith",
    "age": 30,
    "active": True,
    "score": 98.6,
}

json_bytes   = json.dumps(data).encode("utf-8")
msgpack_bytes = msgpack.packb(data, use_bin_type=True)

print(f"JSON:       {len(json_bytes):4d} bytes  {json_bytes[:60]}...")
print(f"MessagePack:{len(msgpack_bytes):4d} bytes  (binary)")
# Avro and Protobuf would be even smaller, plus 5-byte Schema Registry prefix

# Typical output:
# JSON:         96 bytes
# MessagePack:  63 bytes
# Avro:        ~35 bytes  (binary, schema resolved via ID)
# Protobuf:    ~30 bytes  (binary)
```

### Format comparison summary

| Format | Schema required | Code generation | Human-readable | Payload size | Best for |
|---|---|---|---|---|---|
| **Raw bytes / UTF-8** | No | No | Yes | Large | Simple strings, IDs |
| **JSON** | No | No | Yes | Large | Prototyping, debugging |
| **MessagePack** | No | No | No | Medium | JSON users wanting smaller payloads |
| **CBOR** | No | No | No | Medium | IoT, embedded systems |
| **Avro** | Yes | No | No | Small | Data engineering, Kafka-native |
| **Protobuf** | Yes | Yes | No | Smallest | Microservices, gRPC systems |

---

## 7.6 Custom Serializers

Sometimes none of the standard formats fit your needs. Confluent Kafka lets you write
a custom serializer and deserializer that integrate cleanly with the producer and
consumer pipeline.

A serializer is any callable with the signature:
```python
def serializer(obj, ctx: SerializationContext) -> bytes: ...
```

A deserializer is any callable with the signature:
```python
def deserializer(data: bytes, ctx: SerializationContext) -> object: ...
```

### Example 1 — a simple dataclass serializer

Serialize Python dataclasses as compact binary using the `struct` module:

```python
import struct
from dataclasses import dataclass
from confluent_kafka import Producer, Consumer
from confluent_kafka.serialization import SerializationContext

@dataclass
class SensorReading:
    sensor_id: int     # 4 bytes (unsigned int)
    temperature: float # 8 bytes (double)
    humidity: float    # 8 bytes (double)
    # Total: 20 bytes per message — very compact

# Format string for struct: I = unsigned int (4 bytes), d = double (8 bytes)
STRUCT_FORMAT = "!Idd"  # ! = network byte order (big-endian)

def sensor_serializer(reading: SensorReading, ctx: SerializationContext) -> bytes:
    return struct.pack(STRUCT_FORMAT, reading.sensor_id, reading.temperature, reading.humidity)

def sensor_deserializer(data: bytes, ctx: SerializationContext) -> SensorReading:
    sensor_id, temperature, humidity = struct.unpack(STRUCT_FORMAT, data)
    return SensorReading(sensor_id=sensor_id, temperature=temperature, humidity=humidity)


# --- Producer ---
from confluent_kafka.serialization import MessageField

TOPIC = "sensor-readings"
producer = Producer({"bootstrap.servers": "localhost:9092"})

readings = [
    SensorReading(sensor_id=1, temperature=21.5, humidity=60.2),
    SensorReading(sensor_id=2, temperature=19.8, humidity=55.0),
    SensorReading(sensor_id=1, temperature=22.1, humidity=61.0),
]

ctx = SerializationContext(TOPIC, MessageField.VALUE)
for reading in readings:
    payload = sensor_serializer(reading, ctx)
    print(f"Serialized {reading} to {len(payload)} bytes")
    producer.produce(TOPIC, key=str(reading.sensor_id).encode(), value=payload)
    producer.poll(0)

producer.flush()

# --- Consumer ---
consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "sensor-consumer",
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
    if msg.error():
        continue

    reading = sensor_deserializer(msg.value(), SerializationContext(TOPIC, MessageField.VALUE))
    print(f"sensor={reading.sensor_id}  temp={reading.temperature}°C  humidity={reading.humidity}%")

consumer.close()
```

### Example 2 — a reusable class-based serializer

For more complex cases, implement a class with `__call__` so it can carry
configuration:

```python
import json
import gzip
from confluent_kafka.serialization import Serializer, Deserializer, SerializationContext

class CompressedJsonSerializer(Serializer):
    """Serializes a Python dict to gzip-compressed JSON bytes."""

    def __init__(self, compression_level: int = 6):
        self.compression_level = compression_level

    def __call__(self, obj: dict, ctx: SerializationContext) -> bytes:
        if obj is None:
            return None
        json_bytes = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        return gzip.compress(json_bytes, compresslevel=self.compression_level)


class CompressedJsonDeserializer(Deserializer):
    """Deserializes gzip-compressed JSON bytes back to a Python dict."""

    def __call__(self, data: bytes, ctx: SerializationContext) -> dict:
        if data is None:
            return None
        json_bytes = gzip.decompress(data)
        return json.loads(json_bytes.decode("utf-8"))


# --- Usage ---
from confluent_kafka import Producer, Consumer
from confluent_kafka.serialization import SerializationContext, MessageField

serializer   = CompressedJsonSerializer(compression_level=9)
deserializer = CompressedJsonDeserializer()

TOPIC = "compressed-events"
producer = Producer({"bootstrap.servers": "localhost:9092"})

large_event = {
    "event_type": "order.placed",
    "order_id": "ORD-0042",
    "items": [{"sku": f"SKU-{i}", "qty": i, "price": i * 1.5} for i in range(50)],
}

ctx = SerializationContext(TOPIC, MessageField.VALUE)
raw_json = json.dumps(large_event).encode()
compressed = serializer(large_event, ctx)
print(f"JSON:       {len(raw_json):5d} bytes")
print(f"Compressed: {len(compressed):5d} bytes  ({100 - len(compressed)/len(raw_json)*100:.1f}% reduction)")

producer.produce(TOPIC, value=compressed)
producer.flush()

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "compressed-consumer",
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
    if msg.error():
        continue

    event = deserializer(msg.value(), SerializationContext(TOPIC, MessageField.VALUE))
    print(f"Received order: {event['order_id']}  items={len(event['items'])}")

consumer.close()
```

---

## Summary — Quick Reference

| Format | Install | Serialize | Deserialize | Schema Registry |
|---|---|---|---|---|
| **Raw bytes** | built-in | `.encode("utf-8")` | `.decode("utf-8")` | No |
| **JSON** | built-in | `json.dumps(...).encode()` | `json.loads(bytes)` | No |
| **Avro** | `pip install confluent-kafka[avro]` | `AvroSerializer` | `AvroDeserializer` | Yes |
| **Protobuf** | `pip install confluent-kafka[protobuf] protobuf grpcio-tools` | `ProtobufSerializer` | `ProtobufDeserializer` | Yes |
| **MessagePack** | `pip install msgpack` | `msgpack.packb(obj)` | `msgpack.unpackb(bytes)` | No |
| **Custom** | depends | implement `__call__(obj, ctx) -> bytes` | implement `__call__(bytes, ctx) -> obj` | Optional |

**Decision guide:**

```
Are you prototyping or debugging?
  → JSON

Do you need schema enforcement and are in a Kafka-first data engineering context?
  → Avro + Schema Registry

Do you already use gRPC or Protobuf elsewhere in your stack?
  → Protobuf + Schema Registry

Do you want JSON flexibility but smaller payloads with no schema?
  → MessagePack

Do you have a very specific binary format (IoT sensors, financial ticks)?
  → Custom serializer with struct
```
