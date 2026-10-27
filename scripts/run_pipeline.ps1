param(
    [string]$Policy = "STANDARD",
    [string]$InputType = "file", # "file" or "webcam"
    [string]$InputVideo = "",
    [string]$WebcamDevice = "0", # Optional specific device-path or device-index
    [int]$WebcamWidth = 1280,
    [int]$WebcamHeight = 720,
    [int]$WebcamFps = 30,
    [int]$NumBuffers = -1, # -1 for unlimited stream, or positive integer for bounded testing
    [string]$Device = "GPU", # "GPU" or "CPU"
    [string]$OutputType = "display", # "display", "file", "fps", "json"
    [string]$OutputFile = "",
    [int]$InferenceInterval = 0, # If 0, resolved from Policy
    [string]$TrackingType = "",  # If empty, resolved from Policy
    [double]$Threshold = 0.0,    # If 0.0, resolved from Policy
    [switch]$ShowLabels = $false,
    [switch]$BenchmarkSink = $false,
    [string]$ConfigPath = "",
    [string]$Backend = "auto",   # "auto", "dlstreamer", "openvino"
    [switch]$NoAnalytics = $false,
    [string]$AnalyticsConfig = ""
)

$ErrorActionPreference = "Stop"

# --- Resolve Project Root & Dynamic Defaults ---
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $ScriptDir) { $ScriptDir = $PSScriptRoot }
if (-not $ScriptDir) { $ScriptDir = (Get-Location).Path }
$ProjectRoot = (Resolve-Path "$ScriptDir\..").Path
if (-not $ProjectRoot) { $ProjectRoot = (Get-Location).Path }

if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
    $ConfigPath = Join-Path $ProjectRoot "config\privacy_policies.json"
}
if ([string]::IsNullOrWhiteSpace($InputVideo)) {
    $InputVideo = Join-Path $ProjectRoot "video.mp4"
}
if ([string]::IsNullOrWhiteSpace($OutputFile)) {
    $OutputFile = Join-Path $ProjectRoot "output_anonymized.mp4"
}
$ModelXml = Join-Path $ProjectRoot "yolov12n-face_openvino_model\yolov12n-face.xml"

# --- Policy Resolution Layer ---
$ResolvedPolicy = $Policy.ToUpper()
if (-not (Test-Path $ConfigPath)) {
    Write-Error "Configuration file not found at: $ConfigPath"
}

$PolicyJson = (Get-Content -Path $ConfigPath -Raw) | ConvertFrom-Json

if (-not $PolicyJson.policies.PSObject.Properties[$ResolvedPolicy]) {
    $Available = ($PolicyJson.policies.PSObject.Properties.Name) -join ", "
    Write-Error "Policy '$Policy' not found in configuration ($ConfigPath). Available: $Available"
}

$PolicyData = $PolicyJson.policies.$ResolvedPolicy

# Map policy settings with parameter override capability
$EffectiveInterval = if ($InferenceInterval -gt 0) { $InferenceInterval } else { [int]$PolicyData.inference_interval }
$EffectiveTracking = if ($TrackingType -ne "") { $TrackingType } else { [string]$PolicyData.tracking_type }
$EffectiveThreshold = if ($Threshold -gt 0.0) { $Threshold } else { [double]$PolicyData.confidence }
$AnonymizationMethod = [string]$PolicyData.anonymization
$RoiPadding = [string]$PolicyData.roi_padding
$PolicyEnabled = [bool]$PolicyData.enabled

# Check for un-implemented / staged policies
$MethodMeta = $PolicyJson.anonymization_methods.PSObject.Properties[$AnonymizationMethod]
$IsSupportedInPipeline = if ($MethodMeta) { [bool]$MethodMeta.Value.supported_in_pipeline } else { $false }

if (-not $IsSupportedInPipeline) {
    if ($AnonymizationMethod -eq "encryption") {
        Write-Warning "Policy '$ResolvedPolicy' specifies '$AnonymizationMethod'. The encryption anonymization backend is staged and scheduled for a subsequent phase. Running under standard anonymization verification."
    } else {
        Write-Error "Anonymization method '$AnonymizationMethod' in policy '$ResolvedPolicy' is not currently supported in pipeline."
    }
}

# Resolve paths
$InputPath = $InputVideo -replace '\\', '/'
$OutputPath = $OutputFile -replace '\\', '/'
$ModelPath = $ModelXml -replace '\\', '/'

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "Intel DL Streamer / OpenVINO Face Anonymization Pipeline" -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "Input Mode          : $($InputType.ToUpper())" -ForegroundColor Yellow
if ($InputType.ToLower() -eq "webcam") {
    Write-Host "Webcam Device       : $WebcamDevice"
    Write-Host "Target Format       : ${WebcamWidth}x${WebcamHeight} @ ${WebcamFps}fps"
} else {
    Write-Host "Input Source        : $InputPath"
}
Write-Host "Active Policy       : $ResolvedPolicy ($($PolicyData.description))" -ForegroundColor Magenta
Write-Host "Anonymization       : $AnonymizationMethod (Padding: $RoiPadding)"
Write-Host "Model               : $ModelPath"
Write-Host "Target Device       : $Device"
Write-Host "Inference Interval  : $EffectiveInterval"
Write-Host "Tracking Type       : $EffectiveTracking"
Write-Host "Confidence Thresh   : $EffectiveThreshold"
Write-Host "Output Mode         : $OutputType"
Write-Host "==========================================================" -ForegroundColor Cyan

# Check for GStreamer gst-launch-1.0
$GstCmd = Get-Command "gst-launch-1.0" -ErrorAction SilentlyContinue

$UseOpenVinoEngine = $false
if ($Backend -eq "openvino") {
    $UseOpenVinoEngine = $true
} elseif ($Backend -eq "dlstreamer") {
    if (-not $GstCmd) {
        Write-Error "Backend 'dlstreamer' was specified, but 'gst-launch-1.0' was not found in PATH."
    }
} else {
    # auto mode
    if (-not $GstCmd) {
        $UseOpenVinoEngine = $true
        Write-Host "Note: GStreamer 'gst-launch-1.0' not detected in system PATH." -ForegroundColor Yellow
        Write-Host "Automatically routing execution through Intel OpenVINO runtime engine..." -ForegroundColor Green
    }
}

if ($UseOpenVinoEngine) {
    $PyRunner = Join-Path $ScriptDir "run_pipeline.py"
    $LabelsFlag = if ($ShowLabels) { "--show-labels" } else { "" }
    $PyArgs = @(
        "`"$PyRunner`"",
        "--policy `"$ResolvedPolicy`"",
        "--input-type `"$InputType`"",
        "--input-video `"$InputPath`"",
        "--webcam-device `"$WebcamDevice`"",
        "--webcam-width $WebcamWidth",
        "--webcam-height $WebcamHeight",
        "--webcam-fps $WebcamFps",
        "--num-buffers $NumBuffers",
        "--device `"$Device`"",
        "--output-type `"$OutputType`"",
        "--output-file `"$OutputPath`"",
        "--inference-interval $EffectiveInterval",
        "--tracking-type `"$EffectiveTracking`"",
        "--threshold $EffectiveThreshold",
        "--config-path `"$ConfigPath`"",
        "--model-path `"$ModelPath`""
    )
    if ($ShowLabels) { $PyArgs += "--show-labels" }
    if ($NoAnalytics) { $PyArgs += "--no-analytics" }
    if (-not [string]::IsNullOrWhiteSpace($AnalyticsConfig)) { $PyArgs += "--analytics-config `"$AnalyticsConfig`"" }

    $PyCommand = "python $($PyArgs -join ' ')"
    Write-Host "`nExecuting OpenVINO Pipeline Engine:" -ForegroundColor Yellow
    Write-Host $PyCommand -ForegroundColor Gray
    Write-Host ""
    Invoke-Expression $PyCommand
    exit $LASTEXITCODE
}

# --- DL Streamer GStreamer Pipeline Construction ---
# Source element configuration
$NormalizedInputType = $InputType.ToLower()
if ($NormalizedInputType -eq "webcam") {
    $NumBufStr = if ($NumBuffers -gt 0) { "num-buffers=$NumBuffers" } else { "" }
    $DevPathStr = if ($WebcamDevice -ne "") { "device-path=`"$WebcamDevice`"" } else { "" }
    $SOURCE = "mfvideosrc $NumBufStr $DevPathStr ! video/x-raw,width=$WebcamWidth,height=$WebcamHeight,framerate=$WebcamFps/1"
} elseif ($NormalizedInputType -eq "file") {
    if ($InputPath -match "://") {
        $SOURCE = "urisourcebin uri=$InputPath"
    } else {
        $SOURCE = "filesrc location=`"$InputPath`""
    }
} else {
    Write-Error "Invalid InputType: '$InputType'. Supported values: 'file', 'webcam'"
}

# Determine pre-processing and pipeline structure based on device and input source
if ($Device -eq "GPU") {
    $PREPROC = "pre-process-backend=d3d11"
    $TRACK_DEVICE = "device=CPU"
    $WATERMARK_PRE = "! d3d11convert"
    $WATERMARK_DEV = "device=CPU"
    $DECODE_STAGE = "decodebin3 ! d3d11upload ! `"video/x-raw(memory:D3D11Memory)`""
} else {
    $PREPROC = "pre-process-backend=opencv"
    $TRACK_DEVICE = "device=CPU"
    $WATERMARK_PRE = ""
    $WATERMARK_DEV = "device=CPU"
    $DECODE_STAGE = "decodebin3"
}

# Configure blur and display settings
$LABEL_VAL = if ($ShowLabels) { "true" } else { "false" }
$DISPL_CFG = "displ-cfg=`"enable-blur=true,show-blur-roi=face,show-labels=$LABEL_VAL`""

# Build Sink Element
switch ($OutputType) {
    "display" {
        if ($Device -eq "GPU") {
            $SINK = "$WATERMARK_PRE ! gvawatermark $WATERMARK_DEV $DISPL_CFG ! d3d11convert ! gvafpscounter ! d3d11videosink sync=false"
        } else {
            $SINK = "$WATERMARK_PRE ! gvawatermark $WATERMARK_DEV $DISPL_CFG ! videoconvert ! gvafpscounter ! autovideosink sync=false"
        }
    }
    "file" {
        $SINK = "$WATERMARK_PRE ! gvawatermark $WATERMARK_DEV $DISPL_CFG ! videoconvert ! gvafpscounter ! mfh264enc ! h264parse ! mp4mux ! filesink location=`"$OutputPath`""
    }
    "fps" {
        $SINK = "$WATERMARK_PRE ! gvawatermark $WATERMARK_DEV $DISPL_CFG ! gvafpscounter ! fakesink async=false"
    }
    "json" {
        $JsonOut = [System.IO.Path]::ChangeExtension($OutputPath, ".json")
        $SINK = "$WATERMARK_PRE ! gvametaconvert add-tensor-data=false ! gvametapublish file-format=json-lines file-path=`"$JsonOut`" ! fakesink async=false"
    }
    default {
        Write-Error "Invalid OutputType: $OutputType. Supported: display, file, fps, json"
    }
}

$PIPELINE = "gst-launch-1.0 $SOURCE ! $DECODE_STAGE ! gvadetect model=`"$ModelPath`" device=$Device $PREPROC inference-interval=$EffectiveInterval threshold=$EffectiveThreshold ! gvatrack tracking-type=$EffectiveTracking $TRACK_DEVICE $SINK"

Write-Host "`nExecuting DL Streamer Pipeline:" -ForegroundColor Yellow
Write-Host $PIPELINE -ForegroundColor Gray
Write-Host ""

Invoke-Expression $PIPELINE
