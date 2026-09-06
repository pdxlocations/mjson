import json
import logging
import os
import signal
import ssl
import threading
from dataclasses import dataclass

import paho.mqtt.client as mqtt

from .decoder import PacketError, decode_envelope, parse_key

LOG = logging.getLogger("mjson")


def boolean(name: str, default: str = "false") -> bool:
    value = os.getenv(name, default).lower()
    if value not in {"true", "false", "1", "0", "yes", "no"}:
        raise ValueError(f"{name} must be true or false")
    return value in {"true", "1", "yes"}


def validate_topic(topic: str, *, subscription: bool = False) -> None:
    if not topic or "\x00" in topic or len(topic.encode("utf-8")) > 65535:
        raise ValueError("MQTT topics must contain 1–65535 UTF-8 bytes without NUL")
    levels = topic.split("/")
    for index, level in enumerate(levels):
        if "+" in level and (not subscription or level != "+"):
            raise ValueError("Invalid + wildcard in MQTT topic")
        if "#" in level and (not subscription or level != "#" or index != len(levels) - 1):
            raise ValueError("Invalid # wildcard in MQTT topic")


def meshtastic_json_topic(source_topic: str) -> str | None:
    """Map v2 protobuf topics to JSON, preserving root, channel and gateway."""
    parts = source_topic.rsplit("/", 4)
    if len(parts) != 5:
        return None
    root, version, encoding, channel, gateway = parts
    if not root or version != "2" or encoding not in {"e", "c"} or not channel or not gateway:
        return None
    return f"{root}/2/json/{channel}/{gateway}"


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    username: str | None
    password: str | None
    input_topic: str
    output_topic: str
    key: bytes
    tls: bool
    ca_file: str | None
    client_id: str
    qos: int

    @classmethod
    def from_env(cls):
        tls = boolean("MQTT_TLS")
        config = cls(
            host=os.getenv("MQTT_HOST", "localhost"),
            port=int(os.getenv("MQTT_PORT", "8883" if tls else "1883")),
            username=os.getenv("MQTT_USERNAME") or None,
            password=os.getenv("MQTT_PASSWORD") or None,
            input_topic=os.getenv("MQTT_INPUT_TOPIC", "msh/US/2/e/#"),
            output_topic=os.getenv("MQTT_OUTPUT_TOPIC", "msh/US/2/json"),
            key=parse_key(os.getenv("MESHTASTIC_KEY", "AQ==")),
            tls=tls,
            ca_file=os.getenv("MQTT_CA_FILE") or None,
            client_id=os.getenv("MQTT_CLIENT_ID", ""),
            qos=int(os.getenv("MQTT_QOS", "1")),
        )
        if not config.host or not 1 <= config.port <= 65535:
            raise ValueError("MQTT_HOST and MQTT_PORT must identify a broker")
        if config.qos not in (0, 1, 2):
            raise ValueError("MQTT_QOS must be 0, 1, or 2")
        validate_topic(config.input_topic, subscription=True)
        if config.output_topic != "auto":
            validate_topic(config.output_topic)
            if mqtt.topic_matches_sub(config.input_topic, config.output_topic):
                raise ValueError("MQTT_OUTPUT_TOPIC must not match MQTT_INPUT_TOPIC (feedback loop)")
        if config.ca_file and not config.tls:
            raise ValueError("MQTT_CA_FILE requires MQTT_TLS=true")
        return config


class Bridge:
    def __init__(self, config: Config):
        self.config = config
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=config.client_id)
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_subscribe = self.on_subscribe
        self.client.on_message = self.on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)
        self.client.max_queued_messages_set(1000)
        self.client.enable_logger(LOG)
        if config.username:
            self.client.username_pw_set(config.username, config.password)
        if config.tls:
            self.client.tls_set_context(ssl.create_default_context(cafile=config.ca_file))

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            LOG.error("Broker rejected connection: %s", reason_code)
            return
        result, _ = client.subscribe(self.config.input_topic, qos=self.config.qos)
        if result != mqtt.MQTT_ERR_SUCCESS:
            LOG.error("Unable to request subscription: %s", mqtt.error_string(result))
        else:
            LOG.info("Connected; requesting subscription to %s", self.config.input_topic)

    def on_subscribe(self, client, userdata, mid, reason_codes, properties):
        if any(code.is_failure for code in reason_codes):
            LOG.error("Broker rejected subscription: %s", reason_codes)
        else:
            LOG.info("Subscribed; publishing JSON to %s", self.config.output_topic)

    def on_disconnect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            LOG.warning("Disconnected: %s; reconnecting automatically", reason_code)

    def on_message(self, client, userdata, message):
        output_topic = self.config.output_topic
        if output_topic == "auto":
            output_topic = meshtastic_json_topic(message.topic)
            if output_topic is None:
                return
        try:
            document = decode_envelope(message.payload, self.config.key)
        except PacketError as exc:
            LOG.debug("Skipping packet on %s: %s", message.topic, exc)
            return
        result = client.publish(
            output_topic,
            json.dumps(document, ensure_ascii=True, separators=(",", ":")),
            qos=self.config.qos,
            retain=False,
        )
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            LOG.warning("JSON publish not accepted: %s", mqtt.error_string(result.rc))
        else:
            LOG.debug("Published %s packet %s", document["type"], document["id"])

    def run(self):
        stopped = threading.Event()
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, lambda *_: stopped.set())
        LOG.info("Connecting to %s:%s", self.config.host, self.config.port)
        self.client.connect_async(self.config.host, self.config.port, keepalive=60)
        self.client.loop_start()
        try:
            stopped.wait()
        finally:
            self.client.disconnect()
            self.client.loop_stop()
            LOG.info("Stopped")


def main():
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        bridge = Bridge(Config.from_env())
    except (ValueError, OSError) as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc
    bridge.run()
