"""
MiraiMind Decrypted Cryptography Package.
Contains all reverse-engineered crypto algorithms:
- mdata_emulator: Native AES-256-CBC, P-384 ECDH, and mdata packaging.
- mmuid_codec: Hardware device attestation commands (101, 102/103, 107).
- photon_crypto: Photon IM protocol socket framing and encryption.
- device_profile: Android fingerprinting and hardware identity.
"""
from .mdata_emulator import (
    native_m,
    native_h,
    generate_p384_keypair,
    derive_mdata_key,
    build_mdata,
    build_login_plaintext,
    java_url_encode,
)
from .mmuid_codec import (
    sign_mmuid_101,
    sign_mmuid_v3_102,
    decode_mmuid_v3_103,
    decode_mmuid_107,
    build_mmuid_token,
)
from .photon_crypto import (
    client_secret_gen,
    photonim_aes_encode,
    photonim_aes_decode,
    build_auth_packet,
    build_msg_packet,
)
from .device_profile import DeviceProfile

__all__ = [
    "native_m",
    "native_h",
    "generate_p384_keypair",
    "derive_mdata_key",
    "build_mdata",
    "build_login_plaintext",
    "java_url_encode",
    "sign_mmuid_101",
    "sign_mmuid_v3_102",
    "decode_mmuid_v3_103",
    "decode_mmuid_107",
    "build_mmuid_token",
    "client_secret_gen",
    "photonim_aes_encode",
    "photonim_aes_decode",
    "build_auth_packet",
    "build_msg_packet",
    "DeviceProfile",
]
