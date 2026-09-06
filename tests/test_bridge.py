import json
from types import SimpleNamespace
from unittest.mock import Mock

import paho.mqtt.client as mqtt
import pytest

from mjson.bridge import Bridge, Config
from test_decoder import TOPIC, envelope


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    import os
    for name in os.environ:
        if name.startswith("MQTT_") or name == "MESHTASTIC_KEY":
            monkeypatch.delenv(name)


@pytest.mark.parametrize("name,value", [
    ("MQTT_INPUT_TOPIC", "#"),
    ("MQTT_INPUT_TOPIC", "msh/#/e"),
    ("MQTT_OUTPUT_TOPIC", "json/+"),
    ("MQTT_OUTPUT_TOPIC", ""),
    ("MQTT_PORT", "0"),
    ("MQTT_QOS", "3"),
    ("MQTT_TLS", "maybe"),
    ("MQTT_CA_FILE", "/tmp/ca.pem"),
])
def test_config_rejects_invalid(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        Config.from_env()


def test_tls_default_port(monkeypatch):
    monkeypatch.setenv("MQTT_TLS", "true")
    assert Config.from_env().port == 8883


def test_publish_and_skip():
    bridge = Bridge(Config.from_env())
    client = Mock()
    client.publish.return_value.rc = mqtt.MQTT_ERR_SUCCESS
    bridge.on_message(client, None, SimpleNamespace(topic=TOPIC, payload=b"invalid"))
    client.publish.assert_not_called()
    bridge.on_message(client, None, SimpleNamespace(topic=TOPIC, payload=envelope(encrypted=True).SerializeToString()))
    args, kwargs = client.publish.call_args
    assert args[0] == "msh/US/2/json"
    assert json.loads(args[1])["payload"] == {"text": "Hello mesh"}
    assert kwargs == {"qos": 1, "retain": False}


def test_resubscribe_after_reconnect():
    bridge = Bridge(Config.from_env())
    client = Mock()
    client.subscribe.return_value = (mqtt.MQTT_ERR_SUCCESS, 1)
    reason = SimpleNamespace(is_failure=False)
    for _ in range(2):
        bridge.on_connect(client, None, None, reason, None)
    assert client.subscribe.call_count == 2
