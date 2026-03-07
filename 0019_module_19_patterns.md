# Module 19 — Common Patterns and Architectures

## Overview

This module covers the recurring design patterns you will encounter when building Kafka-based systems. Each pattern solves a specific problem. Understanding which pattern fits your problem is as important as the implementation.

---

## 19.1 Event-Driven Architecture

### Events vs Commands vs Queries

| Type | Intent | Example |
|---|---|---|
| **Command** | Tell a service to do something | `ProcessPayment` |
| **Event** | Announce something that happened | `PaymentProcessed` |
| **Query** | Ask for current state | `GetOrderStatus` |

Kafka is best suited for **events**. An event is immutable — it describes something that already happened and cannot be undone. Events are named in past tense.

### Event Notification vs Event-Carried State Transfer

**Event Notification** — the event tells consumers *that* something happened. Consumers must query the source service for the actual data.

```json
{ "event": "order.created", "order_id": "ord-123" }
```

Consumers call the Order Service to get the full order. Decoupled, but creates chattiness.

**Event-Carried State Transfer** — the event includes all the data the consumer needs. No follow-up query required.

```json
{
  "event": "order.created",
  "order_id": "ord-123",
  "user_id": "user-42",
  "amount": 99.99,
  "items": [{"sku": "SKU-001", "qty": 2}],
  "shipping_address": "123 Main St"
}
```

More data on the wire, but consumers are fully autonomous. **Prefer this when possible** — it reduces inter-service coupling and latency.

### Event Sourcing

Instead of storing only the current state of an entity, store every event that led to that state. Kafka (with appropriate retention) becomes the system of record.

```
Order Events (topic: order-events, key: order_id)
────────────────────────────────────────────────
OrderCreated    { order_id, user_id, items }
PaymentReceived { order_id, amount, method }
OrderShipped    { order_id, tracking_number }
OrderDelivered  { order_id, delivered_at }
```

Current state is computed by replaying events:

```python
import json
from dataclasses import dataclass, field
from typing import Optional
from confluent_kafka import Consumer

@dataclass
class Order:
    order_id: str
    user_id: str = ""
    items: list = field(default_factory=list)
    amount: float = 0.0
    status: str = "unknown"
    tracking_number: Optional[str] = None

def apply_event(order: Order, event_type: str, payload: dict) -> Order:
    """Mutate order state based on an event. This is the 'reducer'."""
    if event_type == "OrderCreated":
        order.user_id = payload["user_id"]
        order.items = payload["items"]
        order.status = "created"
    elif event_type == "PaymentReceived":
        order.amount = payload["amount"]
        order.status = "paid"
    elif event_type == "OrderShipped":
        order.tracking_number = payload["tracking_number"]
        order.status = "shipped"
    elif event_type == "OrderDelivered":
        order.status = "delivered"
    return order

def rebuild_order_from_events(bootstrap_servers: str, order_id: str) -> Optional[Order]:
    """Replay all events for an order to reconstruct its current state."""
    consumer = Consumer({
        "bootstrap.servers": bootstrap_servers,
        "group.id": f"order-rebuilder-{order_id}",
        "auto.offset.reset": "earliest",
    })
    consumer.subscribe(["order-events"])

    order = Order(order_id=order_id)
    found = False
    empty = 0

    while empty < 3:
        msg = consumer.poll(timeout=2.0)
        if msg is None:
            empty += 1
            continue
        empty = 0
        if msg.error():
            continue

        # Only process events for our specific order
        if msg.key() and msg.key().decode() == order_id:
            event = json.loads(msg.value().decode())
            apply_event(order, event["type"], event["payload"])
            found = True

    consumer.close()
    return order if found else None

# Example: produce events
from confluent_kafka import Producer

producer = Producer({"bootstrap.servers": "localhost:9092"})

events = [
    ("order-123", {"type": "OrderCreated", "payload": {"user_id": "u-1", "items": [{"sku": "SKU-001", "qty": 1}]}}),
    ("order-123", {"type": "PaymentReceived", "payload": {"amount": 49.99, "method": "card"}}),
    ("order-123", {"type": "OrderShipped", "payload": {"tracking_number": "TRK-9876"}}),
]

for order_id, event in events:
    producer.produce(
        "order-events",
        key=order_id,
        value=json.dumps(event).encode(),
    )
producer.flush()

# Replay and rebuild
order = rebuild_order_from_events("localhost:9092", "order-123")
print(f"Order status: {order.status}, tracking: {order.tracking_number}")
```

---

## 19.2 CQRS — Command Query Responsibility Segregation

CQRS separates write operations (commands) from read operations (queries). Kafka is the bridge between the two.

```
Write side (Command)          Kafka                Read side (Query)
─────────────────────         ──────               ─────────────────────
Order Service                 order-events         Projection Builder
  - validates order    ──>    topic           ──>    - builds read-optimised
  - applies business                                   views (Redis, Postgres)
    rules
  - produces event
                                                    Query API
                                                      - serves reads from
                                                        the projection
```

```python
# projection_builder.py
# Consumes order events and builds a Redis-backed read model

import json
import redis
from confluent_kafka import Consumer

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "order-projection-builder",
    "auto.offset.reset": "earliest",
    "enable.auto.commit": False,
})
consumer.subscribe(["order-events"])

empty = 0
while empty < 5:
    msg = consumer.poll(timeout=2.0)
    if msg is None:
        empty += 1
        continue
    empty = 0
    if msg.error():
        continue

    order_id = msg.key().decode()
    event = json.loads(msg.value().decode())
    event_type = event["type"]
    payload = event["payload"]

    # Update a Redis hash that represents the current order state
    key = f"order:{order_id}"
    if event_type == "OrderCreated":
        r.hset(key, mapping={
            "order_id": order_id,
            "user_id": payload["user_id"],
            "status": "created",
        })
    elif event_type == "PaymentReceived":
        r.hset(key, mapping={"status": "paid", "amount": payload["amount"]})
    elif event_type == "OrderShipped":
        r.hset(key, mapping={"status": "shipped", "tracking": payload["tracking_number"]})

    consumer.commit(message=msg, asynchronous=False)

consumer.close()
```

---

## 19.3 Saga / Choreography Pattern

A saga coordinates a distributed transaction across multiple services. In the **choreography** style (preferred with Kafka), there is no central orchestrator — each service reacts to events and produces its own events.

### Order Fulfilment Saga

```
OrderService        PaymentService       InventoryService      ShippingService
     |                    |                    |                    |
     |--OrderCreated------>                    |                    |
     |                    |--PaymentProcessed->                    |
     |                    |                    |--StockReserved---->|
     |                    |                    |                    |--Shipped-->

Failure path:
     |                    |--PaymentFailed---->                    |
     |<--OrderCancelled---                    |                    |
```

```python
# payment_service.py
# Listens for OrderCreated, charges the customer, emits PaymentProcessed or PaymentFailed

import json
from confluent_kafka import Consumer, Producer

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "payment-service",
    "auto.offset.reset": "earliest",
    "enable.auto.commit": False,
})
consumer.subscribe(["order-events"])

producer = Producer({"bootstrap.servers": "localhost:9092"})

def charge_customer(user_id: str, amount: float) -> bool:
    """Simulate payment processing. Returns True if successful."""
    return amount < 1000  # reject orders over $1000 in this demo

empty = 0
while empty < 5:
    msg = consumer.poll(timeout=2.0)
    if msg is None:
        empty += 1
        continue
    empty = 0
    if msg.error():
        continue

    event = json.loads(msg.value().decode())
    if event["type"] != "OrderCreated":
        continue  # only handle this event type

    order_id = msg.key().decode()
    payload = event["payload"]

    success = charge_customer(payload["user_id"], payload.get("amount", 0))

    if success:
        result_event = {"type": "PaymentProcessed", "payload": {"order_id": order_id, "amount": payload.get("amount")}}
        producer.produce("order-events", key=order_id, value=json.dumps(result_event).encode())
        print(f"Payment processed for {order_id}")
    else:
        result_event = {"type": "PaymentFailed", "payload": {"order_id": order_id, "reason": "Amount exceeds limit"}}
        producer.produce("order-events", key=order_id, value=json.dumps(result_event).encode())
        print(f"Payment failed for {order_id}")

    producer.flush()
    consumer.commit(message=msg, asynchronous=False)

consumer.close()
```

### Compensating Transactions

If a step fails, a compensating event is produced to undo the previous steps:

```python
# If PaymentFailed is received by InventoryService (which already reserved stock):
if event["type"] == "PaymentFailed":
    order_id = payload["order_id"]
    release_reserved_stock(order_id)
    producer.produce(
        "order-events",
        key=order_id,
        value=json.dumps({"type": "StockReleased", "payload": {"order_id": order_id}}).encode()
    )
```

---

## 19.4 Outbox Pattern

The outbox pattern solves the dual-write problem: "how do I save to the database AND produce a Kafka event atomically?"

**Problem:** If you write to the database and then the process crashes before producing to Kafka, you have inconsistent state.

**Solution:** Write the event to an `outbox` table in the same database transaction. A separate process (or Debezium CDC) reads the outbox and produces to Kafka.

```sql
-- Create outbox table
CREATE TABLE outbox (
    id          SERIAL PRIMARY KEY,
    event_type  VARCHAR(100),
    aggregate_id VARCHAR(100),
    payload     JSONB,
    created_at  TIMESTAMP DEFAULT NOW(),
    published   BOOLEAN DEFAULT FALSE
);
```

```python
# order_service.py — atomic database write + outbox insert

import json
import psycopg2

def create_order(conn, user_id: str, items: list, amount: float) -> str:
    """Create an order and insert an outbox event in the same transaction."""
    with conn.cursor() as cur:
        # 1. Insert the order
        cur.execute(
            "INSERT INTO orders (user_id, amount, status) VALUES (%s, %s, 'created') RETURNING id",
            (user_id, amount),
        )
        order_id = str(cur.fetchone()[0])

        # 2. Insert the outbox event — same transaction, guaranteed consistency
        payload = json.dumps({"order_id": order_id, "user_id": user_id, "items": items, "amount": amount})
        cur.execute(
            "INSERT INTO outbox (event_type, aggregate_id, payload) VALUES (%s, %s, %s)",
            ("OrderCreated", order_id, payload),
        )
    conn.commit()  # both writes commit or both roll back
    return order_id

# outbox_relay.py — poll the outbox table and produce to Kafka
import time
import psycopg2
from confluent_kafka import Producer

producer = Producer({"bootstrap.servers": "localhost:9092"})

def relay_outbox(conn):
    """Read unpublished outbox events and produce them to Kafka."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, event_type, aggregate_id, payload FROM outbox WHERE published=FALSE ORDER BY id LIMIT 100"
        )
        rows = cur.fetchall()

        for row_id, event_type, aggregate_id, payload in rows:
            event = json.dumps({"type": event_type, "payload": payload})
            producer.produce(
                "order-events",
                key=aggregate_id,
                value=event.encode(),
            )

        producer.flush()

        # Mark as published only after successful Kafka produce
        ids = [r[0] for r in rows]
        if ids:
            cur.execute(
                "UPDATE outbox SET published=TRUE WHERE id = ANY(%s)",
                (ids,),
            )
        conn.commit()
        return len(rows)

conn = psycopg2.connect("postgresql://postgres:postgres@localhost/shop")
print("Outbox relay running...")
while True:
    count = relay_outbox(conn)
    if count:
        print(f"Relayed {count} events")
    time.sleep(1)
```

> In production, replace the polling outbox relay with **Debezium CDC** (see Module 13) — it reads from the PostgreSQL WAL directly with sub-second latency and no polling overhead.

---

## 19.5 Request-Reply Pattern

Kafka is asynchronous by nature. But sometimes you need a synchronous-style response (e.g. an API that calls Kafka and waits for the result).

The pattern:
1. Producer sends a request message with a `correlation_id` and a `reply_to` topic
2. The consumer processes it and produces a reply to the `reply_to` topic using the same `correlation_id`
3. The original producer reads from the reply topic, matching the `correlation_id` to find its response

```python
# request_reply.py

import json
import uuid
import time
import threading
from confluent_kafka import Producer, Consumer

BOOTSTRAP_SERVERS = "localhost:9092"
REQUEST_TOPIC = "pricing-requests"
REPLY_TOPIC = "pricing-replies"

# ─── Server side (runs in a separate service) ────────────────────────────────

def pricing_server():
    consumer = Consumer({
        "bootstrap.servers": BOOTSTRAP_SERVERS,
        "group.id": "pricing-service",
        "auto.offset.reset": "latest",
    })
    consumer.subscribe([REQUEST_TOPIC])
    producer = Producer({"bootstrap.servers": BOOTSTRAP_SERVERS})

    empty = 0
    while empty < 10:
        msg = consumer.poll(timeout=1.0)
        if msg is None:
            empty += 1
            continue
        empty = 0
        if msg.error():
            continue

        request = json.loads(msg.value().decode())
        headers = dict(msg.headers() or [])
        correlation_id = headers.get(b"correlation-id", b"").decode()
        reply_to = headers.get(b"reply-to", b"").decode()

        # Business logic: calculate price
        sku = request.get("sku", "")
        quantity = request.get("quantity", 1)
        unit_price = 49.99 if sku == "SKU-001" else 19.99
        total = unit_price * quantity

        reply = json.dumps({"sku": sku, "quantity": quantity, "total": total})
        producer.produce(
            reply_to,
            value=reply.encode(),
            headers=[("correlation-id", correlation_id.encode())],
        )
        producer.flush()
        print(f"[Server] Replied to {correlation_id}: ${total:.2f}")

    consumer.close()

# ─── Client side ─────────────────────────────────────────────────────────────

class RequestReplyClient:
    def __init__(self, bootstrap_servers: str, reply_topic: str):
        self.producer = Producer({"bootstrap.servers": bootstrap_servers})
        self.consumer = Consumer({
            "bootstrap.servers": bootstrap_servers,
            "group.id": f"rr-client-{uuid.uuid4()}",
            "auto.offset.reset": "latest",
        })
        self.consumer.subscribe([reply_topic])
        self.reply_topic = reply_topic
        self._pending: dict[str, threading.Event] = {}
        self._results: dict[str, dict] = {}

        # Background thread to collect replies
        self._running = True
        self._reply_thread = threading.Thread(target=self._collect_replies, daemon=True)
        self._reply_thread.start()

    def _collect_replies(self):
        while self._running:
            msg = self.consumer.poll(timeout=0.5)
            if msg is None or msg.error():
                continue
            headers = dict(msg.headers() or [])
            correlation_id = headers.get(b"correlation-id", b"").decode()
            if correlation_id in self._pending:
                self._results[correlation_id] = json.loads(msg.value().decode())
                self._pending[correlation_id].set()

    def request(self, request_topic: str, payload: dict, timeout: float = 5.0) -> dict:
        correlation_id = str(uuid.uuid4())
        event = threading.Event()
        self._pending[correlation_id] = event

        self.producer.produce(
            request_topic,
            value=json.dumps(payload).encode(),
            headers=[
                ("correlation-id", correlation_id.encode()),
                ("reply-to", self.reply_topic.encode()),
            ],
        )
        self.producer.flush()

        if not event.wait(timeout=timeout):
            del self._pending[correlation_id]
            raise TimeoutError(f"No reply for {correlation_id} within {timeout}s")

        result = self._results.pop(correlation_id)
        del self._pending[correlation_id]
        return result

    def close(self):
        self._running = False
        self.consumer.close()


# ─── Run the demo ─────────────────────────────────────────────────────────────

server_thread = threading.Thread(target=pricing_server, daemon=True)
server_thread.start()
time.sleep(1)  # let server start

client = RequestReplyClient(BOOTSTRAP_SERVERS, REPLY_TOPIC)
time.sleep(1)  # let client consumer assign partitions

try:
    result = client.request(REQUEST_TOPIC, {"sku": "SKU-001", "quantity": 3})
    print(f"[Client] Price for 3x SKU-001: ${result['total']:.2f}")

    result = client.request(REQUEST_TOPIC, {"sku": "SKU-002", "quantity": 5})
    print(f"[Client] Price for 5x SKU-002: ${result['total']:.2f}")
finally:
    client.close()
```

> **When to use request-reply:** when you need a response but have a compelling reason to go through Kafka (audit trail, fan-out to multiple processors, existing Kafka infrastructure). If you just need a response, a direct HTTP call is simpler.

---

## 19.6 Fan-Out and Fan-In

### Fan-Out: One Producer, Many Consumer Groups

One topic, multiple independent consumer groups — each group gets all the messages.

```
                    ┌── analytics-group (reads all events for dashboards)
orders topic ──────>├── fraud-detection-group (real-time fraud scoring)
                    ├── inventory-group (update stock levels)
                    └── notification-group (send emails)
```

Each group maintains its own offset and processes independently. Adding a new consumer group has zero impact on existing groups — they don't know about each other.

```python
# Each service just subscribes with its own group.id
analytics = Consumer({"bootstrap.servers": "localhost:9092", "group.id": "analytics-group", ...})
fraud = Consumer({"bootstrap.servers": "localhost:9092", "group.id": "fraud-detection-group", ...})
inventory = Consumer({"bootstrap.servers": "localhost:9092", "group.id": "inventory-group", ...})

# All three subscribe to the same topic and receive all messages
for c in [analytics, fraud, inventory]:
    c.subscribe(["orders"])
```

### Fan-In: Many Producers, One Consumer Group

Multiple services produce to one topic; a single consumer group aggregates them.

```
order-service   ──>
payment-service ──> audit-events topic ──> audit-consumer-group (single stream)
shipping-service──>
```

```python
# Each service produces to the same topic with its own source identifier
producer = Producer({"bootstrap.servers": "localhost:9092"})

def produce_audit_event(source_service: str, event_type: str, payload: dict):
    event = {
        "source": source_service,
        "type": event_type,
        "payload": payload,
        "timestamp": time.time(),
    }
    producer.produce(
        "audit-events",
        key=source_service,  # key by source so events from same service are ordered
        value=json.dumps(event).encode(),
        headers=[("source", source_service.encode())],
    )
```

---

## 19.7 Data Pipeline Architecture

### Kafka as the Data Platform Backbone

```
Operational Systems          Kafka                     Analytical Systems
───────────────────          ──────                    ──────────────────
Web App          ──>         topics                    Data Warehouse
Mobile App       ──>         (raw events)    ──────>   (BigQuery, Redshift)
Order Service    ──>                         |
Payment Service  ──>    Kafka Streams /      ──────>   Data Lake (S3)
Legacy DB (CDC)  ──>    Faust / Spark
                         (enrichment,        ──────>   Real-time Dashboards
                          aggregation,       |          (Grafana, Superset)
                          filtering)         ──────>   ML Feature Store
```

### Lambda Architecture vs Kappa Architecture

**Lambda Architecture** uses two processing paths:
- **Batch layer** (Spark, Hadoop) — reprocesses all historical data periodically for accuracy
- **Speed layer** (Kafka Streams, Faust) — processes real-time data for freshness
- **Serving layer** — merges batch and speed views for queries

Downside: maintaining two codebases.

**Kappa Architecture** (simpler, Kafka-native):
- Only one path: stream processing
- Historical reprocessing done by replaying Kafka topics from the beginning
- Same code handles both historical and real-time

```python
# kappa_reprocess.py — replay historical events through the same pipeline

from confluent_kafka.admin import AdminClient
from confluent_kafka import Consumer

def reprocess_from_beginning(
    bootstrap_servers: str,
    group_id: str,
    topic: str,
):
    """
    Reset a consumer group's offsets to the beginning and
    let the existing processing pipeline re-consume everything.
    """
    from confluent_kafka import TopicPartition, OFFSET_BEGINNING
    from confluent_kafka.admin import ConsumerGroupTopicPartitions

    admin = AdminClient({"bootstrap.servers": bootstrap_servers})

    # Get all partitions
    metadata = admin.list_topics(topic=topic, timeout=10)
    partitions = list(metadata.topics[topic].partitions.keys())
    tps = [TopicPartition(topic, p, OFFSET_BEGINNING) for p in partitions]

    # Reset offsets (consumer group must be stopped first)
    future = admin.alter_consumer_group_offsets(
        [ConsumerGroupTopicPartitions(group_id, tps)]
    )
    for f in future.values():
        f.result()

    print(f"Offsets reset for group '{group_id}' on topic '{topic}'.")
    print("Restart the consumer group to begin reprocessing.")
```

---

## 19.8 Choosing the Right Pattern

| Problem | Pattern |
|---|---|
| Service A must react to something that happened in Service B | Event-Driven (event notification or ECST) |
| You need to audit every state change | Event Sourcing |
| You need separate read and write models for performance | CQRS |
| A business process spans multiple services | Saga / Choreography |
| You need atomic DB write + Kafka produce | Outbox Pattern |
| You need a response from a service via Kafka | Request-Reply |
| Multiple services need the same events | Fan-Out |
| You want a unified stream from multiple services | Fan-In |
| You need both real-time and historical processing | Kappa Architecture |
