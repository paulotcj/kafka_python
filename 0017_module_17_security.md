# Module 17 — Security

## Overview

Security in Kafka has three independent layers:

| Layer | What It Protects | Mechanism |
|---|---|---|
| **Encryption** | Data in transit (network sniffing) | TLS/SSL |
| **Authentication** | Who is connecting (identity) | SASL/PLAIN, SASL/SCRAM, mTLS, OAUTHBEARER |
| **Authorization** | What they can do (access control) | ACLs (Access Control Lists) |

A production cluster typically uses all three. Development clusters often use none (PLAINTEXT with no auth) for simplicity — which is the default Docker setup in this guide.

This module explains each mechanism, shows how to configure them in Python, and covers secrets management best practices.

---

## 17.1 Authentication Mechanisms

### PLAINTEXT (No Auth — Development Only)

The default. No encryption, no authentication. Anyone who can reach the broker can produce and consume any topic.

```python
producer = Producer({"bootstrap.servers": "localhost:9092"})
```

Never use this in production. Use it only in local Docker environments.

### SASL/PLAIN (Username + Password)

Simple username/password sent in plaintext over the wire. **Always combine with TLS** — without TLS the password is sent unencrypted.

**Docker Compose — enable SASL/PLAIN on the broker:**

```yaml
kafka:
  image: apache/kafka:latest
  container_name: kafka
  user: root
  ports:
    - "9092:9092"
  environment:
    KAFKA_NODE_ID: 1
    KAFKA_PROCESS_ROLES: broker,controller
    KAFKA_LISTENERS: SASL_PLAINTEXT://0.0.0.0:9092,CONTROLLER://0.0.0.0:9093
    KAFKA_ADVERTISED_LISTENERS: SASL_PLAINTEXT://localhost:9092
    KAFKA_INTER_BROKER_LISTENER_NAME: SASL_PLAINTEXT
    KAFKA_CONTROLLER_LISTENER_NAMES: CONTROLLER
    KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: CONTROLLER:PLAINTEXT,SASL_PLAINTEXT:SASL_PLAINTEXT
    KAFKA_CONTROLLER_QUORUM_VOTERS: 1@kafka:9093
    KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
    KAFKA_LOG_DIRS: /tmp/kraft-combined-logs
    CLUSTER_ID: "MkU3OEVBNTcwNTJENDM2Qk"
    # SASL configuration
    KAFKA_SASL_ENABLED_MECHANISMS: PLAIN
    KAFKA_SASL_MECHANISM_INTER_BROKER_PROTOCOL: PLAIN
    KAFKA_OPTS: "-Djava.security.auth.login.config=/etc/kafka/kafka_jaas.conf"
  volumes:
    - ./kafka_jaas.conf:/etc/kafka/kafka_jaas.conf
```

Create `kafka_jaas.conf`:
```
KafkaServer {
    org.apache.kafka.common.security.plain.PlainLoginModule required
    username="admin"
    password="admin-secret"
    user_admin="admin-secret"
    user_alice="alice-secret"
    user_bob="bob-secret";
};
```

**Python client — SASL/PLAIN:**

```python
from confluent_kafka import Producer, Consumer

# Producer
producer = Producer({
    "bootstrap.servers": "localhost:9092",
    "security.protocol": "SASL_PLAINTEXT",   # use SASL_SSL in production with TLS
    "sasl.mechanism": "PLAIN",
    "sasl.username": "alice",
    "sasl.password": "alice-secret",
})

# Consumer
consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "my-group",
    "security.protocol": "SASL_PLAINTEXT",
    "sasl.mechanism": "PLAIN",
    "sasl.username": "alice",
    "sasl.password": "alice-secret",
    "auto.offset.reset": "earliest",
})
```

### SASL/SCRAM (Salted Challenge-Response)

SCRAM is more secure than PLAIN: the password is never sent over the wire. The broker and client exchange cryptographic challenges. Passwords are stored as salted hashes.

SCRAM-SHA-256 and SCRAM-SHA-512 are both supported. Use SHA-512 for stronger hashing.

**Add users to Kafka (KRaft mode):**

```bash
# Create a SCRAM credential for user 'alice'
docker exec kafka /opt/kafka/bin/kafka-configs.sh \
  --bootstrap-server localhost:9092 \
  --alter \
  --add-config "SCRAM-SHA-512=[iterations=8192,password=alice-secret]" \
  --entity-type users \
  --entity-name alice

# Verify
docker exec kafka /opt/kafka/bin/kafka-configs.sh \
  --bootstrap-server localhost:9092 \
  --describe \
  --entity-type users \
  --entity-name alice
```

**Docker Compose — enable SASL/SCRAM on the broker:**

```yaml
environment:
  KAFKA_SASL_ENABLED_MECHANISMS: SCRAM-SHA-512
  KAFKA_SASL_MECHANISM_INTER_BROKER_PROTOCOL: SCRAM-SHA-512
  KAFKA_OPTS: "-Djava.security.auth.login.config=/etc/kafka/kafka_jaas.conf"
```

`kafka_jaas.conf` for SCRAM (brokers authenticate to each other):
```
KafkaServer {
    org.apache.kafka.common.security.scram.ScramLoginModule required
    username="broker"
    password="broker-secret";
};
```

**Python client — SASL/SCRAM:**

```python
from confluent_kafka import Producer

producer = Producer({
    "bootstrap.servers": "localhost:9092",
    "security.protocol": "SASL_SSL",       # SCRAM + TLS in production
    "sasl.mechanism": "SCRAM-SHA-512",
    "sasl.username": "alice",
    "sasl.password": "alice-secret",
    "ssl.ca.location": "/path/to/ca.crt",  # CA cert for TLS verification
})
```

### SASL/OAUTHBEARER (OAuth 2.0)

Used when your organisation already has an identity provider (Keycloak, Okta, Azure AD). The client obtains a JWT token from the IdP and presents it to Kafka.

```python
import time
import requests
from confluent_kafka import Producer

def get_token(config, schema_registry_client=None):
    """
    Fetch a JWT token from your OAuth server.
    This function is called by confluent-kafka when the token expires.
    """
    token_url = config.get("token_url")
    client_id = config.get("client_id")
    client_secret = config.get("client_secret")

    response = requests.post(token_url, data={
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "kafka",
    })
    response.raise_for_status()
    data = response.json()
    return data["access_token"], time.time() + data["expires_in"]

producer = Producer({
    "bootstrap.servers": "kafka.example.com:9092",
    "security.protocol": "SASL_SSL",
    "sasl.mechanism": "OAUTHBEARER",
    "oauth_cb": get_token,
    # Extra config passed to get_token:
    "token_url": "https://idp.example.com/oauth/token",
    "client_id": "kafka-producer",
    "client_secret": "super-secret",
    "ssl.ca.location": "/etc/ssl/certs/ca-certificates.crt",
})
```

### mTLS (Mutual TLS — Client Certificates)

Both the broker and the client verify each other's TLS certificates. The client's certificate is its identity — no username/password needed. Used in high-security environments.

```python
from confluent_kafka import Producer

producer = Producer({
    "bootstrap.servers": "kafka.example.com:9093",
    "security.protocol": "SSL",
    "ssl.ca.location": "/certs/ca.crt",              # CA that signed broker cert
    "ssl.certificate.location": "/certs/client.crt", # client's cert (identity)
    "ssl.key.location": "/certs/client.key",          # client's private key
    "ssl.key.password": "key-passphrase",             # if the key is encrypted
})
```

---

## 17.2 TLS/SSL Encryption in Transit

TLS encrypts the connection between clients and brokers. Without it, all data (including SASL credentials in PLAIN mode) travels as plaintext.

### Generating Self-Signed Certificates (for Dev/Testing)

```bash
# Create a directory for certificates
mkdir certs && cd certs

# Generate a CA (Certificate Authority)
openssl genrsa -out ca.key 4096
openssl req -new -x509 -days 1826 -key ca.key -out ca.crt \
  -subj "/C=US/O=MyOrg/CN=MyCA"

# Generate broker key and certificate signing request
openssl genrsa -out broker.key 2048
openssl req -new -key broker.key -out broker.csr \
  -subj "/C=US/O=MyOrg/CN=localhost"

# Sign the broker cert with the CA
openssl x509 -req -days 365 -in broker.csr -CA ca.crt -CAkey ca.key \
  -CAcreateserial -out broker.crt

# For Python clients: convert to PEM format (usually already PEM)
# Verify the chain
openssl verify -CAfile ca.crt broker.crt
```

### Docker Compose with TLS

```yaml
kafka:
  image: apache/kafka:latest
  container_name: kafka
  user: root
  ports:
    - "9093:9093"   # SSL listener
  volumes:
    - ./certs:/etc/kafka/certs
  environment:
    KAFKA_LISTENERS: SSL://0.0.0.0:9093,CONTROLLER://0.0.0.0:9094
    KAFKA_ADVERTISED_LISTENERS: SSL://localhost:9093
    KAFKA_INTER_BROKER_LISTENER_NAME: SSL
    KAFKA_CONTROLLER_LISTENER_NAMES: CONTROLLER
    KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: SSL:SSL,CONTROLLER:PLAINTEXT
    KAFKA_SSL_KEYSTORE_LOCATION: /etc/kafka/certs/broker.keystore.jks
    KAFKA_SSL_KEYSTORE_PASSWORD: changeit
    KAFKA_SSL_KEY_PASSWORD: changeit
    KAFKA_SSL_TRUSTSTORE_LOCATION: /etc/kafka/certs/broker.truststore.jks
    KAFKA_SSL_TRUSTSTORE_PASSWORD: changeit
    KAFKA_SSL_CLIENT_AUTH: required   # enforce mTLS; use 'none' for server-only TLS
```

### Python Client with TLS

```python
# Server-only TLS (broker verifies its cert, client only has the CA)
producer = Producer({
    "bootstrap.servers": "localhost:9093",
    "security.protocol": "SSL",
    "ssl.ca.location": "./certs/ca.crt",
    # No client cert needed for server-only TLS
})

# Mutual TLS (both sides verify)
producer = Producer({
    "bootstrap.servers": "localhost:9093",
    "security.protocol": "SSL",
    "ssl.ca.location": "./certs/ca.crt",
    "ssl.certificate.location": "./certs/client.crt",
    "ssl.key.location": "./certs/client.key",
})
```

---

## 17.3 Authorization with ACLs

ACLs define what an authenticated principal (user or service account) is allowed to do.

### ACL Operations

| Operation | Applies To | Example |
|---|---|---|
| `READ` | Topic, Group | Consumer reads from topic |
| `WRITE` | Topic | Producer writes to topic |
| `CREATE` | Topic, Cluster | Create new topics |
| `DELETE` | Topic | Delete a topic |
| `DESCRIBE` | Topic, Group | Describe topic metadata |
| `ALTER` | Topic, Cluster | Alter topic config |
| `ALL` | Any | Grant all permissions |

### Enabling ACLs on the Broker

```yaml
environment:
  KAFKA_AUTHORIZER_CLASS_NAME: org.apache.kafka.metadata.authorizer.StandardAuthorizer
  KAFKA_SUPER_USERS: User:admin   # admin bypasses all ACL checks
  KAFKA_ALLOW_EVERYONE_IF_NO_ACL_FOUND: "false"  # deny by default
```

### Managing ACLs with CLI

```bash
# Grant alice READ access to topic 'orders' and its consumer group
docker exec kafka /opt/kafka/bin/kafka-acls.sh \
  --bootstrap-server localhost:9092 \
  --command-config /etc/kafka/admin.properties \
  --add \
  --allow-principal User:alice \
  --operation READ \
  --topic orders

docker exec kafka /opt/kafka/bin/kafka-acls.sh \
  --bootstrap-server localhost:9092 \
  --command-config /etc/kafka/admin.properties \
  --add \
  --allow-principal User:alice \
  --operation READ \
  --group order-processing-group

# Grant bob WRITE access to topic 'orders'
docker exec kafka /opt/kafka/bin/kafka-acls.sh \
  --bootstrap-server localhost:9092 \
  --command-config /etc/kafka/admin.properties \
  --add \
  --allow-principal User:bob \
  --operation WRITE \
  --topic orders

# List ACLs for a topic
docker exec kafka /opt/kafka/bin/kafka-acls.sh \
  --bootstrap-server localhost:9092 \
  --command-config /etc/kafka/admin.properties \
  --list \
  --topic orders

# Remove an ACL
docker exec kafka /opt/kafka/bin/kafka-acls.sh \
  --bootstrap-server localhost:9092 \
  --command-config /etc/kafka/admin.properties \
  --remove \
  --allow-principal User:alice \
  --operation READ \
  --topic orders
```

Where `admin.properties` contains the admin credentials:
```
security.protocol=SASL_PLAINTEXT
sasl.mechanism=SCRAM-SHA-512
sasl.jaas.config=org.apache.kafka.common.security.scram.ScramLoginModule required username="admin" password="admin-secret";
```

### Principle of Least Privilege — Service ACL Design

Each microservice should have its own principal with only the permissions it needs:

| Service | Principal | Topic | Operations |
|---|---|---|---|
| Order Service (producer) | `User:order-svc` | `orders` | WRITE, DESCRIBE |
| Enrichment Service (consumer) | `User:enrichment-svc` | `orders` | READ, DESCRIBE |
| | | `enriched-orders` | WRITE, DESCRIBE |
| | | `enrichment-group` (group) | READ |
| Analytics Service (consumer) | `User:analytics-svc` | `orders`, `enriched-orders` | READ, DESCRIBE |
| Admin (ops) | `User:admin` | `*` | ALL (super user) |

---

## 17.4 Secrets Management

### Never Hardcode Credentials

```python
# BAD — credential in source code
producer = Producer({
    "sasl.username": "alice",
    "sasl.password": "super-secret",  # ends up in git history
})

# GOOD — from environment variables
import os
producer = Producer({
    "sasl.username": os.environ["KAFKA_SASL_USERNAME"],
    "sasl.password": os.environ["KAFKA_SASL_PASSWORD"],
})
```

### Configuration Builder Pattern

Centralise all security configuration in one place so you only change it in one location:

```python
# kafka_config.py

import os
from typing import Optional

def get_base_config(
    bootstrap_servers: Optional[str] = None,
) -> dict:
    """
    Build Kafka client config from environment variables.
    Works for both development (no auth) and production (SCRAM+TLS).
    """
    config = {
        "bootstrap.servers": bootstrap_servers or os.environ.get(
            "KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"
        ),
    }

    protocol = os.environ.get("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT")
    config["security.protocol"] = protocol

    if protocol in ("SASL_PLAINTEXT", "SASL_SSL"):
        config["sasl.mechanism"] = os.environ.get("KAFKA_SASL_MECHANISM", "PLAIN")
        config["sasl.username"] = os.environ["KAFKA_SASL_USERNAME"]
        config["sasl.password"] = os.environ["KAFKA_SASL_PASSWORD"]

    if protocol in ("SSL", "SASL_SSL"):
        ca = os.environ.get("KAFKA_SSL_CA_LOCATION")
        cert = os.environ.get("KAFKA_SSL_CERT_LOCATION")
        key = os.environ.get("KAFKA_SSL_KEY_LOCATION")
        key_pass = os.environ.get("KAFKA_SSL_KEY_PASSWORD")

        if ca:
            config["ssl.ca.location"] = ca
        if cert:
            config["ssl.certificate.location"] = cert
        if key:
            config["ssl.key.location"] = key
        if key_pass:
            config["ssl.key.password"] = key_pass

    return config

def get_producer_config(**extra) -> dict:
    return {**get_base_config(), **extra}

def get_consumer_config(group_id: str, **extra) -> dict:
    return {
        **get_base_config(),
        "group.id": group_id,
        "auto.offset.reset": "earliest",
        **extra,
    }
```

Usage:
```python
from kafka_config import get_producer_config, get_consumer_config
from confluent_kafka import Producer, Consumer

producer = Producer(get_producer_config(acks="all"))
consumer = Consumer(get_consumer_config("my-group"))
```

Deployment (set these in your `.env` file, CI/CD secrets, or Kubernetes Secret):
```bash
# Development
export KAFKA_BOOTSTRAP_SERVERS=localhost:9092
export KAFKA_SECURITY_PROTOCOL=PLAINTEXT

# Production
export KAFKA_BOOTSTRAP_SERVERS=kafka.prod.example.com:9093
export KAFKA_SECURITY_PROTOCOL=SASL_SSL
export KAFKA_SASL_MECHANISM=SCRAM-SHA-512
export KAFKA_SASL_USERNAME=order-service
export KAFKA_SASL_PASSWORD=<from secrets manager>
export KAFKA_SSL_CA_LOCATION=/etc/ssl/kafka/ca.crt
```

### AWS Secrets Manager Integration

```python
import boto3
import json
from confluent_kafka import Producer

def get_kafka_credentials_from_aws(secret_name: str, region: str = "us-east-1") -> dict:
    client = boto3.client("secretsmanager", region_name=region)
    response = client.get_secret_value(SecretId=secret_name)
    return json.loads(response["SecretString"])

creds = get_kafka_credentials_from_aws("prod/kafka/order-service")
producer = Producer({
    "bootstrap.servers": "kafka.prod.example.com:9093",
    "security.protocol": "SASL_SSL",
    "sasl.mechanism": "SCRAM-SHA-512",
    "sasl.username": creds["username"],
    "sasl.password": creds["password"],
    "ssl.ca.location": "/etc/ssl/kafka/ca.crt",
})
```

### HashiCorp Vault Integration

```python
import hvac
from confluent_kafka import Producer

def get_kafka_credentials_from_vault(path: str) -> dict:
    client = hvac.Client(url="https://vault.example.com", token=os.environ["VAULT_TOKEN"])
    secret = client.secrets.kv.v2.read_secret_version(path=path)
    return secret["data"]["data"]

creds = get_kafka_credentials_from_vault("kafka/order-service")
producer = Producer({
    "bootstrap.servers": "kafka.prod.example.com:9093",
    "security.protocol": "SASL_SSL",
    "sasl.mechanism": "SCRAM-SHA-512",
    "sasl.username": creds["username"],
    "sasl.password": creds["password"],
})
```

---

## 17.5 aiokafka with SSL

```python
import asyncio
import ssl
from aiokafka import AIOKafkaProducer

async def produce():
    ssl_context = ssl.create_default_context(cafile="/certs/ca.crt")
    ssl_context.load_cert_chain(
        certfile="/certs/client.crt",
        keyfile="/certs/client.key",
    )

    producer = AIOKafkaProducer(
        bootstrap_servers="kafka.example.com:9093",
        ssl_context=ssl_context,
        security_protocol="SSL",
    )
    await producer.start()
    try:
        await producer.send_and_wait("orders", b"hello")
    finally:
        await producer.stop()

asyncio.run(produce())
```

For SASL/SCRAM with aiokafka:
```python
producer = AIOKafkaProducer(
    bootstrap_servers="kafka.example.com:9092",
    security_protocol="SASL_SSL",
    sasl_mechanism="SCRAM-SHA-512",
    sasl_plain_username="alice",
    sasl_plain_password="alice-secret",
    ssl_context=ssl_context,
)
```

---

## 17.6 Security Checklist

Use this checklist when deploying a Kafka cluster to any non-development environment:

**Encryption**
- [ ] TLS enabled on all listener ports
- [ ] Self-signed certificates are acceptable for internal traffic, but CA-signed for external
- [ ] `ssl.endpoint.identification.algorithm=https` enabled (verifies broker hostname matches cert)

**Authentication**
- [ ] PLAINTEXT listener disabled or only accessible from localhost
- [ ] SASL mechanism chosen (SCRAM-SHA-512 recommended; OAUTHBEARER if you have an IdP)
- [ ] Inter-broker communication uses an authenticated listener
- [ ] Controller listener uses at least PLAINTEXT within the same trusted network

**Authorization**
- [ ] ACL authorizer enabled
- [ ] `allow.everyone.if.no.acl.found=false` (deny by default)
- [ ] Each service has a dedicated principal
- [ ] Principle of least privilege applied (producers can't read; consumers can't write)
- [ ] Admin operations restricted to ops principals

**Secrets**
- [ ] No credentials in source code or Dockerfiles
- [ ] Credentials stored in a secrets manager (Vault, AWS Secrets Manager, Kubernetes Secrets)
- [ ] Credentials rotated on a schedule (SCRAM supports this without restarting brokers)
- [ ] TLS certificates have an expiry alert before they expire
