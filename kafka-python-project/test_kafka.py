# =============================================================================
# test_kafka.py — Kafka smoke test: produce and consume messages from Python
# =============================================================================
#
# This script connects to a local Kafka cluster running in Docker, sends 5
# messages to a topic, then reads them back. It is meant to verify that your
# environment is working before moving on to more complex examples.
#
# Prerequisites:
#   - Docker Kafka stack is running (docker compose up -d)
#   - The topic "my-first-topic" exists. If it does not, create it:
#       docker exec kafka /opt/kafka/bin/kafka-topics.sh --create \
#         --topic my-first-topic \
#         --bootstrap-server localhost:9092 \
#         --partitions 3 \
#         --replication-factor 1
#   - confluent-kafka is installed: pip install confluent-kafka
# =============================================================================

from confluent_kafka import Producer as kafka_producer, Consumer as kafka_consumer


# =============================================================================
# BOOTSTRAP SERVERS — how Python finds the Kafka cluster
# =============================================================================
#
# "bootstrap.servers" is the entry point address(es) your Python client uses
# to make its first connection to Kafka. You do NOT need to list every broker.
#
# What happens under the hood:
#   1. The client connects to any one broker in this list.
#   2. That broker responds with the full list of all brokers in the cluster
#      and which partitions each one is responsible for (this is called the
#      cluster metadata).
#   3. From that point on, the client talks directly to whichever broker
#      owns the partition it needs — it no longer depends on the bootstrap
#      address.
#
# Why "localhost:9092" for the multi-broker setup?
#   Look at the docker-compose.yml — kafka-1 maps its internal port 9092 to
#   port 9092 on your Mac ("9092:9092"). kafka-2 maps to 9095, kafka-3 to 9096.
#   So from your Mac, the three brokers are reachable at:
#       localhost:9092  →  kafka-1
#       localhost:9095  →  kafka-2
#       localhost:9096  →  kafka-3
#
#   Any one of them works as the bootstrap address because after the first
#   connection Kafka hands back the full cluster metadata. For resilience it is
#   best practice to list all three so the client can still bootstrap even if
#   one broker is down:
#
BOOTSTRAP_SERVERS = "localhost:9092,localhost:9095,localhost:9096"
#
#   If you are using the single-broker setup (kafka-docker), use:
#       BOOTSTRAP_SERVERS = "localhost:9092"
#
# There is no "leader" from the client's perspective at this level — every
# broker can answer a bootstrap request. Leadership is a per-partition concept:
# each partition has exactly one leader broker that handles reads and writes for
# that partition. The client learns this from the metadata response and routes
# requests accordingly, automatically.
# =============================================================================

TOPIC = "my-first-topic"


# =============================================================================
# PRODUCER
# =============================================================================
#
# A Producer sends messages to Kafka topics.
#
# When you call producer.produce(), the message is placed into a local in-memory
# buffer. It is NOT immediately sent to the broker. The client batches messages
# and sends them in the background for efficiency.
#
# producer.flush() blocks until all buffered messages have been delivered to the
# broker (or an error occurs). Always call flush() before your program exits —
# without it, buffered messages can be silently lost.
#
print("=============================================================================")
print("PRODUCER")
print("=============================================================================")

config_param : dict[str,str] = {"bootstrap.servers": BOOTSTRAP_SERVERS}
producer = kafka_producer(config_param)

for i in range(5):
    producer.produce(
        topic = TOPIC,
        value = f"Message {i}".encode("utf-8"),  # Kafka messages are raw bytes,
                                                # so we encode the string to UTF-8
    )
    print(f"Buffered: Message {i}")

print("Flushing — waiting for all messages to reach the broker...")
producer.flush()
print("All messages delivered.\n")


# =============================================================================
# CONSUMER
# =============================================================================
#
# A Consumer reads messages from one or more Kafka topics.
#
# Key configuration options explained:
#
#   bootstrap.servers  — same as the producer: the entry point to the cluster.
#
#   group.id           — a string that identifies this consumer as part of a
#                        consumer group. Kafka uses this to track which messages
#                        have already been processed by this group (via offsets).
#                        If you run this script twice with the same group.id,
#                        the second run will NOT re-read the messages because
#                        Kafka remembers the group already processed them.
#                        Change the group.id (e.g. "my-test-group-2") to read
#                        from the beginning again.
#
#   auto.offset.reset  — controls what to do when this consumer group has no
#                        previously committed offset for a partition (i.e. it
#                        is reading this topic for the first time, or the
#                        group.id has never been used before).
#                        "earliest" — start from the very first message in
#                                     the topic (read all history).
#                        "latest"   — start from now, only read new messages
#                                     that arrive after the consumer starts.
#
print("=============================================================================")
print("CONSUMER")
print("=============================================================================")


consumer_config_dict : dict[str,str] =     {
        "bootstrap.servers" : BOOTSTRAP_SERVERS,
        "group.id"          : "my-test-group", # arbitrary string to define my consumer group, read comments above
        "auto.offset.reset" : "earliest",
    }
consumer = kafka_consumer(consumer_config_dict)

# subscribe() tells the consumer which topics to read from.
# This triggers a "rebalance" — Kafka assigns partitions of the topic to this
# consumer. The rebalance happens asynchronously in the background, which is
# why the first few poll() calls may return None even though messages exist.
consumer.subscribe([TOPIC])

print(f"Subscribed to '{TOPIC}'. Waiting for partition assignment and messages...\n")

# poll(timeout) asks the broker for new messages and waits up to `timeout`
# seconds for a response. It returns:
#   - A Message object if a message was received
#   - None if no message arrived within the timeout window
#
# Important: poll() returning None does NOT mean the topic is empty.
# During the initial rebalance (partition assignment), poll() returns None
# for a short period. We use an empty_polls counter to distinguish between
# "still waiting for assignment" and "genuinely no more messages".
# We stop only after 3 consecutive empty polls.
#
empty_polls = 0
while empty_polls < 3:
    msg = consumer.poll(timeout=2.0)

    if msg is None:
        # No message in this poll window — could be rebalancing or end of data
        empty_polls += 1
        continue

    # Reset the counter — we are actively receiving messages
    empty_polls = 0

    if msg.error():
        # Errors can be informational (e.g. partition EOF) or real errors.
        # For this smoke test we just print them.
        print(f"Error: {msg.error()}")
    else:
        # msg.value()     — the raw bytes payload
        # msg.topic()     — the topic name
        # msg.partition() — which partition (0, 1, or 2) this message came from
        # msg.offset()    — position of this message within that partition
        #                   (monotonically increasing integer, starts at 0)
        print(
            f"Received: {   msg.value().decode('utf-8') }"
            f"  [topic={    msg.topic()                 }"
            f"  partition={ msg.partition()             }"
            f"  offset={    msg.offset()                }]"
        )

# Always close the consumer when done. This commits any pending offsets and
# sends a "leave group" signal to Kafka, which triggers an immediate rebalance
# to reassign partitions to other consumers in the group (if any).
consumer.close()
print("\nConsumer closed.")
