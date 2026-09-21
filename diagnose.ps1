param(
    [string]$Root = $PSScriptRoot
)

$ErrorActionPreference = "Stop"
$failures = 0

function Write-Check([bool]$Passed, [string]$Message) {
    if ($Passed) {
        Write-Host "[PASS] $Message" -ForegroundColor Green
    }
    else {
        Write-Host "[FAIL] $Message" -ForegroundColor Red
        $script:failures++
    }
}

function Get-EffectiveValue([string]$Name, [string]$Default) {
    $value = [Environment]::GetEnvironmentVariable($Name, "Process")
    if (-not $value) {
        $value = [Environment]::GetEnvironmentVariable($Name, "User")
    }
    if (-not $value) {
        $value = $Default
    }
    return $value
}

Write-Host "VSE Blackwell / RTX 50 series diagnostic" -ForegroundColor Cyan
Write-Host "Root: $Root"
Write-Host ""

$gpuRows = @()
try {
    $gpuRows = & nvidia-smi `
        --query-gpu=name,memory.total,driver_version,compute_cap `
        --format=csv,noheader 2>$null
}
catch {}
Write-Check ($gpuRows.Count -gt 0) "NVIDIA driver and nvidia-smi"
foreach ($gpuRow in $gpuRows) {
    Write-Host "GPU: $gpuRow"
    if ($gpuRow -match 'RTX\s+50\d{2}') {
        Write-Host "[PASS] RTX 50 series / Blackwell GPU detected" `
            -ForegroundColor Green
    }
    else {
        Write-Host "[INFO] Non-RTX-50 NVIDIA GPU; generic CUDA/NVDEC path will be used" `
            -ForegroundColor Yellow
    }
}

$python = Join-Path $Root "videoEnv\Scripts\python.exe"
$main = Join-Path $Root "backend\main.py"
$ocr = Join-Path $Root "backend\tools\ocr.py"
$subtitleOcr = Join-Path $Root "backend\tools\subtitle_ocr.py"
$subtitleDetect = Join-Path $Root "backend\tools\subtitle_detect.py"
$nvidiaVideo = Join-Path $Root "backend\tools\nvidia_video.py"
$mobileDetector = Join-Path $Root "backend\models\V5\PP-OCRv5_mobile_det_infer"
$serverRecognizer = Join-Path $Root "backend\models\V5\PP-OCRv5_server_rec_infer"

Write-Check (Test-Path -LiteralPath $python) "VSE Python environment"
Write-Check (Test-Path -LiteralPath $mobileDetector) "PP-OCRv5 mobile detector"
Write-Check (Test-Path -LiteralPath $serverRecognizer) "PP-OCRv5 server recognizer"

$source = if (Test-Path -LiteralPath $ocr) {
    Get-Content -LiteralPath $ocr -Raw
}
else {
    ""
}
Write-Check ($source -match 'VSE high-speed auto profile enabled') `
    "hybrid auto source marker"

$ffmpeg = Get-EffectiveValue "VSE_FFMPEG_PATH" ""
if (-not $ffmpeg -or -not (Test-Path -LiteralPath $ffmpeg)) {
    $ffmpegCommand = Get-Command "ffmpeg.exe" -ErrorAction SilentlyContinue
    if ($ffmpegCommand) {
        $ffmpeg = $ffmpegCommand.Source
    }
}
if (-not $ffmpeg -or -not (Test-Path -LiteralPath $ffmpeg)) {
    $searchRoots = @(
        (Join-Path $Root "ffmpeg"),
        (Join-Path (Split-Path $Root -Parent) "ffmpeg"),
        (Join-Path ${env:ProgramFiles} "ffmpeg"),
        "C:\ffmpeg",
        "D:\ffmpeg"
    ) | Select-Object -Unique
    foreach ($searchRoot in $searchRoots) {
        if (Test-Path -LiteralPath $searchRoot) {
            $ffmpegFile = Get-ChildItem -LiteralPath $searchRoot -Recurse `
                -Filter "ffmpeg.exe" -File -ErrorAction SilentlyContinue |
                Select-Object -First 1
            if ($ffmpegFile) {
                $ffmpeg = $ffmpegFile.FullName
                break
            }
        }
    }
}
Write-Check ($ffmpeg -and (Test-Path -LiteralPath $ffmpeg)) "FFmpeg executable"
if ($ffmpeg -and (Test-Path -LiteralPath $ffmpeg)) {
    $hwaccels = (& $ffmpeg -hide_banner -hwaccels 2>&1 | Out-String)
    Write-Check ($LASTEXITCODE -eq 0 -and $hwaccels -match '(?m)^cuda\s*$') `
        "FFmpeg CUDA hardware acceleration"
    $decoders = (& $ffmpeg -hide_banner -decoders 2>&1 | Out-String)
    foreach ($decoder in @("h264_cuvid", "hevc_cuvid", "av1_cuvid", "vp9_cuvid")) {
        Write-Check ($LASTEXITCODE -eq 0 -and $decoders -match $decoder) `
            "FFmpeg $decoder decoder"
    }
    Write-Host "FFmpeg: $ffmpeg"
}

$settings = [ordered]@{
    VSE_CANDIDATE_ENGINE = Get-EffectiveValue "VSE_CANDIDATE_ENGINE" "nvdec"
    VSE_TURBO_SAMPLE_FPS = Get-EffectiveValue "VSE_TURBO_SAMPLE_FPS" "2.0"
    VSE_OCR_WORKERS = Get-EffectiveValue "VSE_OCR_WORKERS" "2"
    VSE_OCR_FRAME_BATCH_SIZE = Get-EffectiveValue "VSE_OCR_FRAME_BATCH_SIZE" "8"
    VSE_AUTO_MOBILE_DET = Get-EffectiveValue "VSE_AUTO_MOBILE_DET" "1"
}
Write-Host ""
Write-Host "Effective profile:"
$settings.GetEnumerator() | ForEach-Object {
    Write-Host "  $($_.Key)=$($_.Value)"
}

if (Test-Path -LiteralPath $python) {
    & $python -m py_compile $main $ocr $subtitleOcr $subtitleDetect $nvidiaVideo
    Write-Check ($LASTEXITCODE -eq 0) "Modified Python files compile"
    & $python -m unittest discover -s (Join-Path $Root "tests") -p "test_*.py"
    Write-Check ($LASTEXITCODE -eq 0) "Blackwell compatibility tests"
}

Write-Host ""
if ($failures -gt 0) {
    Write-Host "Diagnostic failed: $failures check(s)." -ForegroundColor Red
    exit 1
}
Write-Host "All checks passed. The next step is a real automatic-mode extraction." `
    -ForegroundColor Green
