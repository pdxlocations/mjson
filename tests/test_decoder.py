import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from meshtastic.protobuf import mesh_pb2, mqtt_pb2, portnums_pb2, telemetry_pb2

from mjson.decoder import PacketError, decode_envelope, parse_key

KEY = parse_key("AQ==")
TOPIC = "msh/US/2/e/LongFast/!aabbccdd"


def envelope(port=portnums_pb2.TEXT_MESSAGE_APP, payload=b"Hello mesh", encrypted=False):
    packet = mesh_pb2.MeshPacket(id=123, to=0xFFFFFFFF, rx_time=1780000000)
    setattr(packet, "from", 0x12345678)
    data = mesh_pb2.Data(portnum=port, payload=payload)
    if encrypted:
        # Independent fixture nonce with explicit wire bytes for packet 123 / node 0x12345678.
        nonce = bytes.fromhex("7b000000000000007856341200000000")
        encryptor = Cipher(algorithms.AES(KEY), modes.CTR(nonce)).encryptor()
        packet.encrypted = encryptor.update(data.SerializeToString()) + encryptor.finalize()
    else:
        packet.decoded.CopyFrom(data)
    return mqtt_pb2.ServiceEnvelope(packet=packet, channel_id="LongFast", gateway_id="!aabbccdd")


@pytest.mark.parametrize("encrypted", [False, True])
def test_text(encrypted):
    result = decode_envelope(envelope(encrypted=encrypted).SerializeToString(), KEY)
    assert result == {
        "id": 123,
        "from": 0x12345678,
        "to": 0xFFFFFFFF,
        "sender": "!aabbccdd",
        "channel": 0,
        "type": "text",
        "payload": {"text": "Hello mesh"},
        "timestamp": 1780000000,
    }


def test_position():
    position = mesh_pb2.Position(latitude_i=454313900, longitude_i=-1223735400)
    result = decode_envelope(envelope(portnums_pb2.POSITION_APP, position.SerializeToString()).SerializeToString(), KEY)
    assert result["payload"] == {"latitude_i": 454313900, "longitude_i": -1223735400}


@pytest.mark.parametrize("encrypted", [False, True])
def test_telemetry(encrypted):
    telemetry = telemetry_pb2.Telemetry(time=1780078454)
    telemetry.device_metrics.battery_level = 100
    telemetry.device_metrics.voltage = 4.10599994659424
    telemetry.device_metrics.air_util_tx = 0.104055553674698
    se = envelope(portnums_pb2.TELEMETRY_APP, telemetry.SerializeToString(), encrypted)
    se.packet.channel = 8 if encrypted else 0
    result = decode_envelope(se.SerializeToString(), KEY)
    assert result == {
        "id": 123,
        "from": 0x12345678,
        "to": 0xFFFFFFFF,
        "sender": "!aabbccdd",
        "channel": 0,
        "type": "telemetry",
        "payload": {
            "battery_level": 100,
            "voltage": telemetry.device_metrics.voltage,
            "air_util_tx": telemetry.device_metrics.air_util_tx,
            "channel_utilization": 0,
            "uptime_seconds": 0,
        },
        "timestamp": 1780000000,
    }


def test_nodeinfo():
    user = mesh_pb2.User(id="!12345678", long_name="Test node")
    result = decode_envelope(envelope(portnums_pb2.NODEINFO_APP, user.SerializeToString()).SerializeToString(), KEY)
    assert result["type"] == "nodeinfo"
    assert result["payload"] == {"id": "!12345678", "longname": "Test node", "shortname": "", "hardware": 0, "role": 0}


def test_unknown_matches_firmware_empty_type():
    result = decode_envelope(envelope(500, b"\x00\xff").SerializeToString(), KEY)
    assert result["type"] == ""
    assert "payload" not in result


@pytest.mark.parametrize("payload", [b"", b"not protobuf", b'{"json":true}', mqtt_pb2.ServiceEnvelope(channel_id="test").SerializeToString()])
def test_invalid_envelope(payload):
    with pytest.raises(PacketError):
        decode_envelope(payload, KEY)


def test_wrong_key():
    with pytest.raises(PacketError):
        decode_envelope(envelope(encrypted=True).SerializeToString(), bytes(16))


def test_pki():
    se = envelope(encrypted=True)
    se.packet.pki_encrypted = True
    with pytest.raises(PacketError, match="PKI"):
        decode_envelope(se.SerializeToString(), KEY)


@pytest.mark.parametrize("port,payload", [(0, b"text"), (portnums_pb2.TEXT_MESSAGE_APP, b"\xff"), (portnums_pb2.POSITION_APP, b"\xff")])
def test_invalid_application(port, payload):
    with pytest.raises(PacketError):
        decode_envelope(envelope(port, payload).SerializeToString(), KEY)


@pytest.mark.parametrize("value", ["", "not base64", "AA==", "é"])
def test_invalid_key(value):
    with pytest.raises(ValueError):
        parse_key(value)


def test_default_key():
    assert KEY.hex() == "d4f1bb3a20290759f0bcffabcf4e6901"
