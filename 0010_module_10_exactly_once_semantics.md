# Module 10 — Exactly-Once Semantics and Transactions

## Overview

Kafka offers three delivery guarantees:

| Guarantee | What It Means | How |
|---|---|---|
| **At most once** | Messages may be lost, never duplicated | No retries, no acks |
| **At least once** | Messages are never lost, but may be duplicated | Retries + `acks=all` |
| **Exactly once** | Each message is processed exactly once, end to end | Idempotent producer + transactions |

Most systems default to **at-least-once**, which is sufficient when your consumer logic is idempotent (safe to replay). **Exactly-once semantics (EOS)** are needed when duplicates would cause real harm — double-charging a customer, double-counting an inventory item, or corrupting an aggregated total.

This module explains how Kafka achieves exactly-once at the producer level (idempotent producer) and across a consume-transform-produce pipeline (transactions).

---

## 10.1 Idempotent Producer

### The Problem: Retries Cause Duplicates

When a producer sends a batch and the broker crashes before sending an acknowledgement, the producer cannot tell whether the batch was written or not. So it retries — and if the broker *did* write the batch before crashing, the retry creates a duplicate.

```
Producer sends batch --> Broker writes batch --> Broker crashes before ACK
Producer retries     --> Broker writes batch again --> DUPLICATE
```

This happens even with `acks=all` and `retries>0` — which is the standard durability configuration.

### How Idempotency Works

The idempotent producer assigns each producer instance a **Producer ID (PID)** at startup and attaches a monotonically increasing **sequence number** to every message batch. The broker tracks the last sequence number it accepted from each PID and discards any batch that arrives with a sequence number it has already seen.

```
Producer (PID=42) sends batch (seq=5) --> Broker writes, stores (PID=42, seq=5)
Network hiccup -- producer retries     --> Broker sees (PID=42, seq=5) again
                                           --> DISCARDED (already stored)
Producer continues with seq=6         --> Broker accepts and writes
```

The deduplication window is per-partition, per-producer-session. PIDs are assigned per broker session, so restarting the producer gives it a new PID and a fresh deduplication window — this is not a cross-session deduplication mechanism.

### Configuration

```python
from confluent_kafka import Producer

producer = Producer({
    "bootstrap.servers": "localhost:9092",
    "enable.idempotence": True,   # enables idempotent producer
    # The following are set automatically when enable.idempotence=True,
    # but shown here for clarity:
    "acks": "all",                # require all in-sync replicas to acknowledge
    "retries": 2147483647,        # effectively infinite retries
    "max.in.flight.requests.per.connection": 5,  # max 5 in-flight (Kafka guarantees order up to 5)
})
```

> **Note:** Setting `enable.idempotence=True` automatically enforces `acks=all` and sufficient `retries`. If you manually set conflicting values (e.g. `acks=0`), the producer will raise a configuration error.

### Idempotent Producer in Action

```python
import time
from confluent_kafka import Producer, KafkaException

BOOTSTRAP_SERVERS = "localhost:9092"
TOPIC = "payments"

def delivery_callback(err, msg):
    if err:
        print(f"Delivery failed: {err}")
    else:
        print(f"Delivered: {msg.value().decode()} -> partition={msg.partition()} offset={msg.offset()}")

producer = Producer({
    "bootstrap.servers": BOOTSTRAP_SERVERS,
    "enable.idempotence": True,
})

# Even if this call is retried internally due to a transient broker error,
# the broker guarantees the message is written exactly once.
producer.produce(
    TOPIC,
    key="order-123",
    value='{"amount": 99.99, "currency": "USD"}',
    callback=delivery_callback,
)
producer.flush()
```

### What Idempotence Does NOT Solve

- **Application-level duplicates**: if your code calls `produce()` twice with the same data, both messages are written. The producer deduplicates its own retries, not your bugs.
- **Cross-session deduplication**: restarting the producer gives it a new PID — no memory of previous sessions.
- **Consumer-side duplicates**: if the consumer crashes after processing a message but before committing the offset, it will re-read and reprocess the message. For that, use transactions (section 10.3).

---

## 10.2 Transactional Producer

Transactions let you write to **multiple partitions and topics atomically**. Either all writes succeed and become visible to consumers together, or none of them do.

Use cases:
- Fan-out: one input event produces N output events that must all appear together
- Topic routing: moving a message from one topic to another as a single atomic operation
- Consume-transform-produce: reading from topic A and writing to topic B with offset commit, all atomically

### Configuration

```python
from confluent_kafka import Producer

producer = Producer({
    "bootstrap.servers": "localhost:9092",
    "transactional.id": "my-app-producer-1",  # unique per producer instance
    # enable.idempotence is automatically set to True when using transactions
})
```

`transactional.id` must be unique across all running instances of your application. Kafka uses it for **zombie fencing** — if an old instance of the producer comes back after a restart and tries to continue an old transaction, the broker rejects it because the new instance has already registered the same `transactional.id`.

### Transaction Lifecycle

```
init_transactions()        -- register with the broker, get epoch
begin_transaction()        -- mark the start of a transaction
produce(...)               -- one or more produce calls (any topics/partitions)
send_offsets_to_transaction()  -- optional: include consumer offsets in the tx
commit_transaction()       -- make all writes visible atomically
  OR
abort_transaction()        -- roll back all writes, nothing becomes visible
```

### Basic Transactional Producer

```python
from confluent_kafka import Producer, KafkaException

BOOTSTRAP_SERVERS = "localhost:9092"

producer = Producer({
    "bootstrap.servers": BOOTSTRAP_SERVERS,
    "transactional.id": "payments-producer-1",
})

# Must be called once at startup, before any transactions.
# This registers the producer with the broker and fences any previous
# instance with the same transactional.id.
producer.init_transactions()

def send_payment_event_atomically(order_id: str, amount: float):
    """
    Write to two topics atomically.
    Either both writes succeed and become visible, or neither does.
    """
    producer.begin_transaction()
    try:
        # Write the payment event
        producer.produce(
            "payments",
            key=order_id,
            value=f'{{"order_id": "{order_id}", "amount": {amount}, "status": "completed"}}',
        )
        # Write a corresponding audit log entry — same transaction
        producer.produce(
            "audit-log",
            key=order_id,
            value=f'{{"order_id": "{order_id}", "action": "payment_processed", "amount": {amount}}}',
        )
        producer.commit_transaction()
        print(f"Transaction committed for order {order_id}")
    except KafkaException as e:
        print(f"Transaction failed: {e} — aborting")
        producer.abort_transaction()
        raise

send_payment_event_atomically("order-123", 99.99)
send_payment_event_atomically("order-456", 149.00)
```

### What Consumers See During a Transaction

While a transaction is open, messages written inside it are stored on the broker but **not visible to consumers** reading with `isolation.level=read_committed` (the setting for exactly-once consumers). They only become visible when `commit_transaction()` is called.

Consumers with `isolation.level=read_uncommitted` (the default) see all messages, including those inside open or aborted transactions — which defeats the purpose of transactions. Always pair transactional producers with `read_committed` consumers.

---

## 10.3 Consume-Transform-Produce Pattern

This is the most common exactly-once pattern: read from topic A, transform the message, write to topic B, and commit the consumer offset — all in a single atomic transaction.

Without transactions:
```
consumer reads msg from topic-A (offset 5)
producer writes result to topic-B
consumer crashes before committing offset 5
consumer restarts at offset 5
producer writes result to topic-B AGAIN  <-- duplicate
```

With transactions, the consumer offset commit is included inside the transaction. If the transaction aborts, the offset is not committed either, so the consumer re-reads the same message and re-runs the transaction.

### Setup: Topics

```bash
# Input topic
docker exec kafka /opt/kafka/bin/kafka-topics.sh --create \
  --topic raw-orders \
  --bootstrap-server localhost:9092 \
  --partitions 3 \
  --replication-factor 1

# Output topic
docker exec kafka /opt/kafka/bin/kafka-topics.sh --create \
  --topic enriched-orders \
  --bootstrap-server localhost:9092 \
  --partitions 3 \
  --replication-factor 1
```

### Complete Consume-Transform-Produce Example

```python
import json
import signal
from confluent_kafka import Producer, Consumer, TopicPartition, KafkaException

BOOTSTRAP_SERVERS = "localhost:9092"
INPUT_TOPIC = "raw-orders"
OUTPUT_TOPIC = "enriched-orders"
CONSUMER_GROUP = "order-enrichment-service"
TRANSACTIONAL_ID = "order-enrichment-producer-1"

# ---------------------------------------------------------------------------
# Producer — transactional
# ---------------------------------------------------------------------------
producer = Producer({
    "bootstrap.servers": BOOTSTRAP_SERVERS,
    "transactional.id": TRANSACTIONAL_ID,
})
producer.init_transactions()

# ---------------------------------------------------------------------------
# Consumer — read_committed so we only see committed transactional messages
# ---------------------------------------------------------------------------
consumer = Consumer({
    "bootstrap.servers": BOOTSTRAP_SERVERS,
    "group.id": CONSUMER_GROUP,
    "auto.offset.reset": "earliest",
    # CRITICAL: disable auto-commit — we commit offsets inside the transaction
    "enable.auto.commit": False,
    # CRITICAL: only see committed messages from transactional producers
    "isolation.level": "read_committed",
})
consumer.subscribe([INPUT_TOPIC])

# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------
running = True

def handle_shutdown(sig, frame):
    global running
    running = False

signal.signal(signal.SIGINT, handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)

# ---------------------------------------------------------------------------
# Enrichment logic (your business logic lives here)
# ---------------------------------------------------------------------------
# A product catalog for enrichment (in production this might be a DB lookup)
PRODUCT_CATALOG = {
    "SKU-001": {"name": "Wireless Headphones", "category": "Electronics"},
    "SKU-002": {"name": "Coffee Mug", "category": "Kitchen"},
    "SKU-003": {"name": "Notebook", "category": "Office"},
}

def enrich_order(raw_order: dict) -> dict:
    sku = raw_order.get("sku", "UNKNOWN")
    product = PRODUCT_CATALOG.get(sku, {"name": "Unknown Product", "category": "Misc"})
    return {
        **raw_order,
        "product_name": product["name"],
        "category": product["category"],
        "enriched": True,
    }

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
print("Starting consume-transform-produce loop. Press Ctrl+C to stop.")

while running:
    # Poll for a batch of messages (up to 500ms wait)
    msg = consumer.poll(timeout=0.5)

    if msg is None:
        continue  # no messages yet — normal during startup or quiet periods

    if msg.error():
        print(f"Consumer error: {msg.error()}")
        continue

    # Parse the raw order
    try:
        raw_order = json.loads(msg.value().decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"Deserialization error, skipping: {e}")
        # In production you would route this to a DLQ (see Module 9)
        continue

    # Run the transformation
    enriched_order = enrich_order(raw_order)

    # Wrap the produce + offset commit in a single transaction
    producer.begin_transaction()
    try:
        # Write the enriched order to the output topic
        producer.produce(
            OUTPUT_TOPIC,
            key=msg.key(),
            value=json.dumps(enriched_order).encode("utf-8"),
        )

        # Include the consumer's current position in the transaction.
        # This means the offset commit only becomes effective if the
        # transaction commits. If it aborts, the consumer will re-read
        # this message on the next attempt.
        #
        # consumer.position() returns the NEXT offset to be read,
        # which is what we want to commit (current offset + 1).
        offsets = consumer.position(consumer.assignment())
        consumer.commit(offsets=offsets, asynchronous=False)

        # send_offsets_to_transaction atomically includes the offset commit
        # inside the ongoing transaction.
        group_metadata = consumer.consumer_group_metadata()
        producer.send_offsets_to_transaction(offsets, group_metadata)

        producer.commit_transaction()
        print(f"Processed order {raw_order.get('order_id')} -> {enriched_order['product_name']}")

    except KafkaException as e:
        print(f"Transaction failed: {e} — aborting")
        producer.abort_transaction()
        # The consumer offset was not committed, so we will re-read this
        # message on the next poll and retry the transaction.

consumer.close()
print("Consumer closed.")
```

### Testing the Pipeline

Open three terminals:

**Terminal 1 — start the pipeline:**
```bash
python3 consume_transform_produce.py
```

**Terminal 2 — produce test orders:**
```bash
docker exec -it kafka /opt/kafka/bin/kafka-console-producer.sh \
  --topic raw-orders \
  --bootstrap-server localhost:9092 \
  --property "key.separator=:" \
  --property "parse.key=true"
```
Type these lines (key:value format):
```
order-1:{"order_id": "order-1", "sku": "SKU-001", "quantity": 2, "price": 49.99}
order-2:{"order_id": "order-2", "sku": "SKU-002", "quantity": 1, "price": 12.50}
order-3:{"order_id": "order-3", "sku": "SKU-003", "quantity": 5, "price": 8.99}
```

**Terminal 3 — read enriched orders:**
```bash
docker exec -it kafka /opt/kafka/bin/kafka-console-consumer.sh \
  --topic enriched-orders \
  --bootstrap-server localhost:9092 \
  --from-beginning
```

You should see the enriched orders with `product_name` and `category` fields added.

---

## 10.4 Consumer Isolation Level

Consumers must be configured to only read committed transactional messages:

```python
consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "my-group",
    "isolation.level": "read_committed",   # default is "read_uncommitted"
    "enable.auto.commit": False,
})
```

| Isolation Level | What It Sees |
|---|---|
| `read_uncommitted` (default) | All messages, including those inside open or aborted transactions |
| `read_committed` | Only messages from committed transactions, and non-transactional messages |

If a transaction is aborted, messages written inside it are marked as aborted on the broker. Consumers with `read_committed` skip them automatically — your application never sees them.

---

## 10.5 Limitations and Trade-offs

### Performance Impact

Transactions add overhead. Each transaction requires:
- A `begin_transaction` marker written to every partition involved
- A `commit` or `abort` marker written to every partition involved
- Coordination with the broker's Transaction Coordinator (a special internal partition)

Benchmark guidance:
- For high-throughput pipelines, batch multiple messages per transaction rather than one transaction per message
- Aim for transactions that last 100ms–1s; very long transactions hold the Transaction Coordinator lock longer
- Avoid transactions if your use case only needs idempotency without atomicity — use `enable.idempotence=True` alone instead

### Transaction Timeout

```python
producer = Producer({
    "bootstrap.servers": "localhost:9092",
    "transactional.id": "my-producer",
    "transaction.timeout.ms": 60000,  # default: 60 seconds
})
```

If a transaction is not committed or aborted within `transaction.timeout.ms`, the broker automatically aborts it. Size your timeout to comfortably fit your processing time, including retries.

### Transactions Do Not Cross System Boundaries

Kafka transactions are atomic within Kafka only. If your consume-transform-produce pipeline also writes to a database, that database write is outside the transaction. You need to handle consistency with that external system separately (see Module 9 — idempotency patterns and Module 19 — outbox pattern).

```
Kafka transaction commits  -- Kafka is consistent
Database write succeeds    -- separate operation, no rollback if Kafka fails
```

For cross-system consistency, the outbox pattern or saga pattern is the right approach.

### Zombie Fencing

If a producer instance crashes and a new instance starts with the same `transactional.id`, the new instance calls `init_transactions()` which bumps the **producer epoch**. The broker then rejects any writes from the old instance that might still be running. This prevents two instances from simultaneously producing to the same transaction.

```
Old instance (epoch=3): begin_transaction, produce...
New instance starts: init_transactions() --> epoch becomes 4
Old instance tries to commit --> broker rejects: epoch 3 is fenced
New instance resumes from scratch: begin_transaction (epoch=4)...
```

### Summary: When to Use What

| Scenario | Recommendation |
|---|---|
| Need resilient delivery, can tolerate rare duplicates | `acks=all`, `retries`, idempotent consumer logic |
| Need producer-level deduplication only | `enable.idempotence=True` |
| Need atomic multi-topic writes | Transactions |
| Need exactly-once end-to-end (consume-transform-produce) | Transactions + `isolation.level=read_committed` |
| Writing to external DB + Kafka | Transactions for Kafka part + idempotent DB writes separately |
