param(
    [string]$InputVideo = "",
    [int]$NumBuffers = 60
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $ScriptDir) { $ScriptDir = $PSScriptRoot }
if (-not $ScriptDir) { $ScriptDir = (Get-Location).Path }
$ProjectRoot = (Resolve-Path "$ScriptDir\..").Path
if (-not $ProjectRoot) { $ProjectRoot = (Get-Location).Path }

if ([string]::IsNullOrWhiteSpace($InputVideo)) {
    $InputVideo = Join-Path $ProjectRoot "video.mp4"
}
$ModelXml = Join-Path $ProjectRoot "yolov12n-face_openvino_model\yolov12n-face.xml"
$ReportCsv = Join-Path $ProjectRoot "benchmarks\benchmark_results.csv"

$InputPath = $InputVideo -replace '\\', '/'
$Results = @()

$Configurations = @(
    @{ Name = "CPU_DetectOnly"; Device = "CPU"; Preproc = "opencv"; Interval = 1; Track = "none"; Blur = $false },
    @{ Name = "CPU_Detect_Interval2"; Device = "CPU"; Preproc = "opencv"; Interval = 2; Track = "none"; Blur = $false },
    @{ Name = "CPU_Detect_Track_Interval2"; Device = "CPU"; Preproc = "opencv"; Interval = 2; Track = "short-term-imageless"; Blur = $false },
    @{ Name = "CPU_Full_Anonymized"; Device = "CPU"; Preproc = "opencv"; Interval = 2; Track = "short-term-imageless"; Blur = $true },
    @{ Name = "GPU_DetectOnly"; Device = "GPU"; Preproc = "d3d11"; Interval = 1; Track = "none"; Blur = $false },
    @{ Name = "GPU_Detect_Interval2"; Device = "GPU"; Preproc = "d3d11"; Interval = 2; Track = "none"; Blur = $false },
    @{ Name = "GPU_Detect_Track_Interval2"; Device = "GPU"; Preproc = "d3d11"; Interval = 2; Track = "short-term-imageless"; Blur = $false },
    @{ Name = "GPU_Full_Anonymized"; Device = "GPU"; Preproc = "d3d11"; Interval = 2; Track = "short-term-imageless"; Blur = $true }
)

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "DL Streamer / OpenVINO Face Anonymization - Benchmark Suite" -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Cyan

$GstCmd = Get-Command "gst-launch-1.0" -ErrorAction SilentlyContinue

foreach ($cfg in $Configurations) {
    Write-Host "`nRunning Configuration: $($cfg.Name)..." -ForegroundColor Yellow
    
    if ($GstCmd) {
        $pipe = "gst-launch-1.0 filesrc location=`"$InputPath`" ! decodebin3 ! gvadetect model=`"$ModelXml`" device=$($cfg.Device) pre-process-backend=$($cfg.Preproc) inference-interval=$($cfg.Interval)"
        if ($cfg.Track -ne "none") {
            $pipe += " ! gvatrack tracking-type=$($cfg.Track) device=CPU"
        }
        if ($cfg.Blur) {
            if ($cfg.Device -eq "GPU") {
                $pipe += " ! d3d11convert"
            }
            $pipe += " ! gvawatermark device=CPU displ-cfg=`"enable-blur=true,show-blur-roi=face,show-labels=false`""
        }
        $pipe += " ! gvafpscounter ! fakesink async=false"
        
        $startTime = [System.DateTime]::Now
        $output = Invoke-Expression "$pipe 2>&1"
        $endTime = [System.DateTime]::Now
        $duration = ($endTime - $startTime).TotalSeconds
        
        $overallFps = "N/A"
        $fpsLines = $output | Where-Object { $_ -match "FpsCounter\(overall" }
        if ($fpsLines) {
            $lastLine = $fpsLines[-1]
            if ($lastLine -match "total=([0-9\.]+) fps") {
                $overallFps = [double]$matches[1]
            }
        }
    } else {
        # Fallback to OpenVINO Python engine
        $PyRunner = Join-Path $ProjectRoot "scripts\run_pipeline.py"
        $PolicyName = if ($cfg.Blur) { "STANDARD" } else { "STANDARD" }
        $TrackArg = if ($cfg.Track -ne "none") { "--tracking-type `"$($cfg.Track)`"" } else { "" }
        $cmd = "python `"$PyRunner`" --input-video `"$InputPath`" --device $($cfg.Device) --inference-interval $($cfg.Interval) --num-buffers $NumBuffers --output-type fps $TrackArg"
        
        $startTime = [System.DateTime]::Now
        $output = Invoke-Expression "$cmd 2>&1"
        $endTime = [System.DateTime]::Now
        $duration = ($endTime - $startTime).TotalSeconds
        
        $overallFps = "N/A"
        $fpsLine = $output | Where-Object { $_ -match "Average Throughput:\s+([0-9\.]+)\s+FPS" }
        if ($fpsLine -and ($fpsLine -match "([0-9\.]+)\s+FPS")) {
            $overallFps = [double]$matches[1]
        }
    }
    
    Write-Host "Result: $overallFps FPS (Time: $([math]::Round($duration, 2))s)" -ForegroundColor Green
    
    $Results += [PSCustomObject]@{
        Configuration = $cfg.Name
        Device        = $cfg.Device
        Interval      = $cfg.Interval
        Tracking      = $cfg.Track
        Blur          = $cfg.Blur
        FPS           = $overallFps
        DurationSec   = [math]::Round($duration, 2)
    }
}

Write-Host "`n==========================================================" -ForegroundColor Cyan
Write-Host "Benchmark Summary:" -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Cyan
$Results | Format-Table -AutoSize

$ReportDir = Split-Path -Parent $ReportCsv
if (-not (Test-Path $ReportDir)) { New-Item -ItemType Directory -Path $ReportDir -Force | Out-Null }
$Results | Export-Csv -Path $ReportCsv -NoTypeInformation
Write-Host "Benchmark results saved to: $ReportCsv" -ForegroundColor Cyan
