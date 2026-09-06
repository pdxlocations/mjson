# mjson

A Docker-ready MQTT bridge that subscribes to Meshtastic `ServiceEnvelope`
protobufs, decrypts channel traffic using the default `AQ==` key, decodes
application payloads, and publishes JSON to a topic on the same broker.

Decryption and protobuf dispatch are adapted from
[Ben Lipsey's mmqtt](https://github.com/pdxlocations/mmqtt). This project uses
the Meshtastic Python package for generated protobufs and application handlers;
it does not require a checkout of mmqtt. Licensed GPL-3.0-only; see LICENSE.

## Run in Docker

```sh
cp .env.example .env
# Edit .env with your broker, credentials, and topic paths.
docker compose up -d --build
docker compose logs -f
```

The broker must allow subscribing to `MQTT_INPUT_TOPIC` and publishing to
`MQTT_OUTPUT_TOPIC`. Configure the radio to uplink protobufs over MQTT.
Both encrypted and already-decoded envelopes are accepted. The defaults read
`msh/US/2/e/#` and publish all converted packets to `msh/US/2/json`.
Set the input to e.g. `msh/US/2/e/LongFast/#` to limit it to one channel.
Topic roots vary by network; use the actual paths on your broker.

Use `host.docker.internal` for a broker on the Docker Desktop host;
`localhost` inside the container refers to the container itself.

## Configuration

All settings are environment variables. Compose reads `.env` (ignored by Git).

| Variable | Default | Meaning |
| --- | --- | --- |
| `MQTT_HOST` | `localhost` | Broker hostname |
| `MQTT_PORT` | `1883`, or `8883` with TLS | Broker port |
| `MQTT_USERNAME`, `MQTT_PASSWORD` | empty | Optional credentials |
| `MQTT_INPUT_TOPIC` | `msh/US/2/e/#` | One MQTT subscription filter |
| `MQTT_OUTPUT_TOPIC` | `msh/US/2/json` | One literal destination topic |
| `MESHTASTIC_KEY` | `AQ==` | Default key shortcut or base64 16/32-byte AES key |
| `MQTT_TLS` | `false` | Enable TLS with hostname/certificate verification |
| `MQTT_CA_FILE` | empty | Optional CA file path; mount it into the container |
| `MQTT_QOS` | `1` | Subscribe/publish QoS: 0, 1, or 2 |
| `MQTT_CLIENT_ID` | generated | Set unique IDs when running multiple instances |
| `LOG_LEVEL` | `INFO` | Use `DEBUG` for packet skips and publish details |

Output is never retained. Input and output must not overlap; invalid
configuration exits immediately. Reconnects use exponential backoff up to 60
seconds and re-subscribe after connecting. SIGTERM/SIGINT stops the bridge.

## JSON format

One document is published per successfully decoded incoming envelope:

```json
{
  "schema_version": 1,
  "id": 123,
  "from": 305419896,
  "to": 4294967295,
  "sender": "!aabbccdd",
  "channel": 8,
  "channel_id": "LongFast",
  "type": "text",
  "payload": {"text": "Hello mesh"},
  "timestamp": 1780000000,
  "encrypted": true,
  "source_topic": "msh/US/2/e/LongFast/!aabbccdd",
  "packet": {
    "from": 305419896,
    "to": 4294967295,
    "channel": 8,
    "id": 123,
    "rx_time": 1780000000,
    "decoded": {"portnum": "TEXT_MESSAGE_APP", "payload": "SGVsbG8gbWVzaA=="}
  }
}
```

`sender` is the MQTT gateway; `from` is the original mesh sender. `timestamp`
is the packet's `rx_time` (zero when absent). `channel` is the wire value
(channel hash for encrypted packets), not necessarily a radio channel index.
`encrypted` describes the incoming packet. `packet` preserves the decoded
MeshPacket metadata and raw application payload using protobuf JSON rules:
snake_case field names, enum names, base64 bytes, string 64-bit integers, and
omitted default scalar fields.

`type` follows Meshtastic's protocol handler names: `text`, `user` (node info),
`position`, `telemetry`, `routing`, `traceroute`, etc. Position payloads include
decimal `latitude`/`longitude` when the integer coordinates are present.
Telemetry retains its nested metrics objects. Unknown application ports use
`type: "unknown"` and `payload: {"raw": "<base64>"}`.

This is mjson's versioned schema, not an exact replica of the retired firmware
JSON format. Consumers needing the old format should adapt to the fields above.

Packets using PKI/direct-message encryption, invalid protobufs, and undecodable
payloads are skipped. One channel key is tried; use separate subscriptions and
instances for networks with multiple keys. AES-CTR has no authentication, so
successful protobuf parsing alone cannot prove the key or sender is correct.
The default key is public; output contains plaintext.

The bridge does not deduplicate gateways or retained input, and QoS delivery
can produce duplicates. It uses a bounded in-memory publish queue, without a
durable spool: restarts, outages, or queue exhaustion can lose packets.

## Local development

```sh
python3.12 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest
MQTT_HOST=your-broker .venv/bin/python -m mjson
```

Local runs read process environment variables, not `.env` automatically.

To run the optional integration test against a disposable Docker broker and
bridge (requires the test dependencies installed above):

```sh
docker compose -p mjson-test -f tests/compose.yaml up -d --build
MJSON_TEST_PORT=18884 .venv/bin/pytest -q
docker compose -p mjson-test -f tests/compose.yaml down
```
