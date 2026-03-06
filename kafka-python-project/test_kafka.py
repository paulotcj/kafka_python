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
empty_polls = 0
while empty_polls < 3:
    msg = consumer.poll(timeout=2.0)
    if msg is None:
        empty_polls += 1
        continue
    empty_polls = 0
    if msg.error():
        print(f"Error: {msg.error()}")
    else:
        print(f"Received: {msg.value().decode('utf-8')} "
              f"[partition={msg.partition()}, offset={msg.offset()}]")

consumer.close()