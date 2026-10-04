# Privacy-Preserving Face Anonymization using Intel DL Streamer

This project implements a high-throughput, edge-accelerated privacy-preserving face anonymization system built on **Intel Deep Learning Streamer (DL Streamer 2026.2.0)**, **OpenVINO 2026.2.0**, and **GStreamer 1.0**.

## Requirements

### Hardware

- Intel CPU
- Intel GPU recommended for hardware acceleration
- Webcam for live-camera mode
- At least 8 GB RAM recommended

### Software

| Component | Version |
|---|---|
| Windows | 10/11 |
| Python | 3.13.14 |
| GStreamer | 1.0 |
| Intel DL Streamer | 2026.2.0 |
| OpenVINO | 2026.2.0 |
| Ultralytics | 8.4.163 |
| PowerShell | 5+ |

The project currently targets **Windows + Intel hardware** because GPU execution uses the Intel GPU/D3D11 path.

## Installation

### 1. Install GStreamer

Install the 64-bit MSVC GStreamer runtime and development packages.

Verify:

```powershell
gst-launch-1.0 --version
```

Verify DL Streamer elements:

```powershell
gst-inspect-1.0 gvadetect
gst-inspect-1.0 gvatrack
gst-inspect-1.0 gvawatermark
gst-inspect-1.0 gvafpscounter
```

All four elements should be available.

### 2. Install Python

Install Python 3.13 and verify:

```powershell
python --version
```

Expected:

```text
Python 3.13.14
```

### 3. Install OpenVINO

```powershell
python -m pip install openvino==2026.2.0
```

Verify:

```powershell
python -c "import openvino; print(openvino.__version__)"
```

Expected:

```text
2026.2.0
```

### 4. Install Ultralytics

```powershell
python -m pip install ultralytics
```

Verify:

```powershell
yolo version
```

The project was developed and tested with:

```text
8.4.163
```

### 5. Obtain the Face Detection Model

The project uses the **YOLOv12n-Face** model.

The model is exported to OpenVINO IR format:

```text
yolov12n-face.xml
yolov12n-face.bin
```

The model files are intentionally excluded from Git because of their size.

After obtaining the model, place the OpenVINO model directory at:

```text
yolov12n-face_openvino_model/
├── yolov12n-face.xml
├── yolov12n-face.bin
└── metadata.yaml
```

### 6. Clone the Project

```powershell
git clone <YOUR-GITHUB-REPOSITORY-URL>
cd ZTE-1
```

### 7. Verify the Project

Run:

```powershell
python scripts/policy_manager.py STANDARD
```

Then verify the GPU pipeline:

```powershell
.\scripts\run_pipeline.ps1 -Policy STANDARD -Device GPU -OutputType display
```

Or use CPU:

```powershell
.\scripts\run_pipeline.ps1 -Policy STANDARD -Device CPU -OutputType display
```

If the video opens and detected faces are anonymized, the installation is complete.