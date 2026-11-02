"""
MiraiMind / Momo Photon IM Protocol Cryptography (core.photon_crypto).

Implements:
1. Photon native AES-128-CBC packet framing (photonim_aesEncode / photonim_aesDecode).
2. 7-byte Photon packet header serialization and parser.
3. Secret generation (clientSecretGen).
4. Direct message packet formatting for character interaction (PBPacket.auth / PBPacket.msg).
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
import struct
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    _HAS_CRYPTOGRAPHY = True
except ImportError:
    _HAS_CRYPTOGRAPHY = False

try:
    from Crypto.Cipher import AES
    _HAS_PYCRYPTODOME = True
except ImportError:
    _HAS_PYCRYPTODOME = False

from .mdata_emulator import pkcs7_pad, pkcs7_unpad

MAGIC_PACKET_BYTE = 0xAA


def client_secret_gen() -> Tuple[bytes, bytes]:
    """
    Mirrors native clientSecretGen.
    Produces the 16-byte session encryption key and 16-byte IV.
    """
    key = secrets.token_bytes(16)
    iv = secrets.token_bytes(16)
    return key, iv


def photonim_aes_encode(payload: bytes, key: bytes, iv: bytes, packet_type: int = 1) -> bytes:
    """
    Encrypts payload using Photon's native AES-128-CBC scheme.
    Packet Structure:
      - 1 byte: Magic (0xAA)
      - 2 bytes: Packet Type (big endian)
      - 4 bytes: Payload Length (big endian)
      - N bytes: AES-128-CBC encrypted data (PKCS#7 padded)
    """
    padded = pkcs7_pad(payload, 16)
    if _HAS_CRYPTOGRAPHY:
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        enc = cipher.encryptor()
        encrypted = enc.update(padded) + enc.finalize()
    elif _HAS_PYCRYPTODOME:
        cipher = AES.new(key, AES.MODE_CBC, iv)
        encrypted = cipher.encrypt(padded)
    else:
        raise RuntimeError("No crypto backend available")

    header = struct.pack(">BHI", MAGIC_PACKET_BYTE, packet_type, len(encrypted))
    return header + encrypted


def photonim_aes_decode(data: bytes, key: bytes, iv: bytes) -> Tuple[int, bytes]:
    """
    Parses and decrypts a Photon IM packet.
    Returns (packet_type, decrypted_payload).
    """
    if len(data) < 7:
        raise ValueError(f"Packet too short ({len(data)} bytes, minimum 7)")

    magic, packet_type, enc_len = struct.unpack(">BHI", data[:7])
    if magic != MAGIC_PACKET_BYTE:
        raise ValueError(f"Invalid Photon packet magic: {hex(magic)}, expected {hex(MAGIC_PACKET_BYTE)}")

    if len(data) < 7 + enc_len:
        raise ValueError(f"Packet truncated: expected {7 + enc_len} bytes, got {len(data)}")

    ciphertext = data[7 : 7 + enc_len]
    if len(ciphertext) % 16 != 0:
        raise ValueError("Ciphertext not block-aligned")

    if _HAS_CRYPTOGRAPHY:
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        dec = cipher.decryptor()
        raw = dec.update(ciphertext) + dec.finalize()
    elif _HAS_PYCRYPTODOME:
        cipher = AES.new(key, AES.MODE_CBC, iv)
        raw = cipher.decrypt(ciphertext)
    else:
        raise RuntimeError("No crypto backend available")

    unpadded = pkcs7_unpad(raw, 16)
    return packet_type, unpadded


def build_auth_packet(token: str, chat_user_id: str, app_id: str = "miraimind") -> bytes:
    """
    Builds the PBPacket.auth authentication payload for character chat.
    """
    ts = int(time.time() * 1000)
    auth_data = {
        "token": token,
        "chatUserId": chat_user_id,
        "appId": app_id,
        "timestamp": ts,
    }
    # Protobuf / JSON transport payload
    import json
    return json.dumps(auth_data, separators=(",", ":")).encode("utf-8")


def build_msg_packet(bot_id: str, message_text: str, chat_user_id: str) -> bytes:
    """
    Builds the PBPacket.msg text payload sent to character bots.
    """
    msg_id = secrets.token_hex(16)
    ts = int(time.time() * 1000)
    msg_data = {
        "msgId": msg_id,
        "botId": bot_id,
        "fromUser": chat_user_id,
        "content": message_text,
        "type": "text",
        "timestamp": ts,
    }
    import json
    return json.dumps(msg_data, separators=(",", ":")).encode("utf-8")
