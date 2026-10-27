"""
Zero-Trust Edge Video - UI Foundation Server.
Real-time Intel OpenVINO Face Anonymization Pipeline with Royal Contrast Dark UI.
Powered by Waitress for high-concurrency production-grade WSGI serving.
"""

import os
import sys
import time
import json
import threading
from typing import Dict, Any, List, Optional

import base64
from flask import Flask, render_template, Response, request, jsonify
from werkzeug.utils import secure_filename
import cv2
import numpy as np

# Resolve project paths
UI_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(UI_DIR)
SCRIPTS_DIR = os.path.join(PROJECT_ROOT, "scripts")
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "privacy_policies.json")
UPLOAD_FOLDER = os.path.join(PROJECT_ROOT, "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {"mp4", "avi", "mov", "mkv", "webm"}

def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from policy_manager import PolicyManager
from run_pipeline import SimpleTracker, apply_roi_padding, anonymize_roi
from analytics_engine import AnalyticsEngine
from roi_encryption import RoiEncryptor, DecryptionAuthError
from ultralytics import YOLO

app = Flask(
    __name__,
    template_folder=os.path.join(UI_DIR, "templates"),
    static_folder=os.path.join(UI_DIR, "static")
)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500 MB upload limit

# Load configuration from privacy_policies.json
def load_privacy_policies() -> Dict[str, Any]:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "policies": {
            "STANDARD": {"description": "Standard privacy preservation with DL Streamer Gaussian blur", "anonymization": "blur", "enabled": True},
            "HIGH_PRIVACY": {"description": "High privacy with expanded ROI padding for perimeter coverage", "anonymization": "strong_blur", "enabled": True},
            "ZERO_TRUST": {"description": "Cryptographic privacy protection staging (encryption module target)", "anonymization": "encryption", "enabled": False}
        }
    }

policy_config_data = load_privacy_policies()
pm = PolicyManager()

# Initialize Reversible Cryptographic Engine
roi_encryptor = RoiEncryptor()
recent_encrypted_rois: List[Dict[str, Any]] = []
recent_crypto_lock = threading.Lock()

# Global state and reentrant lock to eliminate any deadlock risk
state_lock = threading.RLock()
log_lock = threading.Lock()

pipeline_state = {
    "status": "RUNNING",  # Starts running immediately
    "input_source": "file",  # "file" or "webcam"
    "input_file": "video.mp4",
    "webcam_index": 0,
    "device": "GPU",  # "GPU" or "CPU"
    "policy": "STANDARD",
    "fps": 0.0,
    "latency_ms": 0.0,
    "frames_processed": 0,
    "faces_detected": 0,
    "crowd_level": "LOW",
    "crowd_count": 0,
    "entries": 0,
    "exits": 0,
    "occupancy": 0,
    "show_labels": True,
    "encryption_active": False,
    "encrypted_faces_count": 0,
    "crypto_key_fingerprint": roi_encryptor.key_hex[:8] + "..." + roi_encryptor.key_hex[-8:],
    "authorized_decryption_unlocked": False,
    "view_unblurred_video": False,
    "unblur_target": "all",
    "start_time": time.time()
}

activity_logs: List[Dict[str, str]] = []

def add_log(message: str, level: str = "INFO"):
    timestamp = time.strftime("%H:%M:%S")
    with log_lock:
        activity_logs.append({"time": timestamp, "message": message, "level": level})
        if len(activity_logs) > 60:
            activity_logs.pop(0)

add_log("Dashboard initialized. Models and OpenVINO runtime loaded.", "INFO")
add_log("Pipeline initialized: STANDARD policy on GPU acceleration.", "INFO")

# Load YOLO Face / OpenVINO Model once (for Face Anonymization)
model_dir = os.path.join(PROJECT_ROOT, "yolov12n-face_openvino_model")
if not os.path.exists(model_dir):
    model_dir = os.path.join(PROJECT_ROOT, "yolo11n_openvino_model")
if not os.path.exists(model_dir):
    model_dir = "yolo11n.pt"

print(f"[UI Server] Loading Face Model from: {model_dir}")
model = YOLO(model_dir, task="detect")
tracker = SimpleTracker()

# Load Person Model for whole-person analytics (separate from face model)
analytics_cfg_path = os.path.join(PROJECT_ROOT, "config", "analytics_config.json")
analytics_engine = AnalyticsEngine(config_path=analytics_cfg_path)
person_tracker = SimpleTracker()

person_model_cand = os.path.join(PROJECT_ROOT, "yolo11n_openvino_model")
if not os.path.exists(person_model_cand):
    person_model_cand = os.path.join(PROJECT_ROOT, "yolo11n.pt")
try:
    print(f"[UI Server] Loading Person Model for Analytics from: {person_model_cand}")
    person_model = YOLO(person_model_cand, task="detect")
except Exception as ex:
    print(f"[UI Server] Note: Person model fallback: {ex}")
    person_model = None

# Latest processed JPEG frame buffer (non-blocking for HTTP clients)
latest_frame_bytes: Optional[bytes] = None
frame_condition = threading.Condition()


def make_diagnostic_frame(title: str, subtitle: str, color=(245, 245, 245)) -> bytes:
    """Renders a sleek standby / diagnostic frame with dark royal contrast aesthetics."""
    img = np.zeros((480, 720, 3), dtype=np.uint8)
    img[:] = (8, 8, 8)
    # Subtle dark grid
    for y in range(0, 480, 40):
        cv2.line(img, (0, y), (720, y), (20, 20, 20), 1)
    for x in range(0, 720, 40):
        cv2.line(img, (x, 0), (x, 480), (20, 20, 20), 1)
    
    # Restrained accent top border (#3B82F6 in BGR is (246, 130, 59))
    cv2.line(img, (0, 0), (720, 0), (246, 130, 59), 2)

    cv2.putText(img, title, (int(360 - len(title) * 6.5), 230), cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2, cv2.LINE_AA)
    cv2.putText(img, subtitle, (int(360 - len(subtitle) * 4.5), 270), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (115, 115, 115), 1, cv2.LINE_AA)
    _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return buf.tobytes()


def background_pipeline_worker():
    """Continuous background worker capturing and anonymizing frames."""
    global latest_frame_bytes, tracker
    cap = None
    last_source = None
    last_file = None
    frame_idx = 0
    start_time = time.time()
    last_fps_time = time.time()
    webcam_fail_count = 0

    standby_bytes = make_diagnostic_frame("STANDBY • PIPELINE PAUSED", "Click 'Start Pipeline' to resume real-time processing")

    while True:
        with state_lock:
            status = pipeline_state["status"]
            src_type = pipeline_state["input_source"]
            policy_name = pipeline_state["policy"]
            dev = pipeline_state["device"]
            webcam_idx = pipeline_state["webcam_index"]
            input_file = pipeline_state["input_file"]
            show_labels = pipeline_state["show_labels"]

        # When stopped, broadcast standby frame and sleep lightly
        if status != "RUNNING":
            if cap is not None:
                cap.release()
                cap = None
                last_source = None
                last_file = None
            with frame_condition:
                latest_frame_bytes = standby_bytes
                frame_condition.notify_all()
            time.sleep(0.08)
            continue

        # Open / switch source safely
        if cap is None or src_type != last_source or (src_type == "file" and input_file != last_file):
            if cap is not None:
                cap.release()
                cap = None

            # Render transient loading frame
            trans_bytes = make_diagnostic_frame(f"CONNECTING TO {src_type.upper()}...", "Initializing edge video stream...", (0, 215, 255))
            with frame_condition:
                latest_frame_bytes = trans_bytes
                frame_condition.notify_all()

            if src_type == "webcam":
                add_log(f"Opening Live Webcam device [{webcam_idx}]...", "INFO")
                # Try CAP_DSHOW on Windows first, fall back to standard capture
                cap = cv2.VideoCapture(webcam_idx, cv2.CAP_DSHOW)
                if not cap.isOpened():
                    cap = cv2.VideoCapture(webcam_idx)
                
                if cap.isOpened():
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                    cap.set(cv2.CAP_PROP_FPS, 30)
                    add_log(f"Live Webcam stream active (Device #{webcam_idx}).", "INFO")
                else:
                    add_log(f"Warning: Webcam device {webcam_idx} could not be opened.", "WARN")
            else:
                target_video = os.path.join(PROJECT_ROOT, input_file)
                cap = cv2.VideoCapture(target_video)
                add_log(f"Streaming video file: {input_file}", "INFO")

            last_source = src_type
            last_file = input_file
            frame_idx = 0
            start_time = time.time()
            webcam_fail_count = 0

        # Read next frame
        ret = False
        frame = None
        if cap is not None and cap.isOpened():
            ret, frame = cap.read()

        if not ret or frame is None:
            if src_type == "file" and cap is not None:
                # Loop video file continuously
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ret_retry, frame = cap.read()
                if not ret_retry or frame is None:
                    # Reopen file stream if loop seek failed
                    cap.release()
                    target_video = os.path.join(PROJECT_ROOT, input_file)
                    cap = cv2.VideoCapture(target_video)
                    time.sleep(0.04)
                    continue
            elif src_type == "webcam":
                webcam_fail_count += 1
                if webcam_fail_count % 30 == 1:
                    add_log("Webcam frame read dropped. Waiting for video device...", "WARN")
                cam_err_bytes = make_diagnostic_frame("WEBCAM STREAM UNAVAILABLE", "Check camera permissions or switch to File source", (0, 100, 255))
                with frame_condition:
                    latest_frame_bytes = cam_err_bytes
                    frame_condition.notify_all()
                time.sleep(0.08)
                continue
            else:
                time.sleep(0.04)
                continue
        else:
            webcam_fail_count = 0

        frame_idx += 1
        h, w = frame.shape[:2]

        # Resolve active policy parameters
        try:
            pol_obj = pm.get_policy(policy_name)
            conf_thresh = pol_obj.confidence
            interval = pol_obj.inference_interval
            anonymization_method = pol_obj.anonymization
            padding_factor = pol_obj.padding_factor
        except Exception:
            conf_thresh = 0.5
            interval = 2
            anonymization_method = "blur"
            padding_factor = 0.0

        is_zero_trust = (policy_name == "ZERO_TRUST")
        if policy_name == "HIGH_PRIVACY":
            anonymization_method = "strong_blur"
            padding_factor = 0.25
            conf_thresh = 0.35
        elif policy_name == "ZERO_TRUST":
            anonymization_method = "encryption"
            padding_factor = 0.25
            conf_thresh = 0.35

        with state_lock:
            pipeline_state["encryption_active"] = is_zero_trust
            unlocked = pipeline_state.get("authorized_decryption_unlocked", False)
            view_unblurred = pipeline_state.get("view_unblurred_video", False) if unlocked else False
            unblur_target = str(pipeline_state.get("unblur_target", "all")).strip()

        # Inference & Tracking
        t0 = time.time()
        active_tracks = []
        try:
            if (frame_idx % interval == 1) or (interval <= 1):
                results = model.predict(frame, conf=conf_thresh, verbose=False)
                detections = []
                for box in results[0].boxes:
                    bx1, by1, bx2, by2 = map(int, box.xyxy[0].tolist())
                    bconf = float(box.conf[0])
                    detections.append((bx1, by1, bx2, by2, bconf))
                active_tracks = tracker.update(detections)
            else:
                active_tracks = tracker.get_current()
        except Exception as e:
            active_tracks = tracker.get_current()

        latency = (time.time() - t0) * 1000.0

        # Apply Privacy Anonymization
        for track in active_tracks:
            bx1, by1, bx2, by2 = track["box"]
            conf = track["conf"]
            tid = track.get("id", 0)

            # Determine whether THIS specific face should be unblurred
            should_unmask_this_face = False
            if unlocked and view_unblurred:
                if unblur_target in ("all", "", "None"):
                    should_unmask_this_face = True
                elif unblur_target == str(tid):
                    should_unmask_this_face = True

            px1, py1, px2, py2 = apply_roi_padding(bx1, by1, bx2, by2, padding_factor, w, h)

            if is_zero_trust:
                # Reversible AES-256-GCM ROI Encryption
                try:
                    anonymized_frame, enc_meta = roi_encryptor.encrypt_roi(
                        frame,
                        (px1, py1, px2 - px1, py2 - py1),
                        frame_index=frame_idx,
                        camera_id=src_type
                    )
                    enc_meta["track_id"] = tid

                    with recent_crypto_lock:
                        recent_encrypted_rois.append(enc_meta)
                        if len(recent_encrypted_rois) > 40:
                            recent_encrypted_rois.pop(0)

                    with state_lock:
                        pipeline_state["encrypted_faces_count"] += 1

                    if analytics_engine is not None:
                        crypto_ev = {
                            "event_id": enc_meta["roi_id"],
                            "event_type": "ENCRYPTED_FACE_ROI",
                            "timestamp": enc_meta["timestamp"],
                            "timestamp_iso": enc_meta["timestamp_iso"],
                            "frame_index": frame_idx,
                            "track_id": tid,
                            "cipher": enc_meta["cipher"],
                            "bbox": enc_meta["bbox"],
                            "nonce_b64": enc_meta["nonce_b64"],
                            "tag_b64": enc_meta["tag_b64"],
                            "ciphertext_len": len(enc_meta["ciphertext_b64"]),
                            "policy": "ZERO_TRUST"
                        }
                        analytics_engine.emitted_events.append(crypto_ev)
                        if len(analytics_engine.emitted_events) > 40:
                            analytics_engine.emitted_events.pop(0)

                except Exception as ex:
                    # Safe failure fallback: destructive blur if encryption fails
                    anonymize_roi(frame, px1, py1, px2, py2, "blur")

                # If operator unblurred this face, leave original pixels; otherwise apply encrypted noise
                if not should_unmask_this_face:
                    frame[py1:py2, px1:px2] = anonymized_frame[py1:py2, px1:px2]

                if show_labels:
                    if should_unmask_this_face:
                        lbl = f"FACE #{tid} [CLEAR • OPERATOR UNMASKED]"
                        color = (16, 185, 129)
                    elif unlocked:
                        lbl = f"AES-GCM #{tid} [AUTH UNLOCKED (MASKED)]"
                        color = (56, 189, 248)
                    else:
                        lbl = f"AES-GCM #{tid} [ENCRYPTED]"
                        color = (244, 63, 94)
                    cv2.rectangle(frame, (px1, py1), (px2, py2), color, 2)
                    (lw, lh), _ = cv2.getTextSize(lbl, cv2.FONT_HERSHEY_SIMPLEX, 0.44, 2)
                    lbl_y = max(20, py1 - 8)
                    cv2.rectangle(frame, (px1, lbl_y - lh - 4), (px1 + lw + 6, lbl_y + 2), (15, 15, 20), -1)
                    cv2.rectangle(frame, (px1, lbl_y - lh - 4), (px1 + lw + 6, lbl_y + 2), color, 1)
                    cv2.putText(frame, lbl, (px1 + 3, lbl_y - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.44, color, 2, cv2.LINE_AA)

            else:
                if not should_unmask_this_face:
                    anonymize_roi(frame, px1, py1, px2, py2, anonymization_method)

                if show_labels:
                    if should_unmask_this_face:
                        lbl = f"FACE #{tid} [CLEAR • OPERATOR UNMASKED]"
                        color = (16, 185, 129)
                    else:
                        lbl = f"FACE #{tid} {int(conf * 100)}%"
                        color = (0, 220, 255) if anonymization_method == "strong_blur" else (0, 255, 128)
                    cv2.rectangle(frame, (px1, py1), (px2, py2), color, 2)
                    (lw, lh), _ = cv2.getTextSize(lbl, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 2)
                    lbl_y = max(20, py1 - 8)
                    cv2.rectangle(frame, (px1, lbl_y - lh - 4), (px1 + lw + 6, lbl_y + 2), (15, 15, 20), -1)
                    cv2.rectangle(frame, (px1, lbl_y - lh - 4), (px1 + lw + 6, lbl_y + 2), color, 1)
                    cv2.putText(frame, lbl, (px1 + 3, lbl_y - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2, cv2.LINE_AA)

        # Draw top banner if unblurred video stream is active
        if unlocked and view_unblurred:
            if unblur_target in ("all", "", "None"):
                banner_txt = "MASTER KEY VERIFIED: LIVE UNBLURRED STREAM (ALL FACES)"
            else:
                banner_txt = f"MASTER KEY VERIFIED: UNBLURRED (PERSON #{unblur_target} ONLY - OTHERS MASKED)"
            cv2.rectangle(frame, (10, 10), (540, 36), (10, 28, 18), -1)
            cv2.rectangle(frame, (10, 10), (540, 36), (16, 185, 129), 1)
            cv2.putText(frame, banner_txt, (18, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (16, 185, 129), 1, cv2.LINE_AA)

        # Advanced Privacy-Preserving Analytics (Whole-Person Tracking)
        now = time.time()
        an_events = []
        if analytics_engine is not None:
            try:
                active_person_tracks = []
                if person_model is not None:
                    if (frame_idx % interval == 1) or (interval <= 1):
                        p_res = person_model.predict(frame, classes=[0], conf=0.35, verbose=False)
                        p_dets = []
                        for pbox in p_res[0].boxes:
                            px1, py1, px2, py2 = map(int, pbox.xyxy[0].tolist())
                            p_dets.append((px1, py1, px2, py2, float(pbox.conf[0])))
                        active_person_tracks = person_tracker.update(p_dets)
                    else:
                        active_person_tracks = person_tracker.get_current()
                
                # Combine person tracks or fallback to active face tracks if webcam close-up
                tracks_to_feed = active_person_tracks if len(active_person_tracks) > 0 else active_tracks
                an_events = analytics_engine.update(tracks_to_feed, timestamp=now)
                for ev in an_events:
                    if ev["event_type"] == "CROWD_CONGESTION":
                        add_log(f"ALERT: High crowd congestion ({ev['crowd_count']} persons)!", "WARN")
                    elif ev["event_type"] == "ZONE_INTRUSION":
                        add_log(f"ALERT: Intrusion into {ev.get('zone_name', 'Restricted Zone')}!", "WARN")
                    elif ev["event_type"] == "TRIPWIRE_CROSSING":
                        add_log(f"Tripwire: {ev.get('direction', 'CROSSING')} (Occupancy: {ev.get('occupancy')})", "INFO")

                analytics_engine.draw_overlay(frame)
            except Exception:
                pass

        an_state = analytics_engine.get_summary_state() if analytics_engine else {}

        # Update telemetry
        with state_lock:
            pipeline_state["frames_processed"] = frame_idx
            pipeline_state["faces_detected"] = len(active_tracks)
            pipeline_state["latency_ms"] = round(latency, 1)
            pipeline_state["crowd_level"] = an_state.get("crowd_level", "LOW")
            pipeline_state["crowd_count"] = an_state.get("crowd_count", 0)
            pipeline_state["entries"] = an_state.get("entries", 0)
            pipeline_state["exits"] = an_state.get("exits", 0)
            pipeline_state["occupancy"] = an_state.get("occupancy", 0)
            if now - last_fps_time >= 0.35:
                pipeline_state["fps"] = round(frame_idx / max(0.001, (now - start_time)), 1)
                last_fps_time = now

        # Telemetry overlay badge on frame (Top-Left)
        status_line = f"ACTIVE: {policy_name} | {dev} | {src_type.upper()} | {pipeline_state['fps']:.1f} FPS"
        (sw, sh), _ = cv2.getTextSize(status_line, cv2.FONT_HERSHEY_SIMPLEX, 0.48, 2)
        cv2.rectangle(frame, (14, 14), (26 + sw, 38), (12, 12, 16), -1)
        cv2.rectangle(frame, (14, 14), (26 + sw, 38), (75, 75, 85), 1, cv2.LINE_AA)
        cv2.putText(frame, status_line, (20, 31), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (240, 240, 240), 2, cv2.LINE_AA)

        # Encode JPEG and notify streaming clients
        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        with frame_condition:
            latest_frame_bytes = buf.tobytes()
            frame_condition.notify_all()

        time.sleep(0.015)


# Start background worker
worker_thread = threading.Thread(target=background_pipeline_worker, daemon=True)
worker_thread.start()


@app.route("/")
def index():
    return render_template("index.html", policies=policy_config_data.get("policies", {}))


@app.route("/video_feed")
def video_feed():
    def stream_generator():
        try:
            while True:
                with frame_condition:
                    frame_condition.wait(timeout=0.25)
                    frame = latest_frame_bytes
                if frame is not None:
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")
                else:
                    time.sleep(0.04)
        except (GeneratorExit, ConnectionResetError, BrokenPipeError, OSError):
            pass

    return Response(stream_generator(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/api/config", methods=["GET"])
def api_config():
    return jsonify(policy_config_data)


@app.route("/api/status", methods=["GET"])
def api_status():
    with state_lock:
        data = dict(pipeline_state)
    with log_lock:
        data["logs"] = list(activity_logs)
    if analytics_engine:
        data["analytics"] = analytics_engine.get_summary_state()
    with recent_crypto_lock:
        data["recent_encrypted_rois"] = [
            {
                "roi_id": r["roi_id"],
                "track_id": r.get("track_id", 1),
                "frame_index": r["frame_index"],
                "timestamp_iso": r["timestamp_iso"],
                "bbox": r["bbox"],
                "cipher": r["cipher"],
                "nonce_b64": r["nonce_b64"],
                "tag_b64": r["tag_b64"]
            }
            for r in recent_encrypted_rois[-12:]
        ]
    return jsonify(data)


@app.route("/api/events/live", methods=["GET"])
def api_events_live():
    limit = int(request.args.get("limit", 30))
    events = []
    if analytics_engine and analytics_engine.emitted_events:
        events = list(analytics_engine.emitted_events)[-limit:]

    with state_lock:
        telemetry = {
            "timestamp": time.time(),
            "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "status": pipeline_state["status"],
            "policy": pipeline_state["policy"],
            "encryption_active": pipeline_state["encryption_active"],
            "encrypted_faces_count": pipeline_state["encrypted_faces_count"],
            "crowd_count": pipeline_state["crowd_count"],
            "crowd_level": pipeline_state["crowd_level"],
            "occupancy": pipeline_state["occupancy"],
            "fps": pipeline_state["fps"],
            "latency_ms": pipeline_state["latency_ms"],
            "frames_processed": pipeline_state["frames_processed"]
        }

    return jsonify({
        "status": "ok",
        "count": len(events),
        "telemetry": telemetry,
        "events": events
    })


@app.route("/api/crypto/status", methods=["GET"])
def api_crypto_status():
    with state_lock:
        active = pipeline_state.get("encryption_active", False)
        count = pipeline_state.get("encrypted_faces_count", 0)
        unlocked = pipeline_state.get("authorized_decryption_unlocked", False)
        pol = pipeline_state.get("policy", "STANDARD")

    with recent_crypto_lock:
        rois_count = len(recent_encrypted_rois)
        sample_meta = recent_encrypted_rois[-1] if recent_encrypted_rois else None

    return jsonify({
        "status": "ok",
        "cipher": "AES-256-GCM",
        "key_fingerprint": roi_encryptor.key_hex[:8] + "..." + roi_encryptor.key_hex[-8:],
        "encryption_active": active,
        "policy": pol,
        "total_encrypted_faces": count,
        "authorized_decryption_unlocked": unlocked,
        "recent_rois_count": rois_count,
        "sample_roi": sample_meta
    })


@app.route("/api/crypto/decrypt", methods=["POST"])
def api_crypto_decrypt():
    data = request.get_json(force=True, silent=True) or {}
    provided_key_hex = str(data.get("key_hex", "")).strip()
    use_master_key = bool(data.get("use_master_key", False))
    roi_id = data.get("roi_id")
    enable_unblurred = data.get("enable_unblurred_stream", None)

    custom_key = None
    if use_master_key:
        custom_key = roi_encryptor._key
    elif provided_key_hex:
        try:
            custom_key = bytes.fromhex(provided_key_hex)
            if len(custom_key) != 32:
                return jsonify({"status": "error", "message": "Invalid key length: Must be exactly 64 hex characters (256-bit AES key)."}), 400
        except Exception:
            return jsonify({"status": "error", "message": "Invalid hex format in provided cryptographic key."}), 400
    else:
        return jsonify({"status": "error", "message": "Cryptographic key required. Enter a 64-character hex key or select 'Use Active Master Key'."}), 400

    with recent_crypto_lock:
        rois_snapshot = list(recent_encrypted_rois)

    if not rois_snapshot:
        if custom_key == roi_encryptor._key:
            with state_lock:
                pipeline_state["authorized_decryption_unlocked"] = True
                if enable_unblurred is not None:
                    pipeline_state["view_unblurred_video"] = bool(enable_unblurred)
            add_log("Authorized operator key verified (No active face ROIs in buffer yet).", "INFO")
            return jsonify({
                "status": "ok",
                "message": "Authorized master key validated. No encrypted ROIs in buffer yet (select ZERO_TRUST policy and detect a face to capture ROIs).",
                "unlocked": True,
                "view_unblurred_video": pipeline_state.get("view_unblurred_video", False)
            })
        else:
            add_log("Unauthorized key test rejected.", "WARN")
            return jsonify({
                "status": "error",
                "message": "Authentication verification failed: Key does not match active session master key."
            }), 403

    dummy_frame = np.zeros((1080, 1920, 3), dtype=np.uint8)


    target_roi = None
    track_id_target = data.get("track_id", None)
    if roi_id and roi_id not in ("latest", "all"):
        for r in reversed(rois_snapshot):
            if r["roi_id"] == roi_id or str(r.get("track_id")) == str(roi_id):
                target_roi = r
                break
    if target_roi is None and track_id_target not in (None, "", "latest", "all"):
        for r in reversed(rois_snapshot):
            if str(r.get("track_id")) == str(track_id_target):
                target_roi = r
                break
    if target_roi is None:
        target_roi = rois_snapshot[-1]

    try:
        restored_frame = roi_encryptor.decrypt_roi(dummy_frame, target_roi, custom_key=custom_key)
        x, y, w, h = target_roi["bbox"]
        decrypted_roi = restored_frame[y:y+h, x:x+w]
        _, buf = cv2.imencode(".png", decrypted_roi)
        decrypted_b64 = base64.b64encode(buf.tobytes()).decode("ascii")

        with state_lock:
            pipeline_state["authorized_decryption_unlocked"] = True
            # DO NOT auto-unblur video stream! Only unblur when explicitly requested
            if enable_unblurred is True:
                pipeline_state["view_unblurred_video"] = True
            elif enable_unblurred is False:
                pipeline_state["view_unblurred_video"] = False

        add_log(f"Authorized Decryption Verified: Restored Face Image for Person #{target_roi.get('track_id', 1)}", "INFO")
        return jsonify({
            "status": "ok",
            "mode": "single",
            "message": f"Cryptographic authentication tag verified! Face image restored for Person #{target_roi.get('track_id', 1)}.",
            "unlocked": True,
            "view_unblurred_video": pipeline_state.get("view_unblurred_video", False),
            "unblur_target": pipeline_state.get("unblur_target", "all"),
            "roi_id": target_roi["roi_id"],
            "track_id": target_roi.get("track_id", 1),
            "cipher": target_roi.get("cipher", "AES-256-GCM"),
            "decrypted_image_b64": f"data:image/png;base64,{decrypted_b64}",
            "bbox": target_roi["bbox"],
            "frame_index": target_roi["frame_index"],
            "timestamp": target_roi["timestamp_iso"]
        })
    except DecryptionAuthError as ex:
        add_log(f"Decryption authentication failed: {ex}", "WARN")
        return jsonify({
            "status": "error",
            "message": f"Authentication Tag Verification Failed: {str(ex)}"
        }), 403
    except Exception as ex:
        return jsonify({
            "status": "error",
            "message": f"Decryption error: {str(ex)}"
        }), 400


@app.route("/api/crypto/toggle_stream_unmask", methods=["POST"])
def api_crypto_toggle_stream_unmask():
    data = request.get_json(force=True, silent=True) or {}
    unmask = data.get("unmask", None)
    target = str(data.get("target", "all")).strip()

    with state_lock:
        if not pipeline_state.get("authorized_decryption_unlocked", False):
            return jsonify({
                "status": "error",
                "message": "Master Key authentication required before unblurring live video."
            }), 403

        if unmask is None:
            pipeline_state["view_unblurred_video"] = not pipeline_state.get("view_unblurred_video", False)
        else:
            pipeline_state["view_unblurred_video"] = bool(unmask)

        if target:
            pipeline_state["unblur_target"] = target

        active = pipeline_state["view_unblurred_video"]
        curr_target = pipeline_state["unblur_target"]

    target_desc = f"Person #{curr_target}" if curr_target not in ("all", "", "None") else "All Faces"
    status_text = f"Live Video Stream Unblurred for: {target_desc}" if active else "Live Video Stream Masked"
    add_log(f"Authorized Video Stream Mode: {status_text}", "INFO")

    return jsonify({
        "status": "ok",
        "view_unblurred_video": active,
        "unblur_target": curr_target,
        "message": status_text
    })


@app.route("/api/crypto/lock", methods=["POST"])
def api_crypto_lock():
    with state_lock:
        pipeline_state["authorized_decryption_unlocked"] = False
        pipeline_state["view_unblurred_video"] = False
        pipeline_state["unblur_target"] = "all"
    add_log("Authorized decryption session locked. Unblurred stream revoked.", "INFO")
    return jsonify({"status": "ok", "unlocked": False, "view_unblurred_video": False, "unblur_target": "all"})


@app.route("/api/configure", methods=["POST"])
def api_configure():
    data = request.get_json() or {}
    log_actions = []

    with state_lock:
        if "input_source" in data:
            new_src = str(data["input_source"]).lower()
            if new_src in ("file", "webcam"):
                pipeline_state["input_source"] = new_src
                log_actions.append((f"Input source switched to: {new_src.upper()}", "INFO"))

        if "input_file" in data:
            target_file = str(data["input_file"]).strip()
            full_path = os.path.join(PROJECT_ROOT, target_file)
            if os.path.exists(full_path):
                pipeline_state["input_file"] = target_file
                pipeline_state["input_source"] = "file"
                log_actions.append((f"Video file switched to: {os.path.basename(target_file)}", "INFO"))
            else:
                log_actions.append((f"Target file not found: {target_file}", "WARN"))

        if "device" in data:
            new_dev = str(data["device"]).upper()
            if new_dev in ("GPU", "CPU"):
                pipeline_state["device"] = new_dev
                log_actions.append((f"Target hardware switched to: {new_dev}", "INFO"))

        if "policy" in data:
            new_policy = str(data["policy"]).upper()
            pipeline_state["policy"] = new_policy
            pol_info = policy_config_data.get("policies", {}).get(new_policy, {})
            if not pol_info.get("enabled", True):
                log_actions.append((f"Policy set to {new_policy} (Cryptographic staging mode)", "WARN"))
            else:
                log_actions.append((f"Policy set to {new_policy}: {pol_info.get('description', '')}", "INFO"))

        if "show_labels" in data:
            pipeline_state["show_labels"] = bool(data["show_labels"])
            state_str = "ENABLED" if pipeline_state["show_labels"] else "DISABLED"
            log_actions.append((f"Privacy overlay labels: {state_str}", "INFO"))

        current_state = dict(pipeline_state)

    for msg, lvl in log_actions:
        add_log(msg, lvl)

    return jsonify({"status": "ok", "state": current_state})


@app.route("/api/videos", methods=["GET"])
def api_videos():
    videos = []
    # Default sample video if available
    default_sample = os.path.join(PROJECT_ROOT, "video.mp4")
    if os.path.exists(default_sample):
        videos.append({"name": "video.mp4 (Default Sample)", "path": "video.mp4"})

    # Check for uploaded videos
    if os.path.exists(UPLOAD_FOLDER):
        for fname in sorted(os.listdir(UPLOAD_FOLDER), reverse=True):
            if allowed_file(fname):
                rel = os.path.relpath(os.path.join(UPLOAD_FOLDER, fname), PROJECT_ROOT).replace("\\", "/")
                disp_name = fname.split("_", 1)[1] if "_" in fname else fname
                videos.append({"name": f"{disp_name} (Uploaded)", "path": rel})

    with state_lock:
        active_file = pipeline_state["input_file"]
        active_source = pipeline_state["input_source"]

    return jsonify({"videos": videos, "active_file": active_file, "input_source": active_source})


@app.route("/api/upload", methods=["POST"])
def api_upload():
    if "video" not in request.files:
        return jsonify({"error": "No video file provided"}), 400

    file = request.files["video"]
    if not file or file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": "Unsupported video format. Allowed formats: MP4, AVI, MOV, MKV, WEBM."}), 400

    clean_name = secure_filename(file.filename)
    if not clean_name:
        clean_name = f"video_{int(time.time())}.mp4"

    timestamp = int(time.time())
    saved_filename = f"{timestamp}_{clean_name}"
    save_path = os.path.join(UPLOAD_FOLDER, saved_filename)

    try:
        file.save(save_path)
    except Exception as e:
        return jsonify({"error": f"Failed to save file: {str(e)}"}), 500

    rel_path = os.path.relpath(save_path, PROJECT_ROOT).replace("\\", "/")
    file_size_mb = os.path.getsize(save_path) / (1024 * 1024)

    with state_lock:
        pipeline_state["input_source"] = "file"
        pipeline_state["input_file"] = rel_path
        pipeline_state["status"] = "RUNNING"
        cur_state = dict(pipeline_state)

    add_log(f"Custom video uploaded: {clean_name} ({file_size_mb:.1f} MB). Now streaming.", "INFO")

    return jsonify({
        "status": "ok",
        "filename": clean_name,
        "filepath": rel_path,
        "size_mb": round(file_size_mb, 2),
        "state": cur_state
    })


@app.route("/api/start", methods=["POST"])
def api_start():
    with state_lock:
        pipeline_state["status"] = "RUNNING"
        pipeline_state["start_time"] = time.time()
        cur_input = pipeline_state["input_source"]
        cur_dev = pipeline_state["device"]
        cur_pol = pipeline_state["policy"]
        cur_state = dict(pipeline_state)

    add_log(f"Pipeline started [Source: {cur_input.upper()}, Device: {cur_dev}, Policy: {cur_pol}]", "INFO")
    return jsonify({"status": "started", "state": cur_state})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    with state_lock:
        pipeline_state["status"] = "STOPPED"
        frames = pipeline_state["frames_processed"]
        pipeline_state["fps"] = 0.0
        pipeline_state["latency_ms"] = 0.0
        cur_state = dict(pipeline_state)

    add_log(f"Pipeline paused. Total frames redacted: {frames}", "INFO")
    return jsonify({"status": "stopped", "state": cur_state})


@app.route("/api/analytics/config", methods=["GET"])
def api_analytics_config_get():
    if not analytics_engine:
        return jsonify({"error": "Analytics engine not initialized"}), 500
    
    zone_enabled = analytics_engine.restricted_zones[0].get("enabled", True) if analytics_engine.restricted_zones else False
    tripwire_enabled = analytics_engine.virtual_tripwires[0].get("enabled", True) if analytics_engine.virtual_tripwires else False
    
    return jsonify({
        "status": "ok",
        "zone_enabled": zone_enabled,
        "tripwire_enabled": tripwire_enabled,
        "zones_count": len(analytics_engine.restricted_zones),
        "restricted_zones": analytics_engine.restricted_zones,
        "virtual_tripwires": analytics_engine.virtual_tripwires
    })


@app.route("/api/analytics/config", methods=["POST"])
def api_analytics_config_post():
    if not analytics_engine:
        return jsonify({"error": "Analytics engine not initialized"}), 500
    
    data = request.get_json(force=True, silent=True) or {}
    
    if data.get("reset_defaults"):
        analytics_engine.reset_analytics_defaults()
        add_log("Analytics reset: Restricted zone and tripwire restored to defaults.", "INFO")
        return jsonify({
            "status": "ok",
            "message": "Defaults restored",
            "zones_count": len(analytics_engine.restricted_zones),
            "zone_enabled": True,
            "tripwire_enabled": True
        })
    
    if "zone_enabled" in data:
        en = bool(data["zone_enabled"])
        analytics_engine.set_zone_enabled(en)
        status_txt = "ENABLED" if en else "DISABLED"
        add_log(f"Restricted Zone: {status_txt}", "INFO")
        
    if "tripwire_enabled" in data:
        en = bool(data["tripwire_enabled"])
        analytics_engine.set_tripwire_enabled(en)
        status_txt = "ENABLED" if en else "DISABLED"
        add_log(f"Virtual Tripwire: {status_txt}", "INFO")

    if data.get("add_zone") and "zone_polygon" in data and isinstance(data["zone_polygon"], list) and len(data["zone_polygon"]) >= 3:
        poly = [[int(pt[0]), int(pt[1])] for pt in data["zone_polygon"]]
        zname = data.get("zone_name")
        new_z = analytics_engine.add_zone(poly, name=zname)
        add_log(f"New Restricted Zone added: {new_z.get('name')} (Total: {len(analytics_engine.restricted_zones)}).", "INFO")
    elif "zone_polygon" in data and isinstance(data["zone_polygon"], list) and len(data["zone_polygon"]) >= 3:
        poly = [[int(pt[0]), int(pt[1])] for pt in data["zone_polygon"]]
        z_idx = int(data.get("zone_index", 0))
        analytics_engine.update_zone_polygon(poly, index=z_idx)
        add_log(f"Restricted Zone #{z_idx + 1} updated ({len(poly)} points).", "INFO")

    if "tripwire_p1" in data and "tripwire_p2" in data:
        p1 = [int(data["tripwire_p1"][0]), int(data["tripwire_p1"][1])]
        p2 = [int(data["tripwire_p2"][0]), int(data["tripwire_p2"][1])]
        analytics_engine.update_tripwire(p1, p2)
        add_log(f"Custom Virtual Tripwire updated: ({p1[0]},{p1[1]}) -> ({p2[0]},{p2[1]}).", "INFO")

    zone_enabled = analytics_engine.restricted_zones[0].get("enabled", True) if analytics_engine.restricted_zones else False
    tripwire_enabled = analytics_engine.virtual_tripwires[0].get("enabled", True) if analytics_engine.virtual_tripwires else False

    return jsonify({
        "status": "ok",
        "zone_enabled": zone_enabled,
        "tripwire_enabled": tripwire_enabled,
        "zones_count": len(analytics_engine.restricted_zones),
        "restricted_zones": analytics_engine.restricted_zones,
        "virtual_tripwires": analytics_engine.virtual_tripwires
    })


if __name__ == "__main__":
    from waitress import serve
    print("\n========================================================")
    print("  Zero-Trust Edge Video - Royal Contrast Dark UI")
    print("  Engine: Intel OpenVINO 2026.4 Accelerated Runtime")
    print("  Server: Waitress Multi-Threaded WSGI Server")
    print("  Dashboard URL: http://127.0.0.1:5000")
    print("========================================================\n")
    serve(app, host="127.0.0.1", port=5000, threads=12)
