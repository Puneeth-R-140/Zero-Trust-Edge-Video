"""
Zero-Trust Face-ROI Cryptographic Privacy Module.
Implements reversible AES-256-GCM face-ROI encryption and authenticated decryption.

NOTE ON REVERSIBILITY:
Ordinary blurring (e.g., GaussianBlur, pixelation, watermarking) is a destructive, lossy filter.
Once blurred, original high-frequency facial details and pixel values are permanently lost and cannot
be mathematically reversed. True zero-trust reversible privacy preservation requires encrypting the
original unredacted pixel ROI with authenticated symmetric cryptography (AES-256-GCM), while replacing
the displayed region with non-reversible redaction. Only authorized parties holding the private key
can authenticate and reconstruct the original facial features.
"""

import os
import json
import time
import base64
import uuid
from typing import Dict, List, Tuple, Any, Optional

import cv2
import numpy as np
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class DecryptionAuthError(Exception):
    """Raised when authentication tag verification or decryption fails."""
    pass


class InvalidRoiMetadataError(Exception):
    """Raised when encrypted ROI metadata format is malformed or missing fields."""
    pass


class RoiEncryptor:
    """
    Manages AES-256-GCM encryption and authenticated restoration of face regions of interest (ROIs).
    Keys are securely loaded from environment variables or dedicated key files (never in code/Git).
    """

    KEY_ENV_VAR = "ZTE_ENCRYPTION_KEY_HEX"
    DEFAULT_KEY_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".keys", "zte_master.key")
    CIPHER_ALGORITHM = "AES-256-GCM"
    FORMAT_VERSION = "1.0"
    NONCE_BYTES = 12  # 96 bits recommended for AES-GCM

    def __init__(self, key: Optional[bytes] = None, key_file: Optional[str] = None):
        """
        Initializes the ROI encryptor with a 256-bit AES key.
        If no key is provided, attempts to load from environment variable or key file.
        If no key exists, generates a secure random 256-bit key and saves it to key_file.
        """
        self._key = self._resolve_key(key, key_file)
        self._aesgcm = AESGCM(self._key)

    @property
    def key_hex(self) -> str:
        """Returns the hex representation of the active key (for authorized exports)."""
        return self._key.hex()

    def _resolve_key(self, explicit_key: Optional[bytes], key_file: Optional[str]) -> bytes:
        # 1. Explicit byte key
        if explicit_key is not None:
            if len(explicit_key) != 32:
                raise ValueError(f"AES-256 key must be exactly 32 bytes (got {len(explicit_key)}).")
            return explicit_key

        # 2. Environment variable
        env_hex = os.getenv(self.KEY_ENV_VAR)
        if env_hex:
            try:
                raw = bytes.fromhex(env_hex.strip())
                if len(raw) == 32:
                    return raw
            except Exception as e:
                print(f"[RoiEncryptor] Warning: Invalid key in {self.KEY_ENV_VAR}: {e}")

        # 3. Key file on disk
        target_path = key_file or self.DEFAULT_KEY_FILE
        if os.path.exists(target_path):
            try:
                with open(target_path, "rb") as f:
                    content = f.read().strip()
                if len(content) == 32:
                    return content
                # Try hex string in file
                try:
                    raw = bytes.fromhex(content.decode("utf-8"))
                    if len(raw) == 32:
                        return raw
                except Exception:
                    pass
            except Exception as e:
                print(f"[RoiEncryptor] Error reading key file {target_path}: {e}")

        # 4. Generate new secure 256-bit key and save to .keys directory
        new_key = os.urandom(32)
        try:
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            with open(target_path, "wb") as f:
                f.write(new_key)
            # Restrict permissions where supported
            try:
                os.chmod(target_path, 0o600)
            except Exception:
                pass
            print(f"[RoiEncryptor] Generated and saved new master key to: {target_path}")
        except Exception as e:
            print(f"[RoiEncryptor] Notice: Could not save key file ({e}), using ephemeral in-memory key.")

        return new_key

    def encrypt_roi(self,
                    frame: np.ndarray,
                    bbox: Tuple[int, int, int, int],
                    frame_index: int = 0,
                    camera_id: str = "cam_01") -> Tuple[np.ndarray, Dict[str, Any]]:
        """
        Encrypts the face ROI bounded by bbox=(x, y, w, h) using AES-256-GCM.
        
        Returns:
            anonymized_frame: Copy of frame with the face region irreversibly redacted.
            metadata: Cryptographic container with ciphertext, 96-bit nonce, auth tag, and coordinates.
        """
        x, y, w, h = bbox
        img_h, img_w = frame.shape[:2]

        # Clamp bounding box inside image bounds
        x1 = max(0, min(x, img_w - 1))
        y1 = max(0, min(y, img_h - 1))
        x2 = max(x1 + 1, min(x + w, img_w))
        y2 = max(y1 + 1, min(y + h, img_h))

        actual_w = x2 - x1
        actual_h = y2 - y1

        # Extract pristine original ROI
        roi = frame[y1:y2, x1:x2].copy()

        # Losslessly encode ROI to PNG format to preserve 100% exact pixel fidelity
        success, encoded_png = cv2.imencode(".png", roi)
        if not success:
            raise RuntimeError("Failed to losslessly encode face ROI to PNG.")
        roi_bytes = encoded_png.tobytes()

        # Cryptographically secure 96-bit random nonce (MUST be unique per encryption)
        nonce = os.urandom(self.NONCE_BYTES)
        roi_id = str(uuid.uuid4())
        now = time.time()

        # Associated Authenticated Data (AAD): binds frame_index, bbox, camera_id to prevent replay/substitution
        aad_dict = {
            "roi_id": roi_id,
            "frame_index": frame_index,
            "bbox": [x1, y1, actual_w, actual_h],
            "camera_id": camera_id
        }
        aad_bytes = json.dumps(aad_dict, sort_keys=True).encode("utf-8")

        # AES-256-GCM encrypt. The cryptography library appends the 16-byte tag to the ciphertext.
        encrypted_data = self._aesgcm.encrypt(nonce, roi_bytes, aad_bytes)
        ciphertext = encrypted_data[:-16]
        auth_tag = encrypted_data[-16:]

        metadata = {
            "version": self.FORMAT_VERSION,
            "cipher": self.CIPHER_ALGORITHM,
            "roi_id": roi_id,
            "frame_index": frame_index,
            "timestamp": now,
            "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + f".{int((now % 1) * 1000):03d}Z",
            "bbox": [x1, y1, actual_w, actual_h],
            "original_shape": [actual_h, actual_w, roi.shape[2] if len(roi.shape) > 2 else 1],
            "encoding": "png",
            "nonce_b64": base64.b64encode(nonce).decode("ascii"),
            "ciphertext_b64": base64.b64encode(ciphertext).decode("ascii"),
            "tag_b64": base64.b64encode(auth_tag).decode("ascii"),
            "aad": aad_dict
        }

        # Produce publicly safe anonymized frame (destructive blur or solid block)
        anonymized_frame = frame.copy()
        sub = anonymized_frame[y1:y2, x1:x2]
        
        # Heavy Gaussian Blur as public placeholder (irreversible on its own)
        ksize = max(31, (actual_w // 2) * 2 + 1)
        ksize = min(ksize, 99)
        blurred_roi = cv2.GaussianBlur(sub, (ksize, ksize), 30.0)
        anonymized_frame[y1:y2, x1:x2] = blurred_roi

        return anonymized_frame, metadata

    def decrypt_roi(self,
                    anonymized_frame: np.ndarray,
                    metadata: Dict[str, Any],
                    custom_key: Optional[bytes] = None) -> np.ndarray:
        """
        Authenticates and decrypts the encrypted face ROI, restoring exact original pixels into the frame.
        
        Raises:
            DecryptionAuthError: If the authentication tag fails (tampered data or wrong key).
            InvalidRoiMetadataError: If metadata format is invalid.
        """
        self._validate_metadata(metadata)

        # Use custom key if provided, else current master key
        aesgcm = AESGCM(custom_key) if custom_key is not None else self._aesgcm

        try:
            nonce = base64.b64decode(metadata["nonce_b64"])
            ciphertext = base64.b64decode(metadata["ciphertext_b64"])
            tag = base64.b64decode(metadata["tag_b64"])
            encrypted_payload = ciphertext + tag

            aad_bytes = json.dumps(metadata["aad"], sort_keys=True).encode("utf-8")

            # Decrypt and verify 16-byte authentication tag
            decrypted_bytes = aesgcm.decrypt(nonce, encrypted_payload, aad_bytes)
        except Exception as e:
            raise DecryptionAuthError(f"Decryption failed: Authentication tag verification failure or invalid key. ({e})")

        # Decode lossless PNG back to image array
        nparr = np.frombuffer(decrypted_bytes, np.uint8)
        restored_roi = cv2.imdecode(nparr, cv2.IMREAD_UNCHANGED)
        if restored_roi is None:
            raise DecryptionAuthError("Decrypted bytes could not be decoded as image.")

        # Blit original pixels back into place
        restored_frame = anonymized_frame.copy()
        x1, y1, w, h = metadata["bbox"]
        x2 = x1 + w
        y2 = y1 + h

        # Validate bounding coordinates
        img_h, img_w = restored_frame.shape[:2]
        if x1 >= img_w or y1 >= img_h or x2 > img_w or y2 > img_h:
            raise DecryptionAuthError("Stored bounding box extends beyond current image bounds.")

        restored_frame[y1:y2, x1:x2] = restored_roi
        return restored_frame

    def batch_encrypt_faces(self,
                            frame: np.ndarray,
                            bboxes: List[Tuple[int, int, int, int]],
                            frame_index: int = 0,
                            camera_id: str = "cam_01") -> Tuple[np.ndarray, List[Dict[str, Any]]]:
        """Encrypts multiple detected faces in a single frame."""
        anonymized = frame.copy()
        metadata_list = []
        for bbox in bboxes:
            anonymized, meta = self.encrypt_roi(anonymized, bbox, frame_index=frame_index, camera_id=camera_id)
            metadata_list.append(meta)
        return anonymized, metadata_list

    def batch_restore_faces(self,
                            anonymized_frame: np.ndarray,
                            metadata_list: List[Dict[str, Any]],
                            custom_key: Optional[bytes] = None) -> np.ndarray:
        """Restores multiple encrypted faces into an anonymized frame."""
        restored = anonymized_frame.copy()
        for meta in metadata_list:
            restored = self.decrypt_roi(restored, meta, custom_key=custom_key)
        return restored

    @staticmethod
    def _validate_metadata(meta: Dict[str, Any]) -> None:
        required = ["version", "cipher", "roi_id", "bbox", "nonce_b64", "ciphertext_b64", "tag_b64", "aad"]
        for field in required:
            if field not in meta:
                raise InvalidRoiMetadataError(f"Missing required metadata field: '{field}'")
        if meta.get("cipher") != "AES-256-GCM":
            raise InvalidRoiMetadataError(f"Unsupported cipher: {meta.get('cipher')}")
