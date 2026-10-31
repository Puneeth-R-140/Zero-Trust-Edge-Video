"""
Intel OpenVINO & DL Streamer Face Anonymization Pipeline Runner.

Provides edge-accelerated, privacy-preserving face anonymization using
Intel OpenVINO runtime with GPU / CPU execution, adhering to privacy policies
(STANDARD, HIGH_PRIVACY, ZERO_TRUST).
"""

import os
import sys
import time
import json
import argparse
from typing import List, Tuple, Dict, Any, Optional

import cv2
import numpy as np

# Add scripts directory to path to import policy_manager
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from policy_manager import PolicyManager, PolicyConfig
from analytics_engine import AnalyticsEngine


class SimpleTracker:
    """
    Lightweight short-term bounding-box tracker for inference-interval frames,
    emulating DL Streamer's short-term-imageless tracking element (gvatrack).
    """
    def __init__(self, max_missed: int = 5):
        self.tracks: Dict[int, Dict[str, Any]] = {}
        self.next_id = 0
        self.max_missed = max_missed

    def update(self, detected_boxes: List[Tuple[int, int, int, int, float]]) -> List[Dict[str, Any]]:
        if not detected_boxes and not self.tracks:
            return []

        # Age existing tracks
        for tid in list(self.tracks.keys()):
            self.tracks[tid]["missed"] += 1
            if self.tracks[tid]["missed"] > self.max_missed:
                del self.tracks[tid]

        # Match detections to existing tracks using IoU
        matched = set()
        for x1, y1, x2, y2, conf in detected_boxes:
            best_iou = 0.0
            best_id = None
            for tid, tdata in self.tracks.items():
                if tid in matched:
                    continue
                tx1, ty1, tx2, ty2 = tdata["box"]
                iou = self._iou((x1, y1, x2, y2), (tx1, ty1, tx2, ty2))
                if iou > best_iou:
                    best_iou = iou
                    best_id = tid

            if best_id is not None and best_iou > 0.3:
                self.tracks[best_id]["box"] = (x1, y1, x2, y2)
                self.tracks[best_id]["conf"] = conf
                self.tracks[best_id]["missed"] = 0
                matched.add(best_id)
            else:
                self.tracks[self.next_id] = {
                    "box": (x1, y1, x2, y2),
                    "conf": conf,
                    "missed": 0,
                    "id": self.next_id
                }
                matched.add(self.next_id)
                self.next_id += 1

        return [
            {"id": tid, "box": data["box"], "conf": data["conf"]}
            for tid, data in self.tracks.items()
            if data["missed"] == 0
        ]

    def get_current(self) -> List[Dict[str, Any]]:
        return [
            {"id": tid, "box": data["box"], "conf": data["conf"]}
            for tid, data in self.tracks.items()
            if data["missed"] <= 2
        ]

    @staticmethod
    def _iou(boxA: Tuple[int, int, int, int], boxB: Tuple[int, int, int, int]) -> float:
        xA = max(boxA[0], boxB[0])
        yA = max(boxA[1], boxB[1])
        xB = min(boxA[2], boxB[2])
        yB = min(boxA[3], boxB[3])
        interArea = max(0, xB - xA) * max(0, yB - yA)
        boxAArea = max(1, (boxA[2] - boxA[0]) * (boxA[3] - boxA[1]))
        boxBArea = max(1, (boxB[2] - boxB[0]) * (boxB[3] - boxB[1]))
        return interArea / float(boxAArea + boxBArea - interArea)


def apply_roi_padding(x1: int, y1: int, x2: int, y2: int, padding_factor: float, max_w: int, max_h: int) -> Tuple[int, int, int, int]:
    """Applies contextual padding to region of interest (ROI) bounding box."""
    if padding_factor <= 0.0:
        return x1, y1, x2, y2
    
    bw = x2 - x1
    bh = y2 - y1
    pad_x = int(bw * padding_factor)
    pad_y = int(bh * padding_factor)

    nx1 = max(0, x1 - pad_x)
    ny1 = max(0, y1 - pad_y)
    nx2 = min(max_w - 1, x2 + pad_x)
    ny2 = min(max_h - 1, y2 + pad_y)
    return nx1, ny1, nx2, ny2


def anonymize_roi(frame: np.ndarray, x1: int, y1: int, x2: int, y2: int, method: str) -> None:
    """Anonymizes the specified bounding box in-place via blur or pixelation."""
    if x2 <= x1 or y2 <= y1:
        return
    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        return

    if method == "blur":
        # Dynamic kernel size proportional to face dimensions
        kw = max(15, (roi.shape[1] // 3) | 1)
        kh = max(15, (roi.shape[0] // 3) | 1)
        if kw % 2 == 0: kw += 1
        if kh % 2 == 0: kh += 1
        blurred = cv2.GaussianBlur(roi, (kw, kh), sigmaX=30)
        frame[y1:y2, x1:x2] = blurred

    elif method == "strong_blur":
        # Extra heavy blur for High Privacy profile
        kw = max(29, (roi.shape[1] // 2) | 1)
        kh = max(29, (roi.shape[0] // 2) | 1)
        if kw % 2 == 0: kw += 1
        if kh % 2 == 0: kh += 1
        blurred = cv2.GaussianBlur(roi, (kw, kh), sigmaX=60)
        frame[y1:y2, x1:x2] = blurred

    elif method == "pixelate":
        # Mosaic block size
        blocks = 8
        rh, rw = roi.shape[:2]
        small_w = max(1, rw // blocks)
        small_h = max(1, rh // blocks)
        temp = cv2.resize(roi, (small_w, small_h), interpolation=cv2.INTER_LINEAR)
        pixelated = cv2.resize(temp, (rw, rh), interpolation=cv2.INTER_NEAREST)
        frame[y1:y2, x1:x2] = pixelated

    else:
        # Default fallback blur
        kw = max(19, (roi.shape[1] // 3) | 1)
        if kw % 2 == 0: kw += 1
        blurred = cv2.GaussianBlur(roi, (kw, kw), sigmaX=30)
        frame[y1:y2, x1:x2] = blurred


def run_pipeline(
    policy_name: str = "STANDARD",
    input_type: str = "file",
    input_video: str = "video.mp4",
    webcam_device: str = "0",
    webcam_width: int = 1280,
    webcam_height: int = 720,
    webcam_fps: int = 30,
    num_buffers: int = -1,
    device: str = "GPU",
    output_type: str = "display",
    output_file: str = "output_anonymized.mp4",
    inference_interval: int = 0,
    tracking_type: str = "",
    threshold: float = 0.0,
    show_labels: bool = False,
    config_path: Optional[str] = None,
    model_path: Optional[str] = None,
    headless: bool = False,
    enable_analytics: bool = True,
    analytics_config_path: Optional[str] = None,
    show_analytics_overlay: bool = True
) -> Dict[str, Any]:
    
    # 1. Load Policy
    pm = PolicyManager(config_path)
    policy = pm.get_policy(policy_name)
    
    effective_interval = inference_interval if inference_interval > 0 else policy.inference_interval
    effective_threshold = threshold if threshold > 0.0 else policy.confidence
    effective_tracking = tracking_type if tracking_type else policy.tracking_type
    anonymization_method = policy.anonymization
    roi_padding_factor = policy.padding_factor

    print("==========================================================")
    print("Intel OpenVINO Face Anonymization Pipeline")
    print("==========================================================")
    print(f"Input Mode          : {input_type.upper()}")
    if input_type.lower() == "webcam":
        print(f"Webcam Device       : {webcam_device}")
        print(f"Target Format       : {webcam_width}x{webcam_height} @ {webcam_fps}fps")
    else:
        print(f"Input Source        : {input_video}")
    print(f"Active Policy       : {policy.name} ({policy.description})")
    print(f"Anonymization       : {anonymization_method} (Padding: {policy.roi_padding})")
    print(f"Target Device       : {device}")
    print(f"Inference Interval  : {effective_interval}")
    print(f"Tracking Type       : {effective_tracking}")
    print(f"Confidence Thresh   : {effective_threshold}")
    print(f"Output Mode         : {output_type}")
    print(f"Privacy Analytics   : {'ENABLED' if enable_analytics else 'DISABLED'}")
    print("==========================================================")

    # 2. Resolve Models
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not model_path:
        cand_ov = os.path.join(base_dir, "yolov12n-face_openvino_model")
        cand_pt = os.path.join(base_dir, "yolov12n-face.pt")
        cand_yolo11 = os.path.join(base_dir, "yolo11n_openvino_model")
        if os.path.exists(cand_ov):
            model_path = cand_ov
        elif os.path.exists(cand_pt):
            model_path = cand_pt
        elif os.path.exists(cand_yolo11):
            model_path = cand_yolo11
        else:
            model_path = "yolo11n.pt"

    # Normalize if path points directly to an .xml file inside the model folder
    if model_path and model_path.endswith(".xml") and os.path.isdir(os.path.dirname(model_path)):
        model_path = os.path.dirname(model_path)

    print(f"Loading Face Model  : {model_path}")
    from ultralytics import YOLO

    # Check device availability for OpenVINO / PyTorch
    target_device = device.lower()
    try:
        model = YOLO(model_path, task="detect")
    except Exception as e:
        print(f"Note loading model: {e}, falling back to default YOLO model")
        model = YOLO("yolo11n.pt")

    # Person Model for Whole-Person Tracking & Advanced Privacy Analytics
    person_model = None
    analytics_engine = None
    person_tracker = None
    if enable_analytics:
        if not analytics_config_path:
            analytics_config_path = os.path.join(base_dir, "config", "analytics_config.json")
        analytics_engine = AnalyticsEngine(config_path=analytics_config_path)
        person_tracker = SimpleTracker()

        person_cand = os.path.join(base_dir, "yolo11n_openvino_model")
        if not os.path.exists(person_cand):
            person_cand = os.path.join(base_dir, "yolo11n.pt")
        if os.path.exists(person_cand):
            try:
                print(f"Loading Person Model: {person_cand}")
                person_model = YOLO(person_cand, task="detect")
            except Exception as e:
                print(f"Note loading person model: {e}")

    # 3. Setup Video Source
    if input_type.lower() == "webcam":
        try:
            cam_idx = int(webcam_device)
        except ValueError:
            cam_idx = 0
        cap = cv2.VideoCapture(cam_idx, cv2.CAP_DSHOW)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, webcam_width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, webcam_height)
        cap.set(cv2.CAP_PROP_FPS, webcam_fps)
    else:
        if not os.path.exists(input_video):
            # Check relative to base_dir
            alt_path = os.path.join(base_dir, input_video)
            if os.path.exists(alt_path):
                input_video = alt_path
            else:
                raise FileNotFoundError(f"Input video not found: {input_video}")
        cap = cv2.VideoCapture(input_video)

    if not cap.isOpened():
        raise RuntimeError(f"Failed to open video source ({input_type}: {input_video or webcam_device})")

    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or webcam_width
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or webcam_height
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    # 4. Setup Output Sink
    writer = None
    json_records = []
    if output_type == "file":
        out_dir = os.path.dirname(os.path.abspath(output_file))
        if out_dir: os.makedirs(out_dir, exist_ok=True)
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        writer = cv2.VideoWriter(output_file, fourcc, src_fps, (src_w, src_h))
        print(f"Output Writer       : {output_file} ({src_w}x{src_h} @ {src_fps}fps)")

    tracker = SimpleTracker()
    frame_idx = 0
    start_time = time.time()
    last_fps_time = time.time()
    fps_display = 0.0

    print("\nStarting Face Anonymization Pipeline processing...")
    if output_type == "display" and not headless:
        print("Press 'q' or 'ESC' on the video window to stop.")

    try:
        while True:
            if num_buffers > 0 and frame_idx >= num_buffers:
                break

            ret, frame = cap.read()
            if not ret or frame is None:
                break

            frame_idx += 1
            h, w = frame.shape[:2]
            now = time.time()

            # Inference interval: only run model every effective_interval frames
            ov_dev = "intel:gpu" if str(device).upper() == "GPU" else "intel:cpu"
            if (frame_idx % effective_interval == 1) or (effective_interval <= 1):
                # Run detection with OpenVINO acceleration
                results = model.predict(frame, conf=effective_threshold, device=ov_dev, imgsz=640, verbose=False)
                detections = []
                for box in results[0].boxes:
                    bx1, by1, bx2, by2 = map(int, box.xyxy[0].tolist())
                    bconf = float(box.conf[0])
                    detections.append((bx1, by1, bx2, by2, bconf))
                
                active_tracks = tracker.update(detections)
            else:
                # Use tracking positions
                active_tracks = tracker.get_current()

            # Apply Anonymization & ROI Padding
            frame_detections_json = []
            for track in active_tracks:
                bx1, by1, bx2, by2 = track["box"]
                conf = track["conf"]
                track_id = track.get("id", 0)

                # Expand with ROI padding
                px1, py1, px2, py2 = apply_roi_padding(bx1, by1, bx2, by2, roi_padding_factor, w, h)
                anonymize_roi(frame, px1, py1, px2, py2, anonymization_method)

                if show_labels:
                    lbl = f"ID:{track_id} {conf:.2f}"
                    cv2.rectangle(frame, (px1, py1), (px2, py2), (0, 255, 0), 1)
                    cv2.putText(frame, lbl, (px1, max(15, py1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1)

                if output_type == "json":
                    frame_detections_json.append({
                        "id": track_id,
                        "roi": [px1, py1, px2, py2],
                        "confidence": round(conf, 3)
                    })

            # 2. Person Detection & Advanced Privacy-Preserving Analytics
            frame_events = []
            if enable_analytics and analytics_engine is not None:
                active_person_tracks = []
                if person_model is not None:
                    person_interval = max(3, effective_interval * 2)
                    if (frame_idx % person_interval == 0):
                        p_results = person_model.predict(frame, classes=[0], conf=0.35, device=ov_dev, imgsz=640, verbose=False)
                        p_detections = []
                        for pbox in p_results[0].boxes:
                            px1, py1, px2, py2 = map(int, pbox.xyxy[0].tolist())
                            p_detections.append((px1, py1, px2, py2, float(pbox.conf[0])))
                        active_person_tracks = person_tracker.update(p_detections)
                    else:
                        active_person_tracks = person_tracker.get_current()
                else:
                    active_person_tracks = active_tracks

                frame_events = analytics_engine.update(active_person_tracks, timestamp=now)
                for ev in frame_events:
                    print(f"[ANALYTICS ALERT] {ev['event_type']} -> Zone/Line: {ev.get('zone_id', ev.get('tripwire_id', 'N/A'))} | Crowd: {ev['crowd_level']} ({ev['crowd_count']}) | Occupancy: {ev['occupancy']}")

                if show_analytics_overlay:
                    analytics_engine.draw_overlay(frame)

            # Calculate FPS
            if now - last_fps_time >= 0.5:
                fps_display = frame_idx / max(0.001, (now - start_time))
                last_fps_time = now

            # Overlay stats header
            status_text = f"Policy: {policy.name} | FPS: {fps_display:.1f} | Method: {anonymization_method} | Interval: {effective_interval}"
            cv2.putText(frame, status_text, (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

            # Output handling
            if output_type == "display" and not headless:
                cv2.imshow("Intel DL Streamer / OpenVINO Face Anonymization", frame)
                key = cv2.waitKey(1) & 0xFF
                if key == 27 or key == ord('q'):
                    print("\nUser requested exit.")
                    break
            elif output_type == "file" and writer is not None:
                writer.write(frame)
            elif output_type == "json":
                rec = {
                    "frame": frame_idx,
                    "timestamp": round(now - start_time, 4),
                    "faces": frame_detections_json
                }
                if analytics_engine:
                    rec["analytics"] = analytics_engine.get_summary_state()
                    if frame_events:
                        rec["events"] = frame_events
                json_records.append(rec)

            if frame_idx % 30 == 0:
                elapsed = now - start_time
                cur_fps = frame_idx / max(0.001, elapsed)
                print(f"[Pipeline] Processed {frame_idx} frames | Overall FPS: {cur_fps:.2f}")

    finally:
        cap.release()
        if writer is not None:
            writer.release()
        if output_type == "display" and not headless:
            cv2.destroyAllWindows()

    total_time = max(0.001, time.time() - start_time)
    avg_fps = frame_idx / total_time
    print("==========================================================")
    print(f"Pipeline Completed: {frame_idx} frames processed in {total_time:.2f}s")
    print(f"Average Throughput: {avg_fps:.2f} FPS")
    if analytics_engine:
        summary = analytics_engine.get_summary_state()
        print(f"Analytics Summary : Crowd: {summary['crowd_level']} ({summary['crowd_count']}) | IN: {summary['entries']} | OUT: {summary['exits']} | Occupancy: {summary['occupancy']}")
    print("==========================================================")

    if output_type == "json":
        json_path = os.path.splitext(output_file)[0] + ".json"
        with open(json_path, "w", encoding="utf-8") as f:
            for rec in json_records:
                f.write(json.dumps(rec) + "\n")
        print(f"Wrote JSON detections and analytics to: {json_path}")

    res = {
        "frames": frame_idx,
        "duration_sec": round(total_time, 2),
        "fps": round(avg_fps, 2),
        "policy": policy.name,
        "device": device
    }
    if analytics_engine:
        res["analytics"] = analytics_engine.get_summary_state()
        res["total_events"] = len(analytics_engine.emitted_events)
    return res


def main():
    parser = argparse.ArgumentParser(description="Intel OpenVINO & DL Streamer Face Anonymization Pipeline")
    parser.add_argument("--policy", "-p", default="STANDARD", help="Privacy policy name (STANDARD, HIGH_PRIVACY, ZERO_TRUST)")
    parser.add_argument("--input-type", "-it", default="file", choices=["file", "webcam"], help="Input stream type")
    parser.add_argument("--input-video", "-i", default="video.mp4", help="Path to input video file")
    parser.add_argument("--webcam-device", default="0", help="Webcam device index or path")
    parser.add_argument("--webcam-width", type=int, default=1280)
    parser.add_argument("--webcam-height", type=int, default=720)
    parser.add_argument("--webcam-fps", type=int, default=30)
    parser.add_argument("--num-buffers", "-n", type=int, default=-1, help="Max number of frames to process (-1 for all)")
    parser.add_argument("--device", "-d", default="GPU", help="Target device (GPU, CPU)")
    parser.add_argument("--output-type", "-o", default="display", choices=["display", "file", "fps", "json"], help="Output sink mode")
    parser.add_argument("--output-file", default="output_anonymized.mp4", help="Path to output video or json file")
    parser.add_argument("--inference-interval", type=int, default=0, help="Inference interval override")
    parser.add_argument("--tracking-type", default="", help="Tracking type override")
    parser.add_argument("--threshold", type=float, default=0.0, help="Confidence threshold override")
    parser.add_argument("--show-labels", action="store_true", help="Draw bounding boxes and confidence labels")
    parser.add_argument("--config-path", default=None, help="Path to privacy_policies.json")
    parser.add_argument("--model-path", default=None, help="Path to OpenVINO or YOLO model")
    parser.add_argument("--headless", action="store_true", help="Do not open GUI window (headless mode)")
    parser.add_argument("--no-analytics", action="store_true", help="Disable advanced analytics")
    parser.add_argument("--analytics-config", default=None, help="Path to analytics_config.json")
    parser.add_argument("--no-analytics-overlay", action="store_true", help="Do not draw analytics overlay on frame")

    args = parser.parse_args()

    run_pipeline(
        policy_name=args.policy,
        input_type=args.input_type,
        input_video=args.input_video,
        webcam_device=args.webcam_device,
        webcam_width=args.webcam_width,
        webcam_height=args.webcam_height,
        webcam_fps=args.webcam_fps,
        num_buffers=args.num_buffers,
        device=args.device,
        output_type=args.output_type,
        output_file=args.output_file,
        inference_interval=args.inference_interval,
        tracking_type=args.tracking_type,
        threshold=args.threshold,
        show_labels=args.show_labels,
        config_path=args.config_path,
        model_path=args.model_path,
        headless=args.headless,
        enable_analytics=not args.no_analytics,
        analytics_config_path=args.analytics_config,
        show_analytics_overlay=not args.no_analytics_overlay
    )


if __name__ == "__main__":
    main()
