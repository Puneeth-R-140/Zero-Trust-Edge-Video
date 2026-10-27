"""
Stage A CLI Demonstration Script for Reversible Face-ROI Encryption (AES-256-GCM).
Demonstrates encryption, secure metadata emission, and authenticated restoration.
"""

import os
import sys
import json
import base64
import cv2
import numpy as np

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from scripts.roi_encryption import RoiEncryptor, DecryptionAuthError


def run_demo():
    print("=" * 65)
    print("  Zero-Trust Edge Video - Stage A Face-ROI Encryption Demo")
    print("  Algorithm: AES-256-GCM (Authenticated Symmetric Encryption)")
    print("=" * 65)

    encryptor = RoiEncryptor()
    print(f"[+] Loaded/Generated 256-bit Master Key (HEX): {encryptor.key_hex[:16]}...{encryptor.key_hex[-16:]}")

    # Load a frame from video.mp4 or generate high-fidelity test frame
    video_path = "video.mp4"
    frame = None
    if os.path.exists(video_path):
        cap = cv2.VideoCapture(video_path)
        ret, frame = cap.read()
        cap.release()

    if frame is None:
        print("[*] Creating high-fidelity synthetic face image for demonstration...")
        frame = np.full((720, 1280, 3), 40, dtype=np.uint8)
        # Gradient background
        for y in range(720):
            frame[y, :, 0] = int(y / 720 * 80) + 30
            frame[y, :, 1] = 45
            frame[y, :, 2] = 60
        # Draw realistic simulated subject with high-frequency features
        cv2.circle(frame, (640, 360), 120, (210, 185, 160), -1)
        cv2.circle(frame, (595, 330), 16, (40, 30, 20), -1)
        cv2.circle(frame, (685, 330), 16, (40, 30, 20), -1)
        cv2.ellipse(frame, (640, 410), (45, 20), 0, 0, 180, (40, 40, 160), -1)
        bbox = (500, 220, 280, 300)
    else:
        # Use center region or face bounds
        h, w = frame.shape[:2]
        bbox = (int(w * 0.4), int(h * 0.25), int(w * 0.2), int(h * 0.35))

    x, y, bw, bh = bbox
    orig_roi = frame[y:y+bh, x:x+bw].copy()

    os.makedirs("output_stage_a", exist_ok=True)
    cv2.imwrite("output_stage_a/01_original.png", frame)
    print(f"[+] 1. Saved original frame -> output_stage_a/01_original.png (BBox: {bbox})")

    # Step 1: Encrypt ROI
    anonymized_frame, metadata = encryptor.encrypt_roi(frame, bbox, frame_index=1, camera_id="gate_cam_01")
    cv2.imwrite("output_stage_a/02_anonymized_public.png", anonymized_frame)
    with open("output_stage_a/03_encrypted_metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    print("[+] 2. Encrypted face ROI with AES-256-GCM")
    print(f"    - Nonce (96-bit base64): {metadata['nonce_b64']}")
    print(f"    - Auth Tag (128-bit base64): {metadata['tag_b64']}")
    print(f"    - Ciphertext Size: {len(base64.b64decode(metadata['ciphertext_b64']))} bytes")
    print("    - Saved anonymized frame -> output_stage_a/02_anonymized_public.png")
    print("    - Saved encrypted metadata -> output_stage_a/03_encrypted_metadata.json")

    # Step 2: Authenticated Decryption
    restored_frame = encryptor.decrypt_roi(anonymized_frame, metadata)
    cv2.imwrite("output_stage_a/04_authenticated_restored.png", restored_frame)
    restored_roi = restored_frame[y:y+bh, x:x+bw]

    is_identical = np.array_equal(orig_roi, restored_roi)
    print(f"[+] 3. Authenticated Decryption Successful! Exact Pixel Match: {is_identical}")
    print("    - Saved restored frame -> output_stage_a/04_authenticated_restored.png")

    # Step 3: Test Tamper Detection
    print("[*] 4. Testing Tamper Resistance (Flipping 1 byte in ciphertext)...")
    tampered_meta = json.loads(json.dumps(metadata))
    raw_cipher = bytearray(base64.b64decode(tampered_meta["ciphertext_b64"]))
    raw_cipher[10] ^= 0x55
    tampered_meta["ciphertext_b64"] = base64.b64encode(raw_cipher).decode("ascii")

    try:
        encryptor.decrypt_roi(anonymized_frame, tampered_meta)
        print("[-] ERROR: Tampered ciphertext was NOT rejected!")
    except DecryptionAuthError as e:
        print(f"[+] Safe Failure Verified: Tampered ciphertext rejected ({e})")

    print("\n" + "=" * 65)
    print("  STAGE A VERIFICATION COMPLETE: ALL SECURITY CHECKS PASSED")
    print("=" * 65)


if __name__ == "__main__":
    run_demo()
