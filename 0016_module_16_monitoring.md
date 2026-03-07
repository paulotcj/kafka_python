# Module 16 — Monitoring and Observability

## Overview

You cannot run Kafka in production without observability. This module covers three pillars:

1. **Metrics** — numbers over time: consumer lag, error rates, throughput, latency
2. **Logging** — structured logs with context for debugging specific events
3. **Distributed tracing** — following a message across multiple services

The most important single metric in any Kafka deployment is **consumer lag**. If lag grows, your consumers are falling behind and will eventually miss their SLA.

---

## 16.1 Key Metrics Reference

### Consumer Metrics (most critical)

| Metric | Description | Alert When |
|---|---|---|
| **Consumer lag** | How many messages behind the consumer is, per partition | Growing consistently |
| Records consumed rate | Messages processed per second | Drops significantly |
| Commit rate | Offset commits per second | Drops to 0 |
| Rebalance rate | How often the group rebalances | More than once per hour |
| Poll interval | Time between `poll()` calls | Exceeds `max.poll.interval.ms` |

### Producer Metrics

| Metric | Description | Alert When |
|---|---|---|
| Record error rate | Delivery failures per second | > 0 in production |
| Request latency | Time to broker acknowledgement | Exceeds SLA (e.g. 500ms) |
| Batch size avg | Average bytes per batch | Near `batch.size` limit |
| Buffer available bytes | Free space in producer buffer | < 20% remaining |
| Record queue time | Time a message waits in the buffer | Growing |

### Broker Metrics

| Metric | Description | Alert When |
|---|---|---|
| Under-replicated partitions | Partitions with fewer than RF in-sync replicas | > 0 |
| Offline partitions | Partitions with no leader | > 0 (critical) |
| ISR shrink rate | Rate at which ISR shrinks | > 0 |
| Log flush latency | Time to flush to disk | > 1s |
| Request handler utilization | Broker thread pool saturation | > 80% |
| Network handler utilization | Network thread saturation | > 80% |

---

## 16.2 Client-Side Metrics with stats_cb

`confluent-kafka` can report internal client metrics via a statistics callback. Set `statistics.interval.ms` to receive a JSON blob with everything the client knows about itself.

```python
import json
import time
from confluent_kafka import Consumer

def stats_callback(stats_json_str: str):
    """Called every statistics.interval.ms with a JSON metrics payload."""
    stats = json.loads(stats_json_str)

    # Top-level client info
    client_id = stats.get("client_id", "unknown")
    msg_cnt = stats.get("msg_cnt", 0)        # messages in all queues
    msg_size = stats.get("msg_size", 0)      # bytes in all queues

    # Per-broker round-trip latency
    brokers = stats.get("brokers", {})
    for broker_name, broker_stats in brokers.items():
        rtt = broker_stats.get("rtt", {})
        avg_rtt_ms = rtt.get("avg", 0) / 1000  # microseconds -> ms
        if avg_rtt_ms > 0:
            print(f"  Broker {broker_name}: avg RTT = {avg_rtt_ms:.1f}ms")

    # Per-topic partition stats (consumer)
    topics = stats.get("topics", {})
    for topic_name, topic_stats in topics.items():
        if topic_name.startswith("__"):
            continue
        partitions = topic_stats.get("partitions", {})
        for part_id, part_stats in partitions.items():
            if part_id == "-1":  # aggregate partition, skip
                continue
            consumer_lag = part_stats.get("consumer_lag", -1)
            fetch_state = part_stats.get("fetch_state", "")
            if consumer_lag >= 0:
                print(f"  {topic_name}[{part_id}]: lag={consumer_lag} state={fetch_state}")

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "monitored-consumer",
    "auto.offset.reset": "earliest",
    "statistics.interval.ms": 5000,   # emit stats every 5 seconds
    "on_commit": lambda err, partitions: None,
})
consumer.subscribe(["orders"])

# Register the stats callback
consumer.set_stats_cb(stats_callback)

print("Consumer running with metrics. Stats emitted every 5 seconds.")
empty = 0
while empty < 10:
    msg = consumer.poll(timeout=1.0)
    if msg is None:
        empty += 1
        continue
    empty = 0
    if not msg.error():
        print(f"Processed: {msg.topic()}[{msg.partition()}] offset={msg.offset()}")

consumer.close()
```

---

## 16.3 Consumer Lag Monitor

Lag is the difference between the partition's latest offset (high-water mark) and the consumer group's committed offset. A standalone lag monitor you can run as a sidecar process or scheduled job:

```python
# lag_monitor.py

import time
import json
from dataclasses import dataclass
from confluent_kafka import Consumer, TopicPartition
from confluent_kafka.admin import AdminClient, ConsumerGroupTopicPartitions

@dataclass
class PartitionLag:
    topic: str
    partition: int
    committed_offset: int
    high_water_mark: int
    lag: int

def get_lag(bootstrap_servers: str, group_id: str, topic: str) -> list[PartitionLag]:
    admin = AdminClient({"bootstrap.servers": bootstrap_servers})

    # Get all partitions for this topic
    metadata = admin.list_topics(topic=topic, timeout=10)
    partitions = list(metadata.topics[topic].partitions.keys())

    # Fetch committed offsets for this group
    tps = [TopicPartition(topic, p) for p in partitions]
    result = admin.list_consumer_group_offsets(
        [ConsumerGroupTopicPartitions(group_id, tps)]
    )
    committed = {}
    for future in result.values():
        for tp in future.result().topic_partitions:
            committed[tp.partition] = max(tp.offset, 0)  # -1 = not committed

    # Fetch high-water marks via a temporary consumer
    temp = Consumer({
        "bootstrap.servers": bootstrap_servers,
        "group.id": "__lag_monitor_temp__",
    })
    lags = []
    for p in partitions:
        lo, hi = temp.get_watermark_offsets(TopicPartition(topic, p), timeout=10)
        committed_offset = committed.get(p, 0)
        lag = max(0, hi - committed_offset)
        lags.append(PartitionLag(
            topic=topic,
            partition=p,
            committed_offset=committed_offset,
            high_water_mark=hi,
            lag=lag,
        ))
    temp.close()
    return lags

def monitor_lag(
    bootstrap_servers: str,
    group_id: str,
    topic: str,
    interval_seconds: int = 10,
    lag_alert_threshold: int = 1000,
):
    """Continuously monitor lag and alert when it exceeds the threshold."""
    print(f"Monitoring lag for group='{group_id}' topic='{topic}'")
    print(f"Alert threshold: {lag_alert_threshold} messages")
    print("-" * 60)

    while True:
        try:
            lags = get_lag(bootstrap_servers, group_id, topic)
            total_lag = sum(pl.lag for pl in lags)
            timestamp = time.strftime("%H:%M:%S")

            print(f"\n[{timestamp}] Total lag: {total_lag}")
            for pl in sorted(lags, key=lambda x: x.partition):
                alert = " <-- ALERT" if pl.lag > lag_alert_threshold else ""
                print(f"  Partition {pl.partition}: committed={pl.committed_offset} hwm={pl.high_water_mark} lag={pl.lag}{alert}")

            if total_lag > lag_alert_threshold * len(lags):
                print(f"\n  *** HIGH LAG ALERT: total lag {total_lag} exceeds threshold ***")

        except Exception as e:
            print(f"Error fetching lag: {e}")

        time.sleep(interval_seconds)

if __name__ == "__main__":
    monitor_lag(
        bootstrap_servers="localhost:9092",
        group_id="order-processing-group",
        topic="orders",
        interval_seconds=10,
        lag_alert_threshold=500,
    )
```

Run it:
```bash
python3 lag_monitor.py
```

---

## 16.4 Prometheus Metrics

For production, emit metrics to Prometheus so Grafana can visualise them.

```bash
pip install prometheus-client confluent-kafka
```

```python
# kafka_metrics_exporter.py
"""
Exposes Kafka client metrics as a Prometheus endpoint at http://localhost:8000/metrics
Run alongside your producer/consumer process.
"""

import json
import threading
import time
from prometheus_client import start_http_server, Gauge, Counter
from confluent_kafka import Consumer

# Define Prometheus metrics
CONSUMER_LAG = Gauge(
    "kafka_consumer_lag",
    "Consumer lag per partition",
    ["topic", "partition", "group_id"],
)
MESSAGES_CONSUMED = Counter(
    "kafka_messages_consumed_total",
    "Total messages consumed",
    ["topic", "partition"],
)
CONSUMER_RTT_MS = Gauge(
    "kafka_broker_rtt_ms",
    "Broker round-trip latency in milliseconds",
    ["broker"],
)

def make_stats_callback(group_id: str):
    def stats_callback(stats_json: str):
        stats = json.loads(stats_json)

        # Update RTT gauges
        for broker_name, broker_stats in stats.get("brokers", {}).items():
            rtt = broker_stats.get("rtt", {}).get("avg", 0)
            if rtt > 0:
                CONSUMER_RTT_MS.labels(broker=broker_name).set(rtt / 1000)

        # Update lag gauges
        for topic_name, topic_stats in stats.get("topics", {}).items():
            for part_id, part_stats in topic_stats.get("partitions", {}).items():
                if part_id == "-1":
                    continue
                lag = part_stats.get("consumer_lag", -1)
                if lag >= 0:
                    CONSUMER_LAG.labels(
                        topic=topic_name,
                        partition=part_id,
                        group_id=group_id,
                    ).set(lag)

    return stats_callback

GROUP_ID = "order-processing-group"
TOPIC = "orders"

# Start Prometheus HTTP server
start_http_server(8000)
print("Prometheus metrics available at http://localhost:8000/metrics")

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": GROUP_ID,
    "auto.offset.reset": "earliest",
    "statistics.interval.ms": 10000,
})
consumer.set_stats_cb(make_stats_callback(GROUP_ID))
consumer.subscribe([TOPIC])

empty = 0
while True:
    msg = consumer.poll(timeout=1.0)
    if msg is None:
        empty += 1
        if empty > 10:
            time.sleep(1)
        continue
    empty = 0
    if not msg.error():
        MESSAGES_CONSUMED.labels(
            topic=msg.topic(),
            partition=str(msg.partition()),
        ).inc()
        # ... your processing logic here
```

Visit `http://localhost:8000/metrics` to see the raw Prometheus metrics. Connect Prometheus to scrape this endpoint and add a Grafana dashboard.

---

## 16.5 Structured Logging

Log structured JSON with consistent fields so logs are searchable and correlatable.

```bash
pip install structlog
```

```python
# logging_setup.py

import structlog
import logging
import sys

def configure_logging(log_level: str = "INFO"):
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper()),
    )
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
```

```python
# structured_consumer.py

import json
import structlog
from confluent_kafka import Consumer
from logging_setup import configure_logging

configure_logging()
log = structlog.get_logger()

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "orders-structured-group",
    "auto.offset.reset": "earliest",
})
consumer.subscribe(["orders"])

empty = 0
while empty < 5:
    msg = consumer.poll(timeout=2.0)

    if msg is None:
        empty += 1
        continue
    empty = 0

    # Bind message metadata to every log line within this block
    msg_log = log.bind(
        topic=msg.topic(),
        partition=msg.partition(),
        offset=msg.offset(),
    )

    if msg.error():
        msg_log.error("kafka_consumer_error", error=str(msg.error()))
        continue

    try:
        order = json.loads(msg.value().decode("utf-8"))

        # Bind business context (correlation ID from headers if present)
        headers = dict(msg.headers() or [])
        trace_id = headers.get(b"trace-id", b"").decode("utf-8")

        msg_log = msg_log.bind(
            order_id=order.get("order_id"),
            trace_id=trace_id or "none",
        )

        # Do NOT log the full message payload in production —
        # it may contain PII or sensitive data
        msg_log.info("processing_order", amount=order.get("amount"))

        # ... processing ...

        consumer.commit(message=msg, asynchronous=False)
        msg_log.info("order_processed")

    except json.JSONDecodeError as e:
        msg_log.error("deserialization_failed", error=str(e))
    except Exception as e:
        msg_log.exception("processing_failed", error=str(e))

consumer.close()
log.info("consumer_shutdown")
```

Output (one JSON object per line):
```json
{"topic": "orders", "partition": 0, "offset": 42, "order_id": "ord-123", "trace_id": "abc-def", "event": "processing_order", "amount": 99.99, "timestamp": "2025-01-01T12:00:00Z", "level": "info"}
{"topic": "orders", "partition": 0, "offset": 42, "order_id": "ord-123", "trace_id": "abc-def", "event": "order_processed", "timestamp": "2025-01-01T12:00:00.050Z", "level": "info"}
```

### What Not to Log

- **Full message payloads** — may contain PII (email, credit card numbers, addresses)
- **Credentials** — never log `bootstrap.servers` passwords or SSL keys
- **Message values** — log message keys and offsets for traceability, not values

---

## 16.6 Distributed Tracing with OpenTelemetry

Distributed tracing shows the journey of a single request or event across multiple services. The trace ID is injected into Kafka message headers by the producer and extracted by the consumer.

```bash
pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-jaeger
```

### Producer — Inject Trace Context into Headers

```python
import uuid
import json
from confluent_kafka import Producer
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.jaeger.thrift import JaegerExporter

# Set up tracing (Jaeger for local dev; replace with your backend in production)
provider = TracerProvider()
jaeger_exporter = JaegerExporter(agent_host_name="localhost", agent_port=6831)
provider.add_span_processor(BatchSpanProcessor(jaeger_exporter))
trace.set_tracer_provider(provider)
tracer = trace.get_tracer("order-service")

producer = Producer({"bootstrap.servers": "localhost:9092"})

def publish_order(order: dict):
    with tracer.start_as_current_span("publish_order") as span:
        # Generate a trace/span ID to propagate downstream
        trace_id = format(span.get_span_context().trace_id, "032x")
        span_id = format(span.get_span_context().span_id, "016x")

        span.set_attribute("order.id", order.get("order_id"))
        span.set_attribute("kafka.topic", "orders")

        # Inject trace context into message headers
        headers = [
            ("trace-id", trace_id.encode()),
            ("span-id", span_id.encode()),
            ("service", b"order-service"),
        ]

        producer.produce(
            "orders",
            key=order["order_id"],
            value=json.dumps(order).encode(),
            headers=headers,
        )

publish_order({"order_id": "ord-001", "amount": 99.99})
producer.flush()
```

### Consumer — Extract Trace Context from Headers

```python
import json
from confluent_kafka import Consumer
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import SpanContext, TraceFlags, NonRecordingSpan

tracer = trace.get_tracer("enrichment-service")

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "enrichment-group",
    "auto.offset.reset": "earliest",
})
consumer.subscribe(["orders"])

empty = 0
while empty < 5:
    msg = consumer.poll(timeout=2.0)
    if msg is None:
        empty += 1
        continue
    empty = 0
    if msg.error():
        continue

    # Extract trace context from headers
    headers = dict(msg.headers() or [])
    trace_id_hex = headers.get(b"trace-id", b"").decode("utf-8")
    span_id_hex = headers.get(b"span-id", b"").decode("utf-8")
    source_service = headers.get(b"service", b"unknown").decode("utf-8")

    # Create a child span linked to the upstream trace
    ctx = None
    if trace_id_hex and span_id_hex:
        parent_ctx = SpanContext(
            trace_id=int(trace_id_hex, 16),
            span_id=int(span_id_hex, 16),
            is_remote=True,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
        )
        ctx = trace.set_span_in_context(NonRecordingSpan(parent_ctx))

    with tracer.start_as_current_span("process_order", context=ctx) as span:
        order = json.loads(msg.value().decode("utf-8"))
        span.set_attribute("order.id", order.get("order_id"))
        span.set_attribute("kafka.partition", msg.partition())
        span.set_attribute("kafka.offset", msg.offset())
        span.set_attribute("upstream.service", source_service)

        print(f"Processing {order['order_id']} (trace: {trace_id_hex[:8]}...)")
        # ... processing ...

consumer.close()
```

---

## 16.7 Kafka UI and Visual Tools

### Kafka UI (already in Docker Compose)

Visit `http://localhost:8080` for:
- Topic browser with message content, keys, headers, and timestamps
- Consumer group lag dashboard (per partition)
- Broker overview (ISR health, under-replicated partitions)
- Schema Registry integration (if configured)
- Kafka Connect status (if configured)

### CLI-based Monitoring

```bash
# Consumer lag (all groups)
docker exec kafka /opt/kafka/bin/kafka-consumer-groups.sh \
  --bootstrap-server localhost:9092 \
  --list

# Lag for a specific group
docker exec kafka /opt/kafka/bin/kafka-consumer-groups.sh \
  --bootstrap-server localhost:9092 \
  --describe --group order-processing-group

# Watch lag in real-time (refreshes every 3 seconds)
watch -n 3 "docker exec kafka /opt/kafka/bin/kafka-consumer-groups.sh \
  --bootstrap-server localhost:9092 \
  --describe --group order-processing-group"

# Topic throughput (messages per second over 30 seconds)
docker exec kafka /opt/kafka/bin/kafka-consumer-perf-test.sh \
  --bootstrap-server localhost:9092 \
  --topic orders \
  --messages 100000 \
  --group perf-test-group \
  --threads 1

# Describe topic metadata (leader, ISR)
docker exec kafka /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server localhost:9092 \
  --describe --topic orders
```

### Quick Health Check Script

```python
# health_check.py
"""Quick Kafka cluster health check — run periodically or in CI."""

from confluent_kafka.admin import AdminClient

def check_cluster_health(bootstrap_servers: str) -> bool:
    admin = AdminClient({"bootstrap.servers": bootstrap_servers})
    metadata = admin.list_topics(timeout=10)

    issues = []

    for topic_name, topic in metadata.topics.items():
        if topic_name.startswith("__"):
            continue
        for partition_id, partition in topic.partitions.items():
            if partition.leader == -1:
                issues.append(f"NO LEADER: {topic_name}[{partition_id}]")
            if len(partition.isrs) < len(partition.replicas):
                issues.append(
                    f"UNDER-REPLICATED: {topic_name}[{partition_id}] "
                    f"ISR={partition.isrs} Replicas={partition.replicas}"
                )

    if issues:
        print("UNHEALTHY:")
        for issue in issues:
            print(f"  {issue}")
        return False
    else:
        topics = [t for t in metadata.topics if not t.startswith("__")]
        print(f"HEALTHY: {len(metadata.brokers)} brokers, {len(topics)} topics, no issues")
        return True

if __name__ == "__main__":
    healthy = check_cluster_health("localhost:9092")
    exit(0 if healthy else 1)
```
