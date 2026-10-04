# Face Anonymization Evaluation & Metrics

## 1. Experimental Setup

- **Host OS**: Microsoft Windows
- **DL Streamer Version**: 2026.2.0
- **Inference Runtime**: OpenVINO 2026.2.0
- **Accelerators Tested**: 11th Gen Intel CPU / Intel Iris Xe Graphics (GPU.0)
- **Face Model**: Ultralytics YOLOv12n-Face OpenVINO IR (`yolov12n-face.xml` / `.bin`)
- **Input Resolution**: 1080p (`video.mp4`)

---

## 2. Quantitative Performance Comparison

| Configuration | Device | Preprocessing | Inference Interval | Tracker | Blur Applied | Measured Overall FPS | Real-Time Factor (vs 30fps) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **CPU Pipeline** | CPU | OpenCV | 2 | `short-term-imageless` | Yes (`gvawatermark`) | **~37.1 FPS** | **1.24x** |
| **GPU Full Anonymize** | GPU | D3D11 | 2 | `short-term-imageless` | Yes (`gvawatermark`) | **~71.8 FPS** | **2.39x** |
| **GPU Inference Only** | GPU | D3D11 | 2 | `short-term-imageless` | None (`fakesink`) | **~120.3 FPS** | **4.01x** |

---

## 3. Privacy Evaluation & Observations

1. **Detection Stability**:
   - `yolov12n-face` demonstrates high confidence recall for frontal and profile faces.
   - Pairing `gvadetect` with `gvatrack` prevents bounding box flickering when inference is skipped on alternating frames (`inference-interval=2`).

2. **Blur Coverage & Strength**:
   - Intel DL Streamer `gvawatermark` applies OpenCV Gaussian Blur dynamically scaled to approximately $1/5$ of the bounding box size (min kernel 7).
   - In 1080p video, faces occupying smaller relative frame areas receive lighter blur kernels than large close-up faces.
   - Expanding the tracking bounding box or padding can be applied to extend edge coverage.
