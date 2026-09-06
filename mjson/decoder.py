"""Protobuf decoding and AES-CTR adapted from pdxlocations/mmqtt (GPL-3.0-only)."""

import base64

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from google.protobuf.message import DecodeError
from meshtastic.protobuf import mesh_pb2, mqtt_pb2, portnums_pb2

from .legacy import application_payload

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


def decode_envelope(payload: bytes, key: bytes, *, channel_index: int = 0, node_names=None) -> dict:
    try:
        envelope = mqtt_pb2.ServiceEnvelope.FromString(payload)
        if not envelope.HasField("packet"):
            raise PacketError("missing MeshPacket")
        packet = envelope.packet
        if packet.pki_encrypted:
            raise PacketError("PKI encrypted packet cannot use a channel key")
        if packet.WhichOneof("payload_variant") == "encrypted":
            packet.decoded.CopyFrom(decrypt_packet(packet, key))
            # Firmware serializes its decoded packet, whose channel is a local index.
            packet.channel = channel_index
        elif not packet.HasField("decoded"):
            raise PacketError("missing packet payload")
        if packet.decoded.portnum == portnums_pb2.UNKNOWN_APP:
            raise PacketError("missing application port (possibly wrong key)")
        kind, application, has_payload = application_payload(packet, node_names or {})
        document = {
            "id": packet.id,
            "from": getattr(packet, "from"),
            "to": packet.to,
            "sender": envelope.gateway_id,
            "channel": packet.channel,
            "type": kind,
            "timestamp": packet.rx_time,
        }
        if has_payload:
            document["payload"] = application
        if packet.rx_rssi:
            document["rssi"] = packet.rx_rssi
        if packet.rx_snr:
            document["snr"] = packet.rx_snr
        if packet.hop_start and packet.hop_limit <= packet.hop_start:
            document["hop_start"] = packet.hop_start
            document["hops_away"] = packet.hop_start - packet.hop_limit
        return document
    except (DecodeError, UnicodeDecodeError) as exc:
        raise PacketError("invalid protobuf/text or incorrect channel key") from exc
