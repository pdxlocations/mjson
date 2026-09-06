"""Run against a broker with the Docker bridge subscribed to the default topics.

MJSON_TEST_PORT=18884 .venv/bin/pytest tests/test_integration.py -v
"""

import json
import os
import queue
import threading
import time

import paho.mqtt.client as mqtt
import pytest
from meshtastic.protobuf import portnums_pb2, telemetry_pb2

from test_decoder import TOPIC, envelope


@pytest.mark.skipif(not os.getenv("MJSON_TEST_PORT"), reason="requires a running broker and bridge")
def test_docker_round_trip():
    received = queue.Queue()
    subscribed = threading.Event()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = lambda c, *_: c.subscribe("msh/US/2/json", qos=1)
    client.on_subscribe = lambda *_: subscribed.set()
    client.on_message = lambda c, u, msg: received.put(json.loads(msg.payload))
    client.connect("127.0.0.1", int(os.environ["MJSON_TEST_PORT"]))
    client.loop_start()
    try:
        assert subscribed.wait(10)
        # Retry while the bridge establishes its initial broker subscription.
        deadline = time.monotonic() + 20
        while True:
            client.publish(TOPIC, envelope(encrypted=True).SerializeToString(), qos=1).wait_for_publish(5)
            try:
                document = received.get(timeout=1)
                break
            except queue.Empty:
                assert time.monotonic() < deadline, "No JSON from bridge"
        assert document["payload"] == {"text": "Hello mesh"}
        assert set(document) == {"id", "from", "to", "sender", "channel", "type", "payload", "timestamp"}
        client.publish(TOPIC, b"invalid protobuf", qos=1).wait_for_publish(5)
        client.publish(TOPIC, envelope(payload=b"Still alive").SerializeToString(), qos=1).wait_for_publish(5)
        deadline = time.monotonic() + 10
        while True:
            document = received.get(timeout=max(0.1, deadline - time.monotonic()))
            if document["payload"] == {"text": "Still alive"}:
                break
            assert time.monotonic() < deadline
        telemetry = telemetry_pb2.Telemetry(time=1780078454)
        telemetry.device_metrics.battery_level = 100
        telemetry.device_metrics.voltage = 4.106
        se = envelope(portnums_pb2.TELEMETRY_APP, telemetry.SerializeToString(), encrypted=True)
        se.packet.channel = 8
        se.packet.rx_rssi = -116
        se.packet.rx_snr = -12.75
        se.packet.hop_start = se.packet.hop_limit = 2
        client.publish(TOPIC, se.SerializeToString(), qos=1).wait_for_publish(5)
        deadline = time.monotonic() + 10
        while True:
            document = received.get(timeout=max(0.1, deadline - time.monotonic()))
            if document["type"] == "telemetry":
                break
            assert time.monotonic() < deadline
        assert document["channel"] == 0
        assert document["payload"] == {
            "battery_level": 100, "voltage": 4.10599994659424,
            "air_util_tx": 0, "channel_utilization": 0, "uptime_seconds": 0,
        }
        assert document["rssi"] == -116 and document["snr"] == -12.75
        assert document["hop_start"] == 2 and document["hops_away"] == 0
    finally:
        client.disconnect()
        client.loop_stop()
