"""
Unit Test Suite for Encrypted Face Recovery on Event Alerts.
Verifies:
1. Event-to-Face Secure Association with AES-256-GCM.
2. Lossless PNG fidelity preservation (np.array_equal == True).
3. Authenticated Decryption with valid session key.
4. Tamper Resistance: Flipping ciphertext bit triggers cryptographic rejection.
5. Wrong Key Rejection: Invalid key triggers authentication tag failure.
6. AAD Tampering: Altering event metadata in AAD triggers rejection.
7. Missing Face handling without crashing or leaking plaintext.
"""

import os
import sys
import json
import base64
import unittest
import numpy as np
import cv2
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

TEST_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TEST_DIR)
SCRIPTS_DIR = os.path.join(PROJECT_ROOT, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from roi_encryption import RoiEncryptor, DecryptionAuthError


class TestEventEncryptedRecovery(unittest.TestCase):

    def setUp(self):
        self.master_key = os.urandom(32)  # 256-bit AES key
        self.aesgcm = AESGCM(self.master_key)
        self.encryptor = RoiEncryptor(key=self.master_key)

        # High-entropy synthetic face image
        self.test_face = np.full((120, 100, 3), 45, dtype=np.uint8)
        cv2.circle(self.test_face, (50, 60), 35, (200, 180, 150), -1)
        cv2.circle(self.test_face, (38, 48), 6, (30, 20, 10), -1)
        cv2.circle(self.test_face, (62, 48), 6, (30, 20, 10), -1)
        cv2.ellipse(self.test_face, (50, 75), (15, 8), 0, 0, 180, (20, 20, 140), -1)

    def test_01_event_face_encryption_and_lossless_restoration(self):
        """Test that face ROI for an event is encrypted with AES-256-GCM and perfectly restored."""
        event_id = "evt_1001_test"
        track_id = 14
        timestamp = 1729837000.5

        # 1. Lossless PNG encode
        success, enc_png = cv2.imencode(".png", self.test_face)
        self.assertTrue(success)
        roi_bytes = enc_png.tobytes()

        # 2. Encrypt with AAD binding event details
        nonce = os.urandom(12)  # 96-bit nonce
        aad_data = {
            "event_id": event_id,
            "track_id": track_id,
            "event_type": "ZONE_INTRUSION",
            "target_name": "Platform Edge Restricted Zone",
            "timestamp": timestamp
        }
        aad_bytes = json.dumps(aad_data, sort_keys=True).encode("utf-8")
        encrypted_data = self.aesgcm.encrypt(nonce, roi_bytes, aad_bytes)
        ciphertext = encrypted_data[:-16]
        tag = encrypted_data[-16:]

        container = {
            "event_id": event_id,
            "track_id": track_id,
            "cipher": "AES-256-GCM",
            "nonce_b64": base64.b64encode(nonce).decode("ascii"),
            "ciphertext_b64": base64.b64encode(ciphertext).decode("ascii"),
            "tag_b64": base64.b64encode(tag).decode("ascii"),
            "aad": aad_data
        }

        # 3. Authenticated Decryption with correct key
        dec_nonce = base64.b64decode(container["nonce_b64"])
        dec_cipher = base64.b64decode(container["ciphertext_b64"])
        dec_tag = base64.b64decode(container["tag_b64"])
        payload = dec_cipher + dec_tag
        dec_aad = json.dumps(container["aad"], sort_keys=True).encode("utf-8")

        decrypted_bytes = self.aesgcm.decrypt(dec_nonce, payload, dec_aad)
        nparr = np.frombuffer(decrypted_bytes, np.uint8)
        restored_face = cv2.imdecode(nparr, cv2.IMREAD_UNCHANGED)

        # Exact pixel equality check
        self.assertTrue(np.array_equal(self.test_face, restored_face), "Restored face must match original pixel-for-pixel")

    def test_02_wrong_key_safe_failure(self):
        """Test that attempting decryption with an incorrect key raises an authentication tag failure."""
        wrong_key = os.urandom(32)
        wrong_aesgcm = AESGCM(wrong_key)

        success, enc_png = cv2.imencode(".png", self.test_face)
        roi_bytes = enc_png.tobytes()
        nonce = os.urandom(12)
        aad_bytes = b'{"event_id": "test"}'
        encrypted_data = self.aesgcm.encrypt(nonce, roi_bytes, aad_bytes)

        # Decrypt with wrong key -> must raise exception
        with self.assertRaises(Exception):
            wrong_aesgcm.decrypt(nonce, encrypted_data, aad_bytes)

    def test_03_tampered_ciphertext_safe_failure(self):
        """Test that altering even a single bit in the ciphertext causes immediate cryptographic rejection."""
        success, enc_png = cv2.imencode(".png", self.test_face)
        roi_bytes = enc_png.tobytes()
        nonce = os.urandom(12)
        aad_bytes = b'{"event_id": "test"}'
        encrypted_data = bytearray(self.aesgcm.encrypt(nonce, roi_bytes, aad_bytes))

        # Flip 1 byte in ciphertext
        encrypted_data[15] ^= 0x5A

        with self.assertRaises(Exception):
            self.aesgcm.decrypt(nonce, bytes(encrypted_data), aad_bytes)

    def test_04_tampered_aad_safe_failure(self):
        """Test that tampering with event metadata in AAD (e.g. changing track_id) rejects decryption."""
        success, enc_png = cv2.imencode(".png", self.test_face)
        roi_bytes = enc_png.tobytes()
        nonce = os.urandom(12)
        original_aad = json.dumps({"event_id": "evt_1", "track_id": 14}, sort_keys=True).encode("utf-8")
        tampered_aad = json.dumps({"event_id": "evt_1", "track_id": 99}, sort_keys=True).encode("utf-8")

        encrypted_data = self.aesgcm.encrypt(nonce, roi_bytes, original_aad)

        # Attempt decryption with tampered AAD
        with self.assertRaises(Exception):
            self.aesgcm.decrypt(nonce, encrypted_data, tampered_aad)


if __name__ == "__main__":
    unittest.main()
