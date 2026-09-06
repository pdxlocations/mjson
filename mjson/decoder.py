"""Protobuf decoding and AES-CTR adapted from pdxlocations/mmqtt (GPL-3.0-only)."""

import base64

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from google.protobuf.json_format import MessageToDict
from google.protobuf.message import DecodeError
from meshtastic import protocols
from meshtastic.protobuf import mesh_pb2, mqtt_pb2, portnums_pb2

DEFAULT_KEY = "1PG7OiApB1nwvP+rz05pAQ=="


class PacketError(ValueError):
    """A packet cannot be converted to usable JSON."""


def parse_key(value: str) -> bytes:
    if value == "AQ==":
        value = DEFAULT_KEY
    try:
        key = base64.b64decode(value, validate=True)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("MESHTASTIC_KEY must be a base64 AES key or AQ==") from exc
    if len(key) not in (16, 32):
        raise ValueError("MESHTASTIC_KEY must decode to 16 or 32 bytes")
    return key


def decrypt_packet(packet: mesh_pb2.MeshPacket, key: bytes) -> mesh_pb2.Data:
    nonce = packet.id.to_bytes(8, "little") + getattr(packet, "from").to_bytes(8, "little")
    decryptor = Cipher(algorithms.AES(key), modes.CTR(nonce)).decryptor()
    plaintext = decryptor.update(packet.encrypted) + decryptor.finalize()
    return mesh_pb2.Data.FromString(plaintext)


def as_dict(message) -> dict:
    return MessageToDict(message, preserving_proto_field_name=True)


def decode_envelope(payload: bytes, key: bytes) -> dict:
    try:
        envelope = mqtt_pb2.ServiceEnvelope.FromString(payload)
        if not envelope.HasField("packet"):
            raise PacketError("missing MeshPacket")
        packet = envelope.packet
        if packet.pki_encrypted:
            raise PacketError("PKI encrypted packet cannot use a channel key")
        if packet.WhichOneof("payload_variant") == "encrypted":
            packet.decoded.CopyFrom(decrypt_packet(packet, key))
        elif not packet.HasField("decoded"):
            raise PacketError("missing packet payload")
        data = packet.decoded
        if data.portnum == portnums_pb2.UNKNOWN_APP:
            raise PacketError("missing application port (possibly wrong key)")

        handler = protocols.get(data.portnum)
        kind = handler.name if handler else "unknown"
        if data.portnum in (
            portnums_pb2.TEXT_MESSAGE_APP,
            portnums_pb2.RANGE_TEST_APP,
            portnums_pb2.DETECTION_SENSOR_APP,
        ):
            application = {"text": data.payload.decode("utf-8")}
        elif handler and handler.protobufFactory:
            message = handler.protobufFactory()
            message.ParseFromString(data.payload)
            application = as_dict(message)
            if data.portnum == portnums_pb2.POSITION_APP:
                if "latitude_i" in application:
                    application["latitude"] = message.latitude_i / 1e7
                if "longitude_i" in application:
                    application["longitude"] = message.longitude_i / 1e7
        else:
            application = {"raw": base64.b64encode(data.payload).decode("ascii")}

        return {
            "id": packet.id,
            "from": getattr(packet, "from"),
            "to": packet.to,
            "sender": envelope.gateway_id,
            "channel": packet.channel,
            "type": kind,
            "payload": application,
            "timestamp": packet.rx_time,
        }
    except (DecodeError, UnicodeDecodeError) as exc:
        raise PacketError("invalid protobuf/text or incorrect channel key") from exc
