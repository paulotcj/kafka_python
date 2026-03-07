# Module 20 — Production Deployment Considerations

## Overview

Running Kafka locally with Docker is very different from running it in production. Production Kafka requires deliberate decisions about cluster sizing, high availability, security, operational readiness, and cost. This module covers what changes — and what you must plan for — when moving beyond a local development cluster.

---

## 20.1 Managed vs Self-Managed Kafka

Before sizing and configuring a cluster yourself, consider whether a managed service fits your needs.

| Option | Examples | Best For |
|---|---|---|
| **Managed cloud** | Confluent Cloud, Amazon MSK, Aiven, Upstash | Most teams — no ops burden |
| **Self-managed on Kubernetes** | Strimzi, Confluent Operator | Full control, cost optimisation at scale |
| **Self-managed on VMs** | Raw EC2/GCP instances | Maximum control, lowest cloud overhead |

**When managed services make sense:**
- You want to focus on application code, not Kafka operations
- Your team does not have Kafka expertise
- You need elastic scaling without provisioning effort
- You need 99.99%+ SLA with minimal operational overhead

**When self-managed makes sense:**
- Very high throughput (>500 MB/s) where managed cost is prohibitive
- Regulatory requirements preventing data from leaving your own infrastructure
- You have dedicated Kafka expertise in-house

---

## 20.2 Cluster Sizing

### Broker Count

| Replication Factor | Minimum Brokers | Recommended Brokers |
|---|---|---|
| 1 (no redundancy) | 1 | Development only |
| 2 | 2 | Small non-critical workloads |
| 3 | 3 | Production standard |
| 3 + cross-rack | 3 (1 per rack/AZ) | High availability production |

The minimum viable production cluster is **3 brokers** with replication factor 3. This allows one broker failure with no data loss and no service interruption.

### Disk Capacity Planning

```
Daily retention per topic = throughput (bytes/sec) × 86400 × replication_factor
Total cluster disk = sum(daily retention per topic) × retention_days
                   + 20% headroom
```

Example:
- Topic `orders`: 10 MB/s, RF=3, 7-day retention
  - 10 × 86400 × 3 × 7 = 18.1 TB raw
- Topic `user-events`: 50 MB/s, RF=3, 3-day retention
  - 50 × 86400 × 3 × 3 = 38.9 TB raw

```python
# capacity_planner.py

def plan_disk_capacity(topics: list[dict]) -> dict:
    """
    topics: list of dicts with keys:
      name, throughput_mb_s, replication_factor, retention_days
    """
    total_gb = 0
    print(f"{'Topic':<30} {'Throughput':>12} {'RF':>4} {'Retention':>10} {'Disk GB':>10}")
    print("-" * 70)

    for t in topics:
        daily_gb = t["throughput_mb_s"] * 86400 / 1024
        total_topic_gb = daily_gb * t["replication_factor"] * t["retention_days"]
        total_gb += total_topic_gb
        print(
            f"{t['name']:<30} "
            f"{t['throughput_mb_s']:>10.1f} MB/s "
            f"{t['replication_factor']:>4} "
            f"{t['retention_days']:>8}d "
            f"{total_topic_gb:>10.0f} GB"
        )

    recommended_gb = total_gb * 1.2  # 20% headroom
    print("-" * 70)
    print(f"{'Total (raw)':<50} {total_gb:>10.0f} GB")
    print(f"{'Total + 20% headroom':<50} {recommended_gb:>10.0f} GB")
    print(f"{'Per broker (3 brokers, RF=3)':<50} {recommended_gb / 3:>10.0f} GB")
    return {"total_gb": total_gb, "recommended_gb": recommended_gb}

plan_disk_capacity([
    {"name": "orders",            "throughput_mb_s": 10,  "replication_factor": 3, "retention_days": 7},
    {"name": "user-events",       "throughput_mb_s": 50,  "replication_factor": 3, "retention_days": 3},
    {"name": "payment-events",    "throughput_mb_s": 5,   "replication_factor": 3, "retention_days": 30},
    {"name": "audit-log",         "throughput_mb_s": 2,   "replication_factor": 3, "retention_days": 90},
])
```

### Memory and CPU

| Component | Recommendation |
|---|---|
| JVM heap per broker | 4–8 GB (do not exceed 8 GB — GC pauses) |
| OS page cache | As much RAM as possible (Kafka's primary read cache) |
| Total RAM per broker | 32–64 GB (6 GB heap + page cache) |
| CPU per broker | 4–8 cores (Kafka is not very CPU-intensive) |
| Network per broker | 10 Gbps recommended for high-throughput clusters |

Kafka relies heavily on the OS **page cache** to serve reads from memory rather than disk. A broker with 32 GB RAM and 6 GB heap has ~26 GB of page cache — enough to serve several hours of data at high throughput without disk reads.

---

## 20.3 High Availability

### Rack Awareness

Distribute replicas across failure domains (AWS Availability Zones, physical racks):

```yaml
# broker 1 in AZ us-east-1a
KAFKA_BROKER_RACK: us-east-1a

# broker 2 in AZ us-east-1b
KAFKA_BROKER_RACK: us-east-1b

# broker 3 in AZ us-east-1c
KAFKA_BROKER_RACK: us-east-1c
```

With rack awareness, Kafka ensures that replicas of a partition are placed on brokers in different AZs. If an entire AZ fails, you still have at least one in-sync replica on a surviving broker.

```bash
# Verify rack assignment
docker exec kafka-1 /opt/kafka/bin/kafka-topics.sh \
  --describe \
  --topic orders \
  --bootstrap-server kafka-1:9094
# Shows Replicas: 1,2,3 spread across racks
```

### min.insync.replicas

`min.insync.replicas` defines the minimum number of replicas that must be in sync before a write is acknowledged. Combined with `acks=all`:

```
acks=all + min.insync.replicas=2 + replication_factor=3
  = write succeeds if at least 2 of 3 replicas are in sync
  = can tolerate 1 broker failure without blocking writes
  = 1 broker failure still blocks writes if RF=2 (no slack)
```

```yaml
KAFKA_MIN_INSYNC_REPLICAS: 2
```

Or per topic:
```bash
docker exec kafka-1 /opt/kafka/bin/kafka-configs.sh \
  --bootstrap-server kafka-1:9094 \
  --alter \
  --entity-type topics \
  --entity-name orders \
  --add-config min.insync.replicas=2
```

### Cross-Region Replication with MirrorMaker 2

For disaster recovery or data locality, replicate topics across Kafka clusters in different regions using MirrorMaker 2 (MM2):

```yaml
# mirrormaker2.yaml (simplified config)
clusters = primary, secondary

primary.bootstrap.servers = kafka.us-east.example.com:9092
secondary.bootstrap.servers = kafka.eu-west.example.com:9092

primary->secondary.enabled = true
primary->secondary.topics = orders, user-events, payment-events
primary->secondary.replication.factor = 3

# Offset sync: enables consumers to resume from the correct offset
# after failover without reprocessing everything
primary->secondary.sync.group.offsets.enabled = true
primary->secondary.sync.group.offsets.interval.seconds = 60
```

MirrorMaker 2 creates topic mirrors named `{source-cluster}.{topic}` on the destination (e.g. `primary.orders`). After a failover:
1. Update consumer `bootstrap.servers` to point to the secondary cluster
2. Update `auto.offset.reset` or use the synced offsets

---

## 20.4 Rolling Upgrades

Upgrade brokers one at a time without downtime:

```bash
# Step 1: Check all brokers are healthy
docker exec kafka-1 /opt/kafka/bin/kafka-topics.sh \
  --describe --bootstrap-server kafka-1:9094 | grep -v "Isr:.*Replicas"
# All partitions should be fully in-sync before proceeding

# Step 2: Stop one broker
docker compose stop kafka-3

# Step 3: Update the image in docker-compose.yml (or apply config change)
# Then restart it
docker compose up -d kafka-3

# Step 4: Wait for kafka-3 to rejoin and all ISRs to be restored
watch docker exec kafka-1 /opt/kafka/bin/kafka-topics.sh \
  --describe --bootstrap-server kafka-1:9094 | grep "kafka-3"

# Step 5: Repeat for kafka-2, then kafka-1
```

**Key rules:**
- Never stop more brokers than `RF - min.insync.replicas` simultaneously
- With RF=3, min.insync.replicas=2: stop only 1 broker at a time
- Wait for ISR to fully restore before stopping the next broker
- Check under-replicated partitions are 0 before proceeding

---

## 20.5 Disaster Recovery

### Recovery Point Objective (RPO) and Recovery Time Objective (RTO)

| Scenario | RPO | RTO | Approach |
|---|---|---|---|
| Single broker failure | 0 (replication) | Seconds (leader election) | 3-broker cluster with RF=3 |
| Full cluster failure | Minutes (MirrorMaker lag) | Minutes–Hours | Active-passive DR cluster |
| Region failure | Seconds (sync offset) | Minutes | Active-active multi-region |

### Active-Passive DR Runbook

**Scenario:** Primary region (us-east) is down. Fail over to secondary (eu-west).

```bash
# Step 1: Verify the primary is unreachable (don't switch on a false alarm)
ping kafka.us-east.example.com
curl --connect-timeout 5 http://kafka-ui.us-east.example.com

# Step 2: Check MirrorMaker 2 lag before the failure
docker exec mirrormaker /opt/kafka/bin/kafka-consumer-groups.sh \
  --bootstrap-server kafka.eu-west.example.com:9092 \
  --describe --group mirrormaker2-primary.orders
# Note the lag — this is your RPO (data not yet replicated)

# Step 3: Switch application bootstrap.servers to the DR cluster
# (done via config update, Kubernetes ConfigMap change, or DNS failover)
# export KAFKA_BOOTSTRAP_SERVERS=kafka.eu-west.example.com:9092

# Step 4: Consumers will start from synced offsets (if MM2 sync was enabled)
# Or use auto.offset.reset=latest to continue from current position (may miss some)

# Step 5: After primary recovers, resync data and plan failback
```

---

## 20.6 Operational Runbooks

### Runbook: Broker is Down

**Symptoms:** Kafka UI shows fewer than expected online brokers. Under-replicated partitions > 0.

```bash
# 1. Identify which broker is down
docker compose ps
# or check your monitoring (broker online metric = 0)

# 2. Check the broker's logs for the cause
docker compose logs kafka-2 --tail=100

# 3. If the broker process crashed (not a machine failure), restart it
docker compose start kafka-2

# 4. Monitor ISR restoration
watch -n 5 "docker exec kafka-1 /opt/kafka/bin/kafka-topics.sh \
  --describe --bootstrap-server kafka-1:9094 | grep 'Isr' | grep -v '1,2,3'"
# When this shows no output, all ISRs are restored

# 5. If the broker won't start, check for disk full or corruption
docker exec kafka-2 df -h /tmp/kraft-combined-logs
```

### Runbook: Consumer Lag is Growing

**Symptoms:** `kafka_consumer_lag` metric is increasing. Messages are accumulating faster than they are being processed.

```bash
# 1. Check current lag
docker exec kafka /opt/kafka/bin/kafka-consumer-groups.sh \
  --bootstrap-server localhost:9092 \
  --describe --group order-processing-group

# 2. Check how many consumers are active in the group
# (look at the CONSUMER-ID column — should have entries for each partition)

# 3. If consumers are running but slow:
#    a. Check processing time per message (application logs, tracing)
#    b. Look for external bottlenecks (database calls, HTTP calls)
#    c. Increase max.poll.records to process larger batches
#    d. Add more consumer instances (up to partition count)

# 4. If consumers have crashed:
docker compose ps   # or kubectl get pods for Kubernetes
# Restart the consumer service

# 5. Emergency: if lag is catastrophic and you need to skip ahead
docker exec kafka /opt/kafka/bin/kafka-consumer-groups.sh \
  --bootstrap-server localhost:9092 \
  --reset-offsets --to-latest \
  --group order-processing-group \
  --topic orders \
  --execute
# WARNING: this skips all queued messages — use only if you can afford the data loss
```

### Runbook: Topic is Running Out of Disk Space

**Symptoms:** Broker disk usage approaching 80%+. Log warns about insufficient space.

```bash
# 1. Check disk usage
docker exec kafka du -sh /tmp/kraft-combined-logs/*

# 2. Find the largest topics
docker exec kafka du -sh /tmp/kraft-combined-logs/* | sort -rh | head -20

# 3. Reduce retention on heavy topics temporarily
docker exec kafka /opt/kafka/bin/kafka-configs.sh \
  --bootstrap-server localhost:9092 \
  --alter \
  --entity-type topics \
  --entity-name user-events \
  --add-config retention.ms=3600000   # reduce to 1 hour temporarily

# 4. Trigger log cleanup (normally happens automatically)
docker exec kafka /opt/kafka/bin/kafka-log-dirs.sh \
  --bootstrap-server localhost:9092 \
  --topic-list user-events

# 5. Long term: add brokers and rebalance partitions, or increase broker disk
```

### Runbook: Rebalance Storm

**Symptoms:** Consumers are constantly rebalancing. Lag spikes repeatedly. Logs show frequent "Revoked partitions" and "Assigned partitions" messages.

**Common causes and fixes:**

```
Cause: Processing takes longer than max.poll.interval.ms
Fix:   Increase max.poll.interval.ms or reduce processing time per batch

Cause: GC pause causing heartbeat timeout
Fix:   Tune JVM GC, increase session.timeout.ms

Cause: Rolling deployment triggering repeated rebalances
Fix:   Use static group membership (group.instance.id)

Cause: Too many consumers joining/leaving (scaling events)
Fix:   Use cooperative-sticky assignor to reduce partition movement
```

```python
# Applying fixes in Python
consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "order-processing-group",

    # Fix 1: give processing more time before being considered dead
    "max.poll.interval.ms": 600000,     # 10 minutes

    # Fix 2: more aggressive heartbeating
    "session.timeout.ms": 60000,
    "heartbeat.interval.ms": 20000,

    # Fix 3: static membership (survives restarts without rebalance)
    "group.instance.id": "order-worker-1",  # unique per instance

    # Fix 4: cooperative rebalancing (partitions not revoked during rebalance)
    "partition.assignment.strategy": "cooperative-sticky",
})
```

---

## 20.7 Kubernetes Deployment with Strimzi

Strimzi is the most popular Kubernetes operator for Kafka. It manages Kafka clusters as Kubernetes custom resources.

```yaml
# kafka-cluster.yaml (Strimzi custom resource)
apiVersion: kafka.strimzi.io/v1beta2
kind: Kafka
metadata:
  name: production-cluster
  namespace: kafka
spec:
  kafka:
    version: 3.7.0
    replicas: 3
    listeners:
      - name: plain
        port: 9092
        type: internal
        tls: false
      - name: tls
        port: 9093
        type: internal
        tls: true
    config:
      offsets.topic.replication.factor: 3
      transaction.state.log.replication.factor: 3
      transaction.state.log.min.isr: 2
      default.replication.factor: 3
      min.insync.replicas: 2
      inter.broker.protocol.version: "3.7"
    storage:
      type: jbod
      volumes:
        - id: 0
          type: persistent-claim
          size: 500Gi
          class: fast-ssd
          deleteClaim: false
    resources:
      requests:
        memory: 8Gi
        cpu: 2
      limits:
        memory: 16Gi
        cpu: 4
    rack:
      topologyKey: topology.kubernetes.io/zone
  zookeeper:   # or entityOperator for KRaft mode
    replicas: 3
    storage:
      type: persistent-claim
      size: 10Gi
      class: fast-ssd
  entityOperator:
    topicOperator: {}
    userOperator: {}
```

Apply:
```bash
kubectl apply -f kafka-cluster.yaml

# Create a topic as a Kubernetes resource
kubectl apply -f - <<EOF
apiVersion: kafka.strimzi.io/v1beta2
kind: KafkaTopic
metadata:
  name: orders
  namespace: kafka
  labels:
    strimzi.io/cluster: production-cluster
spec:
  partitions: 12
  replicas: 3
  config:
    retention.ms: 604800000  # 7 days
    min.insync.replicas: "2"
EOF
```

---

## 20.8 Pre-Production Checklist

Use this checklist before going live.

**Cluster Configuration**
- [ ] 3+ brokers across 3 availability zones
- [ ] Replication factor ≥ 3 for all production topics
- [ ] `min.insync.replicas=2` for all production topics
- [ ] Rack awareness configured
- [ ] `KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=3`
- [ ] `KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR=3`
- [ ] `KAFKA_TRANSACTION_STATE_LOG_MIN_ISR=2`

**Security**
- [ ] PLAINTEXT listener disabled or firewall-blocked
- [ ] TLS enabled for all client-facing listeners
- [ ] SASL authentication configured
- [ ] ACLs enabled (`allow.everyone.if.no.acl.found=false`)
- [ ] Each service has its own principal with least-privilege ACLs
- [ ] No credentials in source code or container images

**Producers**
- [ ] `acks=all` for critical data
- [ ] `enable.idempotence=True` for critical producers
- [ ] `delivery.timeout.ms` configured (don't leave it unlimited)
- [ ] Delivery callbacks implemented and failures logged
- [ ] `flush()` called before shutdown

**Consumers**
- [ ] `enable.auto.commit=False` for at-least-once processing
- [ ] Manual commit after successful processing
- [ ] Graceful shutdown handler (SIGTERM → stop polling → close)
- [ ] Dead letter queue configured for unprocessable messages
- [ ] `max.poll.interval.ms` tuned to processing time

**Operations**
- [ ] Consumer lag alerting (alert when lag grows for 5+ minutes)
- [ ] Under-replicated partitions alerting (alert immediately if > 0)
- [ ] Offline partitions alerting (critical alert immediately if > 0)
- [ ] Disk usage alerting (alert at 70%, critical at 85%)
- [ ] Runbooks documented for common failure scenarios
- [ ] DR procedure tested (not just documented)
- [ ] Retention policies match data lifecycle requirements
- [ ] Schema Registry running (if using Avro/Protobuf)
- [ ] Topic names follow naming convention
- [ ] Partition counts planned for peak load (can only increase, not decrease)
