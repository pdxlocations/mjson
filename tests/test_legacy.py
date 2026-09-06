"""Compatibility examples for firmware v2.7.15.567b8ea MeshPacketSerializer."""
import json

import pytest
from meshtastic.protobuf import mesh_pb2, paxcount_pb2, portnums_pb2 as ports
from meshtastic.protobuf import remote_hardware_pb2, telemetry_pb2

from mjson.decoder import decode_envelope
from mjson.legacy import dumps
from test_decoder import KEY, envelope


def decode(port, message, **kwargs):
    se = envelope(port, message.SerializeToString())
    return decode_envelope(se.SerializeToString(), KEY, **kwargs)


def test_legacy_telemetry_wire_example():
    telemetry = telemetry_pb2.Telemetry(time=1780078454)
    telemetry.device_metrics.battery_level = 100
    telemetry.device_metrics.voltage = 4.106
    telemetry.device_metrics.air_util_tx = 0.10405555
    se = envelope(ports.TELEMETRY_APP, telemetry.SerializeToString())
    se.packet.id = 1474103697
    setattr(se.packet, "from", 1819523280)
    se.gateway_id = "!433b8cd8"
    se.packet.rx_time = 1788669041
    se.packet.rx_rssi = -116
    se.packet.rx_snr = -12.75
    se.packet.hop_start = se.packet.hop_limit = 2
    assert dumps(decode_envelope(se.SerializeToString(), KEY)) == (
        '{"channel":0,"from":1819523280,"hop_start":2,"hops_away":0,"id":1474103697,'
        '"payload":{"air_util_tx":0.104055553674698,"battery_level":100,"channel_utilization":0,'
        '"uptime_seconds":0,"voltage":4.10599994659424},"rssi":-116,"sender":"!433b8cd8",'
        '"snr":-12.75,"timestamp":1788669041,"to":4294967295,"type":"telemetry"}'
    )


@pytest.mark.parametrize("text,expected", [
    (b'{"reading":12}', {"reading": 12}), (b'[1,true,null]', [1, True, None]),
    (b'null', None), (b'42', 42), (b'"hello"', "hello"),
    (b'NaN', {"text": "NaN"}), (b'hello\0ignored', {"text": "hello"}),
])
def test_text_json(text, expected):
    result = decode_envelope(envelope(payload=text).SerializeToString(), KEY)
    assert "payload" in result
    assert result["payload"] == expected


def test_device_presence_and_defaults():
    telemetry = telemetry_pb2.Telemetry()
    telemetry.device_metrics.SetInParent()
    expected = {"voltage": 0, "channel_utilization": 0, "air_util_tx": 0, "uptime_seconds": 0}
    assert decode(ports.TELEMETRY_APP, telemetry)["payload"] == expected
    telemetry.device_metrics.battery_level = 0
    expected["battery_level"] = 0
    assert decode(ports.TELEMETRY_APP, telemetry)["payload"] == expected


@pytest.mark.parametrize("variant,values,expected", [
    ("environment_metrics", {"temperature": 0, "iaq": 12, "soil_temperature": -5},
     {"temperature": 0, "iaq": 12, "soil_temperature": -5}),
    ("air_quality_metrics", {"pm10_standard": 0, "pm25_environmental": 17}, {"pm10": 0, "pm25_e": 17}),
    ("power_metrics", {"ch1_voltage": 0, "ch3_current": 2}, {"voltage_ch1": 0, "current_ch3": 2}),
    ("local_stats", {"uptime_seconds": 10}, {}),
])
def test_telemetry_variants(variant, values, expected):
    telemetry = telemetry_pb2.Telemetry(time=100)
    metrics = getattr(telemetry, variant)
    for name, value in values.items():
        setattr(metrics, name, value)
    assert decode(ports.TELEMETRY_APP, telemetry)["payload"] == expected


def test_position_legacy_fields():
    position = mesh_pb2.Position(latitude_i=1, longitude_i=-2, altitude=0, time=123,
                                 timestamp=124, ground_speed=2, ground_track=3,
                                 sats_in_view=4, PDOP=5, HDOP=6, VDOP=7, precision_bits=16)
    assert decode(ports.POSITION_APP, position)["payload"] == {
        "latitude_i": 1, "longitude_i": -2, "time": 123, "timestamp": 124,
        "ground_speed": 2, "ground_track": 3, "sats_in_view": 4,
        "PDOP": 5, "HDOP": 6, "VDOP": 7, "precision_bits": 16,
    }


def test_waypoint_defaults():
    waypoint = mesh_pb2.Waypoint(id=123, name="Camp", icon=42)
    assert decode(ports.WAYPOINT_APP, waypoint)["payload"] == {
        "id": 123, "name": "Camp", "description": "", "expire": 0, "locked_to": 0,
        "latitude_i": 0, "longitude_i": 0,
    }


def test_neighborinfo():
    neighbors = mesh_pb2.NeighborInfo(node_id=12, node_broadcast_interval_secs=900, last_sent_by_id=13)
    neighbors.neighbors.add(node_id=14, snr=-12.75)
    assert decode(ports.NEIGHBORINFO_APP, neighbors)["payload"] == {
        "node_id": 12, "node_broadcast_interval_secs": 900, "last_sent_by_id": 13,
        "neighbors_count": 1, "neighbors": [{"node_id": 14, "snr": -12}],
    }


def test_traceroute_response_names_and_snr():
    route = mesh_pb2.RouteDiscovery(route=[42], route_back=[43], snr_towards=[-51], snr_back=[-128])
    se = envelope(ports.TRACEROUTE_APP, route.SerializeToString())
    # Requests have no payload or type in the legacy serializer.
    request = decode_envelope(se.SerializeToString(), KEY)
    assert request["type"] == ""
    assert "payload" not in request
    se.packet.decoded.request_id = 10
    result = decode_envelope(se.SerializeToString(), KEY, node_names={42: "Relay"})
    assert result["payload"] == {
        "route": ["Unknown", "Relay", "Unknown"],
        "route_back": ["Unknown", "Unknown", "Unknown"],
        "snr_towards": [-12.75], "snr_back": [-32],
    }


def test_detection_and_paxcounter():
    se = envelope(ports.DETECTION_SENSOR_APP, b'{"alarm":true}')
    result = decode_envelope(se.SerializeToString(), KEY)
    assert result["type"] == "detection"
    assert result["payload"] == {"text": '{"alarm":true}'}
    result = decode(ports.PAXCOUNTER_APP, paxcount_pb2.Paxcount(wifi=10, ble=20, uptime=30))
    assert result["type"] == "paxcounter"
    assert result["payload"] == {"wifi_count": 10, "ble_count": 20, "uptime": 30}


@pytest.mark.parametrize("kind,name,payload", [
    (3, "gpios_changed", {"gpio_value": 7}),
    (5, "gpios_read_reply", {"gpio_value": 7, "gpio_mask": 15}),
    (1, "", None),
])
def test_gpio(kind, name, payload):
    result = decode(ports.REMOTE_HARDWARE_APP,
                    remote_hardware_pb2.HardwareMessage(type=kind, gpio_value=7, gpio_mask=15))
    assert result["type"] == name
    if payload is None:
        assert "payload" not in result
    else:
        assert result["payload"] == payload


@pytest.mark.parametrize("start,limit,expected", [(0, 0, None), (2, 3, None), (3, 1, 2)])
def test_hop_metadata(start, limit, expected):
    se = envelope()
    se.packet.hop_start, se.packet.hop_limit = start, limit
    result = decode_envelope(se.SerializeToString(), KEY)
    assert "rssi" not in result and "snr" not in result
    if expected is None:
        assert "hop_start" not in result and "hops_away" not in result
    else:
        assert result["hop_start"] == start and result["hops_away"] == expected


def test_channel_index_for_encrypted_and_decoded():
    se = envelope(encrypted=True)
    se.packet.channel = 8
    assert decode_envelope(se.SerializeToString(), KEY)["channel"] == 0
    assert decode_envelope(se.SerializeToString(), KEY, channel_index=3)["channel"] == 3
    se = envelope()
    se.packet.channel = 2
    assert decode_envelope(se.SerializeToString(), KEY, channel_index=3)["channel"] == 2


def test_json_formatting():
    assert dumps({"z": float("nan"), "a": [1.0, True, None, float("inf")]}) == '{"a":[1,true,null,null],"z":null}'
    assert dumps("a/b\x7f") == '"a\\/b\\u007f"'
    assert json.loads(dumps({"text": "Hello 🌎"})) == {"text": "Hello 🌎"}
