param(
    [string]$InputVideo = "E:/projects/ZTE-1/video.mp4"
)

$ModelXml = "E:/projects/ZTE-1/yolov12n-face_openvino_model/yolov12n-face.xml"
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
Write-Host "DL Streamer Face Anonymization - Automated Benchmark Suite" -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Cyan

foreach ($cfg in $Configurations) {
    Write-Host "`nRunning Configuration: $($cfg.Name)..." -ForegroundColor Yellow
    
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
    
    # Parse FPS
    $overallFps = "N/A"
    $fpsLines = $output | Where-Object { $_ -match "FpsCounter\(overall" }
    if ($fpsLines) {
        $lastLine = $fpsLines[-1]
        if ($lastLine -match "total=([0-9\.]+) fps") {
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

$ReportCsv = "E:/projects/ZTE-1/benchmarks/benchmark_results.csv"
$Results | Export-Csv -Path $ReportCsv -NoTypeInformation
Write-Host "Benchmark results saved to: $ReportCsv" -ForegroundColor Cyan
