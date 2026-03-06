
# from confluent_kafka import Producer as kafka_producer, Consumer as kafka_consumer

#----
from confluent_kafka import Producer as kafka_producer , Consumer as kafka_consumer


# BOOTSTRAP_SERVERS = "localhost:9092,localhost:9095,localhost:9096"
BOOTSTRAP_SERVERS = 'localhost:9092,localhost9095,localhost:9096'

# TOPIC = "my-first-topic"

TOPIC = "my-first-topic"

# producer = kafka_producer({"bootstrap.servers": BOOTSTRAP_SERVERS})

prod_args : dict[str:str] = { "bootstrap.servers" : BOOTSTRAP_SERVERS }
producer = kafka_producer(prod_args)

# #-----
# for i in range(5):
#     producer.produce(
#         topic = TOPIC,
#         value = f"Message {i}".encode("utf-8"),  # Kafka messages are raw bytes,
#                                                 # so we encode the string to UTF-8
#     )
#     print(f"Buffered: Message {i}")
#-----
for i in range(5) : 
    producer.produce( topic= TOPIC , value= f"Message {i}".encode("utf-8") )
    print(f"Buffered: Message {i}")
#-----

print("Flushing — waiting for all messages to reach the broker...")
# producer.flush()
producer.flush()
print("All messages delivered.\n")



print("=============================================================================")
print("CONSUMER")
print("=============================================================================")

# consumer = kafka_consumer(
#     {
#         "bootstrap.servers" : BOOTSTRAP_SERVERS,
#         "group.id"          : "my-test-group", # arbitrary string to define my consumer group, read comments above
#         "auto.offset.reset" : "earliest",
#     }
# )

consumer_config_dict : dict[str:str ] = {
    'bootstrap.servers' : BOOTSTRAP_SERVERS,
    'group.id' : 'my-test-group',
    'auto.offset.reset' : 'earliest'
}
consumer = kafka_consumer( consumer_config_dict )


# consumer.subscribe([TOPIC])
consumer.subscribe( [ TOPIC ] )

print(f"Subscribed to '{TOPIC}'. Waiting for partition assignment and messages...\n")


# empty_polls = 0
empty_polls : int = 0
#-----
# while empty_polls < 3:
while empty_polls < 3 : 
    # msg = consumer.poll(timeout=2.0)
    msg = consumer.poll( timeout= 2.0 )

    # if msg is None:
    #     # No message in this poll window — could be rebalancing or end of data
    #     empty_polls += 1
    #     continue

    if msg is None:
        empty_polls += 1
        continue

    # Reset the counter — we are actively receiving messages
    # empty_polls = 0

    empty_polls = 0

    # if msg.error():
    #     # Errors can be informational (e.g. partition EOF) or real errors.
    #     # For this smoke test we just print them.
    #     print(f"Error: {msg.error()}")
    # else:
    #     # msg.value()     — the raw bytes payload
    #     # msg.topic()     — the topic name
    #     # msg.partition() — which partition (0, 1, or 2) this message came from
    #     # msg.offset()    — position of this message within that partition
    #     #                   (monotonically increasing integer, starts at 0)
    #     print(
    #         f"Received: {   msg.value().decode('utf-8') }"
    #         f"  [topic={    msg.topic()                 }"
    #         f"  partition={ msg.partition()             }"
    #         f"  offset={    msg.offset()                }]"
    #     )
    
    if msg.error():
        print(f"Error: {msg.error()}")
    else:
        message = (
            f"Received: {   msg.value().decode('utf-8') }"
            f"  [topic={    msg.topic()                 }"
            f"  partition={ msg.partition()             }"
            f"  offset={    msg.offset()                }]"
        )
        print(message)
#-----

# consumer.close()
consumer.close()
print("\nConsumer closed.")
