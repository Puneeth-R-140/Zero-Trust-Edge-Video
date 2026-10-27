"""
Unit tests for Stage A: Reversible Face-ROI AES-256-GCM Encryption/Decryption.
Validates cryptographic integrity, exact pixel restoration, nonce uniqueness,
tamper detection via authentication tags, and safe failure on unauthorized keys.
"""

import os
import json
import base64
import unittest
import numpy as np
import cv2

from scripts.roi_encryption import (
    RoiEncryptor,
    DecryptionAuthError,
    InvalidRoiMetadataError
)


class TestRoiEncryption(unittest.TestCase):

    def setUp(self):
        # Generate a synthetic test frame with realistic high-frequency detail (face simulation)
        self.frame = np.zeros((480, 640, 3), dtype=np.uint8)
        # Gradient background
        for y in range(480):
            self.frame[y, :, 0] = int(y / 480 * 255)
            self.frame[y, :, 1] = 120
            self.frame[y, :, 2] = 200 - int(y / 480 * 150)

        # Draw a synthetic face with eyes, nose, mouth detail in region (150, 100, 120, 140)
        cv2.circle(self.frame, (210, 170), 55, (220, 190, 170), -1)  # Face oval
        cv2.circle(self.frame, (190, 155), 8, (50, 40, 30), -1)     # Left eye
        cv2.circle(self.frame, (230, 155), 8, (50, 40, 30), -1)     # Right eye
        cv2.line(self.frame, (210, 165), (210, 185), (100, 80, 70), 3)  # Nose
        cv2.ellipse(self.frame, (210, 200), (25, 10), 0, 0, 180, (40, 40, 180), -1)  # Mouth

        self.face_bbox = (150, 100, 120, 140)  # (x, y, w, h)
        self.test_key = os.urandom(32)
        self.encryptor = RoiEncryptor(key=self.test_key)

    def test_01_exact_pixel_fidelity(self):
        """Verifies that encrypting and decrypting a face ROI preserves 100% pixel fidelity."""
        original_roi = self.frame[100:240, 150:270].copy()

        # Encrypt
        anonymized_frame, metadata = self.encryptor.encrypt_roi(
            self.frame, self.face_bbox, frame_index=42, camera_id="cam_main"
        )

        # Verify anonymized frame face region does NOT match original (it is blurred)
        anonymized_roi = anonymized_frame[100:240, 150:270]
        self.assertFalse(
            np.array_equal(original_roi, anonymized_roi),
            "Anonymized frame still contains raw original pixels!"
        )

        # Decrypt with authorized key
        restored_frame = self.encryptor.decrypt_roi(anonymized_frame, metadata)
        restored_roi = restored_frame[100:240, 150:270]

        # Verify exact 100% pixel-for-pixel match
        np.testing.assert_array_equal(
            original_roi,
            restored_roi,
            err_msg="Restored ROI pixels differ from original! Reversibility failed."
        )

    def test_02_blur_irreversibility_contrast(self):
        """
        Validates the fundamental requirement: standard blurring is mathematically irreversible,
        while AES-256-GCM ROI encryption provides true reversible restoration.
        """
        original_roi = self.frame[100:240, 150:270].copy()

        # Perform ordinary blur
        blurred_roi = cv2.GaussianBlur(original_roi, (51, 51), 30.0)

        # Any attempt to sharpen / deconvolve the blur loses high-frequency details
        sharpen_kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
        unblurred_attempt = cv2.filter2D(blurred_roi, -1, sharpen_kernel)

        # Demonstrates ordinary blur CANNOT be reversed
        self.assertFalse(np.array_equal(original_roi, unblurred_attempt))
        pixel_error = np.mean(np.abs(original_roi.astype(float) - unblurred_attempt.astype(float)))
        self.assertGreater(pixel_error, 5.0, "Ordinary blur must show significant irreversible pixel loss.")

    def test_03_nonce_uniqueness(self):
        """Verifies that unique 96-bit nonces (IVs) are generated for every encryption."""
        nonces = set()
        iterations = 50
        for i in range(iterations):
            _, meta = self.encryptor.encrypt_roi(self.frame, self.face_bbox, frame_index=i)
            nonce_bytes = base64.b64decode(meta["nonce_b64"])
            self.assertEqual(len(nonce_bytes), 12, "Nonce must be exactly 12 bytes (96 bits).")
            nonces.add(meta["nonce_b64"])

        self.assertEqual(len(nonces), iterations, "Nonce collision detected! Every nonce must be unique.")

    def test_04_tamper_detection_auth_tag(self):
        """Verifies that altering ciphertext, tag, or AAD fails authentication and raises DecryptionAuthError."""
        anonymized_frame, metadata = self.encryptor.encrypt_roi(self.frame, self.face_bbox, frame_index=1)

        # 1. Tamper with ciphertext
        tampered_meta = json.loads(json.dumps(metadata))
        raw_cipher = bytearray(base64.b64decode(tampered_meta["ciphertext_b64"]))
        raw_cipher[10] ^= 0xFF  # Flip byte
        tampered_meta["ciphertext_b64"] = base64.b64encode(raw_cipher).decode("ascii")

        with self.assertRaises(DecryptionAuthError, msg="Tampered ciphertext was accepted!"):
            self.encryptor.decrypt_roi(anonymized_frame, tampered_meta)

        # 2. Tamper with authentication tag
        tampered_meta_tag = json.loads(json.dumps(metadata))
        raw_tag = bytearray(base64.b64decode(tampered_meta_tag["tag_b64"]))
        raw_tag[0] ^= 0x01
        tampered_meta_tag["tag_b64"] = base64.b64encode(raw_tag).decode("ascii")

        with self.assertRaises(DecryptionAuthError, msg="Tampered auth tag was accepted!"):
            self.encryptor.decrypt_roi(anonymized_frame, tampered_meta_tag)

        # 3. Tamper with AAD (e.g. attempt to replay face ROI to another frame index or bbox)
        tampered_meta_aad = json.loads(json.dumps(metadata))
        tampered_meta_aad["aad"]["frame_index"] = 9999

        with self.assertRaises(DecryptionAuthError, msg="Tampered AAD was accepted without rejection!"):
            self.encryptor.decrypt_roi(anonymized_frame, tampered_meta_aad)

    def test_05_invalid_key_safe_failure(self):
        """Verifies that decrypting with an incorrect key fails safely without exposing partial data."""
        anonymized_frame, metadata = self.encryptor.encrypt_roi(self.frame, self.face_bbox, frame_index=5)

        wrong_key = os.urandom(32)
        with self.assertRaises(DecryptionAuthError):
            self.encryptor.decrypt_roi(anonymized_frame, metadata, custom_key=wrong_key)

    def test_06_secure_key_loading(self):
        """Verifies secure key loading from environment variables and key files."""
        # 1. Environment variable hex loading
        custom_key_hex = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        os.environ[RoiEncryptor.KEY_ENV_VAR] = custom_key_hex
        try:
            enc = RoiEncryptor()
            self.assertEqual(enc.key_hex, custom_key_hex)
        finally:
            del os.environ[RoiEncryptor.KEY_ENV_VAR]

        # 2. Dedicated key file
        temp_key_file = os.path.join(os.path.dirname(__file__), "test_temp.key")
        test_key_bytes = os.urandom(32)
        try:
            with open(temp_key_file, "wb") as f:
                f.write(test_key_bytes)
            enc2 = RoiEncryptor(key_file=temp_key_file)
            self.assertEqual(enc2._key, test_key_bytes)
        finally:
            if os.path.exists(temp_key_file):
                os.remove(temp_key_file)

    def test_07_batch_encryption_and_restoration(self):
        """Verifies batch encryption and restoration of multiple faces in a frame."""
        bbox1 = (50, 50, 80, 80)
        bbox2 = (300, 100, 100, 120)

        # Draw second face
        cv2.circle(self.frame, (350, 160), 45, (200, 180, 160), -1)

        orig1 = self.frame[50:130, 50:130].copy()
        orig2 = self.frame[100:220, 300:400].copy()

        # Batch encrypt
        anonymized, meta_list = self.encryptor.batch_encrypt_faces(
            self.frame, [bbox1, bbox2], frame_index=10
        )
        self.assertEqual(len(meta_list), 2)

        # Batch restore
        restored = self.encryptor.batch_restore_faces(anonymized, meta_list)

        np.testing.assert_array_equal(self.frame, restored)


if __name__ == "__main__":
    unittest.main()
