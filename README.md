# mjson
<img width="400" alt="image" src="https://github.com/user-attachments/assets/38832663-3281-48eb-886d-d883a0b05ef8" />

A Docker-ready MQTT bridge that subscribes to Meshtastic `ServiceEnvelope`
protobufs, decrypts channel traffic using the default `AQ==` key, decodes
application payloads, and publishes JSON to a topic on the same broker.

## Meshtastic 2.8 JSON compatibility

Meshtastic 2.8 removed firmware MQTT JSON publishing. mjson is a stopgap that
subscribes to the MQTT protobuf uplink and republishes the legacy JSON format
under the usual `/2/json/` topic path. It is intended to keep existing Home
Assistant MQTT sensors and automations working while they are migrated to
protobuf-aware integrations or another long-term interface.

The JSON mapping follows the pre-2.8 firmware serializer, so legacy templates
such as `value_json.payload.text` and telemetry automations can continue to use
their existing fields. It is a compatibility bridge, not an official firmware
replacement.

Licensed GPL-3.0-only; see LICENSE.

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

To receive everything under `msh/` and publish using the
[Meshtastic JSON topic structure](https://meshtastic.org/docs/software/integrations/mqtt/#json-topic), set:

```dotenv
MQTT_INPUT_TOPIC=msh/#
MQTT_OUTPUT_TOPIC=auto
```

Auto maps `<root>/2/e/<channel>/<gateway>` (or legacy `/2/c/`) to
`<root>/2/json/<channel>/<gateway>`, preserving region and any nested root.
For example, `msh/US/2/e/LongFast/!abcd1234` becomes
`msh/US/2/json/LongFast/!abcd1234`. Only protobuf topic paths are processed;
incoming JSON and other topics are ignored to prevent feedback loops.
This changes topic routing only; the JSON schema below still applies.

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
| `MQTT_OUTPUT_TOPIC` | `msh/US/2/json` | One literal destination topic, or `auto` for Meshtastic JSON topic routing |
| `MQTT_CHANNEL_INDEX` | `0` | Legacy local channel index (0–7) for encrypted uplinks |
| `MESHTASTIC_KEY` | `AQ==` | Default key shortcut or base64 16/32-byte AES key |
| `MQTT_TLS` | `false` | Enable TLS with hostname/certificate verification |
| `MQTT_CA_FILE` | empty | Optional CA file path; mount it into the container |
| `MQTT_QOS` | `1` | Subscribe/publish QoS: 0, 1, or 2 |
| `MQTT_CLIENT_ID` | generated | Set unique IDs when running multiple instances |
| `LOG_LEVEL` | `INFO` | Use `DEBUG` for packet skips and publish details |

Output is never retained. Literal output must not match the input filter; auto
routing safely permits overlap by accepting only protobuf topic paths. Invalid
configuration exits immediately. Reconnects use exponential backoff up to 60
seconds and re-subscribe after connecting. SIGTERM/SIGINT stops the bridge.

## JSON format

Output follows the non-nRF52/ESP32 JSON serializer from
[Meshtastic firmware v2.7.15.567b8ea](https://github.com/meshtastic/firmware/blob/v2.7.15.567b8ea/src/serialization/MeshPacketSerializer.cpp).
This replaces the previous protobuf-shaped payloads. For example:

```json
{
  "channel": 0,
  "from": 1819523280,
  "hop_start": 2,
  "hops_away": 0,
  "id": 1474103697,
  "payload": {
    "air_util_tx": 0.104055553674698,
    "battery_level": 100,
    "channel_utilization": 0,
    "uptime_seconds": 0,
    "voltage": 4.10599994659424
  },
  "rssi": -116,
  "sender": "!433b8cd8",
  "snr": -12.75,
  "timestamp": 1788669041,
  "to": 4294967295,
  "type": "telemetry"
}
```

Firmware compatibility includes:

- Flat telemetry with firmware field names, presence checks, and defaults.
  Telemetry's own `time` and metric variant wrappers are not emitted.
- `nodeinfo` with `id`, `longname`, `shortname`, numeric `hardware`, and `role`.
- Integer position coordinates and firmware's conditional position fields.
- Waypoint, neighbor info, traceroute replies, detection, paxcounter, and GPIO
  payloads. Unsupported ports and traceroute requests have an empty `type`
  and no `payload`, matching the serializer.
- Valid JSON text messages become the payload directly; ordinary text uses
  `{"text":"..."}`. Detection messages always use the text wrapper.
- Nonzero `rssi` and `snr`; `hop_start` and `hops_away` when the hop values are valid.
- Sorted keys, compact output, 15-significant-digit numbers, and nonfinite
  numbers serialized as `null`. No mjson metadata or duplicate packet wrapper.

`sender` comes from the envelope's gateway ID; `from` is the mesh sender.
`timestamp` comes from `rx_time`, including zero when absent.

The firmware's normal MQTT JSON path serialized the decoded packet, whose
`channel` was the gateway's **local index**, not its encrypted channel hash.
Decoded envelopes preserve their channel value. Encrypted envelopes use
`MQTT_CHANNEL_INDEX` (default `0`), since the gateway's index cannot be recovered
from the channel hash. For example, LongFast's default-key hash `8` typically
corresponds to index `0`. Set the index to match your gateway; one value cannot
reproduce differing channel layouts across all gateways in a broad subscription.

Traceroute names use up to 4096 recently observed node-info entries in memory,
with `"Unknown"` for missing entries. A gateway may have a different node database,
so its route names cannot be guaranteed identical. Invalid/undecodable packets
remain skipped rather than reproducing firmware error output. These source-data
limits prevent a blanket byte-for-byte guarantee for every gateway and packet.

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
