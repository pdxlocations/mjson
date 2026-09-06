"""MQTT JSON mappings adapted from Meshtastic firmware v2.7.15 (GPL-3.0-only).

Reference: src/serialization/MeshPacketSerializer.cpp at v2.7.15.567b8ea.
"""

import json
import math

from meshtastic.protobuf import mesh_pb2, paxcount_pb2, portnums_pb2 as ports
from meshtastic.protobuf import remote_hardware_pb2, telemetry_pb2


def fields(message, names):
    return {name: getattr(message, name) for name in names.split()}


def present(message, mapping):
    return {output: getattr(message, source) for source, output in mapping.items()
            if message.HasField(source)}


def telemetry_payload(message):
    variant = message.WhichOneof("variant")
    if variant == "device_metrics":
        metrics = message.device_metrics
        result = fields(metrics, "voltage channel_utilization air_util_tx uptime_seconds")
        result.update(present(metrics, {"battery_level": "battery_level"}))
        return result
    if variant == "environment_metrics":
        names = ("temperature relative_humidity barometric_pressure gas_resistance voltage current "
                 "lux white_lux iaq distance wind_speed wind_direction wind_gust wind_lull radiation "
                 "ir_lux uv_lux weight rainfall_1h rainfall_24h soil_moisture soil_temperature")
        return present(message.environment_metrics, {name: name for name in names.split()})
    if variant == "air_quality_metrics":
        return present(message.air_quality_metrics, {
            "pm10_standard": "pm10", "pm25_standard": "pm25", "pm100_standard": "pm100",
            "pm10_environmental": "pm10_e", "pm25_environmental": "pm25_e", "pm100_environmental": "pm100_e",
        })
    if variant == "power_metrics":
        return present(message.power_metrics, {
            f"ch{channel}_{unit}": f"{unit}_ch{channel}"
            for channel in range(1, 4) for unit in ("voltage", "current")
        })
    return {}


PROTO_TYPES = {
    ports.TELEMETRY_APP: ("telemetry", telemetry_pb2.Telemetry),
    ports.NODEINFO_APP: ("nodeinfo", mesh_pb2.User),
    ports.POSITION_APP: ("position", mesh_pb2.Position),
    ports.WAYPOINT_APP: ("waypoint", mesh_pb2.Waypoint),
    ports.NEIGHBORINFO_APP: ("neighborinfo", mesh_pb2.NeighborInfo),
    ports.TRACEROUTE_APP: ("traceroute", mesh_pb2.RouteDiscovery),
    ports.PAXCOUNTER_APP: ("paxcounter", paxcount_pb2.Paxcount),
    ports.REMOTE_HARDWARE_APP: ("", remote_hardware_pb2.HardwareMessage),
}


def reject_json_constant(value):
    raise ValueError(value)


def application_payload(packet, node_names):
    data = packet.decoded
    port = data.portnum
    if port in (ports.TEXT_MESSAGE_APP, ports.DETECTION_SENSOR_APP):
        text = data.payload.split(b"\0", 1)[0].decode("utf-8")
        if port == ports.DETECTION_SENSOR_APP:
            return "detection", {"text": text}, True
        try:
            payload = json.loads(text, parse_int=float, parse_constant=reject_json_constant)
        except ValueError:
            payload = {"text": text}
        return "text", payload, True
    if port not in PROTO_TYPES or (port == ports.TRACEROUTE_APP and not data.request_id):
        return "", None, False
    kind, factory = PROTO_TYPES[port]
    message = factory.FromString(data.payload)
    if port == ports.TELEMETRY_APP:
        payload = telemetry_payload(message)
    elif port == ports.NODEINFO_APP:
        payload = {"id": message.id, "longname": message.long_name, "shortname": message.short_name,
                   "hardware": message.hw_model, "role": message.role}
    elif port == ports.POSITION_APP:
        payload = fields(message, "latitude_i longitude_i")
        payload.update({name: value for name, value in fields(message,
            "time timestamp altitude ground_speed ground_track sats_in_view PDOP HDOP VDOP precision_bits").items() if value})
    elif port == ports.WAYPOINT_APP:
        payload = fields(message, "id name description expire locked_to latitude_i longitude_i")
    elif port == ports.NEIGHBORINFO_APP:
        payload = fields(message, "node_id node_broadcast_interval_secs last_sent_by_id")
        payload["neighbors_count"] = len(message.neighbors)
        payload["neighbors"] = [{"node_id": neighbor.node_id, "snr": int(neighbor.snr)}
                                for neighbor in message.neighbors]
    elif port == ports.TRACEROUTE_APP:
        sender = getattr(packet, "from")
        payload = {
            "route": [node_names.get(node, "Unknown") for node in [packet.to, *message.route, sender]],
            "route_back": [node_names.get(node, "Unknown") for node in [sender, *message.route_back, packet.to]],
            "snr_towards": [value / 4 for value in message.snr_towards],
            "snr_back": [value / 4 for value in message.snr_back],
        }
    elif port == ports.PAXCOUNTER_APP:
        payload = {"wifi_count": message.wifi, "ble_count": message.ble, "uptime": message.uptime}
    else:
        if message.type == remote_hardware_pb2.HardwareMessage.GPIOS_CHANGED:
            return "gpios_changed", {"gpio_value": message.gpio_value}, True
        if message.type == remote_hardware_pb2.HardwareMessage.READ_GPIOS_REPLY:
            return "gpios_read_reply", fields(message, "gpio_value gpio_mask"), True
        return "", None, False
    return kind, payload, True


def dumps(value):
    """Match firmware's sorted keys, compact separators and 15-digit numbers."""
    if isinstance(value, dict):
        return "{" + ",".join(dumps(key) + ":" + dumps(value[key]) for key in sorted(value)) + "}"
    if isinstance(value, list):
        return "[" + ",".join(dumps(item) for item in value) + "]"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return format(value, ".15g") if math.isfinite(value) else "null"
    return json.dumps(value, ensure_ascii=False).replace("/", "\\/").replace("\x7f", "\\u007f")
