"""
MiraiMind / Momo Native Protocol Cryptography (core.mdata_emulator).

Implements:
1. Native AES-256-CBC cipher (MNative.m and MNative.h).
2. Key and IV derivation from SHA-512 with fixed suffixes 'mms' and 'ce'.
3. P-384 (secp384r1) ECDH ephemeral key exchange (MNative.o).
4. Java-compatible URL encoding and 'mdata' packaging.
"""
from __future__ import annotations

import base64
import hashlib
import json
import urllib.parse
from typing import Any, Dict, Optional, Tuple

try:
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    _HAS_CRYPTOGRAPHY = True
except ImportError:
    _HAS_CRYPTOGRAPHY = False

try:
    from Crypto.Cipher import AES
    _HAS_PYCRYPTODOME = True
except ImportError:
    _HAS_PYCRYPTODOME = False


def native_aes_parameters(native_key: str) -> Tuple[bytes, bytes]:
    """
    Derives 32-byte AES key and 16-byte IV from native_key using SHA-512.
    Key: first 29 characters of hex digest + 'mms' (32 bytes).
    IV: characters [32..46] of hex digest + 'ce' (16 bytes).
    """
    if not native_key:
        raise ValueError("native_key must be a non-empty string")
    material = hashlib.sha512(native_key.encode("ascii")).hexdigest()
    key = (material[:29] + "mms").encode("ascii")
    iv = (material[32:46] + "ce").encode("ascii")
    return key, iv


def pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    """Applies standard PKCS#7 padding."""
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len] * pad_len)


def pkcs7_unpad(data: bytes, block_size: int = 16) -> bytes:
    """Validates and strips PKCS#7 padding."""
    if not data or len(data) % block_size != 0:
        raise ValueError("Ciphertext not block-aligned for PKCS#7 unpadding")
    pad_len = data[-1]
    if pad_len < 1 or pad_len > block_size:
        raise ValueError(f"Invalid PKCS#7 padding byte: {pad_len}")
    if data[-pad_len:] != bytes([pad_len] * pad_len):
        raise ValueError("PKCS#7 padding bytes mismatch")
    return data[:-pad_len]


def native_m(plaintext: str, native_key: str) -> str:
    """
    Native encryption equivalent to MNative.m(plaintext, native_key).
    Encrypts UTF-8 plaintext with AES-256-CBC, computes SHA-1 over ciphertext,
    prepends SHA-1 (20 bytes), and returns Base64 string.
    """
    key, iv = native_aes_parameters(native_key)
    padded = pkcs7_pad(plaintext.encode("utf-8"))

    if _HAS_CRYPTOGRAPHY:
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        encryptor = cipher.encryptor()
        encrypted = encryptor.update(padded) + encryptor.finalize()
    elif _HAS_PYCRYPTODOME:
        cipher = AES.new(key, AES.MODE_CBC, iv)
        encrypted = cipher.encrypt(padded)
    else:
        raise RuntimeError("No suitable crypto backend found (need cryptography or pycryptodome)")

    sha1_digest = hashlib.sha1(encrypted).digest()
    envelope = sha1_digest + encrypted
    return base64.b64encode(envelope).decode("ascii")


def native_h(envelope_b64: str, native_key: str) -> str:
    """
    Native decryption equivalent to MNative.h(envelope_b64, native_key).
    Decodes Base64, verifies leading 20-byte SHA-1 digest, decrypts remainder
    with AES-256-CBC, strips PKCS#7 padding, and returns UTF-8 plaintext.
    """
    key, iv = native_aes_parameters(native_key)
    raw = base64.b64decode(envelope_b64)
    if len(raw) < 36:
        raise ValueError(f"Envelope length {len(raw)} too short (min 36 bytes)")

    expected_sha1 = raw[:20]
    encrypted = raw[20:]
    if len(encrypted) % 16 != 0:
        raise ValueError(f"Ciphertext length {len(encrypted)} not 16-byte aligned")

    actual_sha1 = hashlib.sha1(encrypted).digest()
    if expected_sha1 != actual_sha1:
        raise ValueError("SHA-1 integrity check failed in native_h")

    if _HAS_CRYPTOGRAPHY:
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        decrypted = decryptor.update(encrypted) + decryptor.finalize()
    elif _HAS_PYCRYPTODOME:
        cipher = AES.new(key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(encrypted)
    else:
        raise RuntimeError("No suitable crypto backend found")

    unpadded = pkcs7_unpad(decrypted)
    return unpadded.decode("utf-8")


def generate_p384_keypair() -> Tuple[Any, Any, str]:
    """
    Generates an ephemeral P-384 (secp384r1) keypair.
    Returns (private_key, public_key, public_key_b64).
    public_key_b64 is the Base64 representation of the uncompressed 97-byte point (0x04 + X + Y).
    """
    if not _HAS_CRYPTOGRAPHY:
        raise RuntimeError("cryptography package is required for P-384 key generation")

    private_key = ec.generate_private_key(ec.SECP384R1())
    public_key = private_key.public_key()
    pub_der = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    raw_point = pub_der[-97:]
    pub_b64 = base64.b64encode(raw_point).decode("ascii")
    return private_key, public_key, pub_b64


def derive_mdata_key(public_key_b64: str) -> str:
    """
    Computes the 32-character lowercase hex MD5 of the public key string.
    This serves as the native_key for the initial mdata exchange.
    """
    return hashlib.md5(public_key_b64.encode("ascii")).hexdigest().lower()


def derive_session_key(private_key: Any, server_public_key_b64: str) -> str:
    """
    Performs ECDH key agreement with the server's P-384 public key.
    Returns the derived shared secret encoded as Base64 for subsequent native_m/native_h.
    """
    if not _HAS_CRYPTOGRAPHY:
        raise RuntimeError("cryptography package is required for ECDH session derivation")

    raw_point = base64.b64decode(server_public_key_b64)
    server_public_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP384R1(), raw_point)
    shared_secret = private_key.exchange(ec.ECDH(), server_public_key)
    return base64.b64encode(shared_secret).decode("ascii")


def java_url_encode(s: str) -> str:
    """
    Java-compatible URL encode (URLEncoder.encode).
    Encodes spaces as '+', encodes punctuation, and uses uppercase hex characters.
    """
    return urllib.parse.quote_plus(s, safe="")


def build_login_plaintext(
    region_code: str = "VND",
    chat_user_id: str = "",
    identifier: str = "",
    mdp_login_type: int = 13,
    email_code: str = "",
    init_encrypt: int = 1,
    rom: str = "Android 14",
    extra: Optional[Dict[str, Any]] = None,
    common_params: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Constructs the canonical JSON payload for loginGuest / loginWeb / confirmEmailCode.
    """
    payload: Dict[str, Any] = {
        "regionCode": region_code,
        "chatUserId": chat_user_id,
        "identifier": identifier,
        "mdpLoginType": mdp_login_type,
        "code": email_code,
        "initEncrypt": init_encrypt,
        "rom": rom,
    }
    if extra:
        payload.update(extra)
    if common_params:
        payload.update(common_params)

    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def build_mdata(
    plaintext_json: Optional[str | Dict[str, Any]] = None,
    public_key_b64: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, str]:
    """
    Constructs the encrypted 'mdata' parameter for MiraiMind APIs:
    1. Generates ephemeral P-384 keypair if not provided.
    2. Derives native_key = md5(public_key_b64).
    3. Serializes plaintext JSON.
    4. Encrypts using native_m(plain, native_key).
    5. Concatenates '<encrypted>&<public_key_b64>'.
    6. URL-encodes with Java-compatible formatting.
    Returns:
      {
        "mdata": "<encoded string>",
        "response_decryption_key": "<md5_key>",
        "public_key": "<public_key_b64>",
      }
    """
    if public_key_b64 is None:
        _, _, public_key_b64 = generate_p384_keypair()

    md5_key = derive_mdata_key(public_key_b64)

    if plaintext_json is not None:
        if isinstance(plaintext_json, dict):
            plain = json.dumps(plaintext_json, separators=(",", ":"), ensure_ascii=False)
        else:
            plain = str(plaintext_json)
    else:
        plain = build_login_plaintext(**kwargs)

    encrypted = native_m(plain, md5_key)
    raw = f"{encrypted}&{public_key_b64}"
    mdata = java_url_encode(raw)

    return {
        "mdata": mdata,
        "response_decryption_key": md5_key,
        "public_key": public_key_b64,
    }
