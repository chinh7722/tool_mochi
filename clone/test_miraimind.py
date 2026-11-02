#!/usr/bin/env python3
"""
MiraiMind Clone Comprehensive Verification Suite (clone.test_miraimind).
Runs unit and integration tests across all decrypted crypto, endpoints, and components.
"""
import unittest
import json
import base64
from pathlib import Path
import sys

CLONE_ROOT = Path(__file__).resolve().parent
if str(CLONE_ROOT) not in sys.path:
    sys.path.insert(0, str(CLONE_ROOT))
workspace_root = str(CLONE_ROOT.parent)
if workspace_root in sys.path:
    sys.path.remove(workspace_root)

from core.crypto.mdata_emulator import (
    native_m,
    native_h,
    generate_p384_keypair,
    derive_mdata_key,
    build_mdata,
    build_login_plaintext,
    java_url_encode,
)
from core.crypto.mmuid_codec import (
    sign_mmuid_101,
    sign_mmuid_v3_102,
    decode_mmuid_v3_103,
    decode_mmuid_107,
    build_mmuid_token,
)
from core.crypto.photon_crypto import (
    client_secret_gen,
    photonim_aes_encode,
    photonim_aes_decode,
    build_auth_packet,
    build_msg_packet,
)
from core.crypto.device_profile import DeviceProfile
from core.api.fetcher import MiraimindFetcher
from core.mail.mail_providers import OTP_REGEX


class TestMiraimindCrypto(unittest.TestCase):
    def test_native_m_and_h_roundtrip(self):
        key = "abcdef1234567890abcdef1234567890"
        data = json.dumps({"test": "hello", "code": 123456})
        encrypted = native_m(data, key)
        self.assertTrue(len(encrypted) > 20)
        decrypted = native_h(encrypted, key)
        self.assertEqual(decrypted, data)

    def test_p384_mdata_builder(self):
        priv, pub, pub_b64 = generate_p384_keypair()
        self.assertEqual(len(pub_b64), 132)
        md5_key = derive_mdata_key(pub_b64)
        self.assertEqual(len(md5_key), 32)

        mdata = build_mdata(identifier="user@example.com", public_key_b64=pub_b64)
        self.assertIn("mdata", mdata)
        self.assertEqual(mdata["response_decryption_key"], md5_key)

    def test_mmuid_101_signature(self):
        profile = DeviceProfile()
        fp = profile.build_mmuid_fingerprint("sess_123")
        res = sign_mmuid_101(fp)
        self.assertIn("mzip", res)
        self.assertTrue(len(res["mzip"]) > 40)

    def test_photon_packet_encryption(self):
        key, iv = client_secret_gen()
        payload = b"Hello Bot!"
        enc_packet = photonim_aes_encode(payload, key, iv, packet_type=2)
        self.assertEqual(enc_packet[0], 0xAA)
        ptype, dec_payload = photonim_aes_decode(enc_packet, key, iv)
        self.assertEqual(ptype, 2)
        self.assertEqual(dec_payload, payload)

    def test_otp_regex_parser(self):
        text = "Your verification code for MiraiMind is 829143. Do not share this code."
        matches = OTP_REGEX.findall(text)
        self.assertEqual(matches, ["829143"])

    def test_device_profile_generation(self):
        profile = DeviceProfile()
        self.assertEqual(len(profile.android_id), 16)
        self.assertTrue(len(profile.imei) >= 14)
        self.assertTrue(profile.get_user_agent().startswith("MiraiMind/"))


if __name__ == "__main__":
    unittest.main()
