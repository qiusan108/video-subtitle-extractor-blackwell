param(
    [string]$Root = $PSScriptRoot,
    [ValidateSet("12.6", "12.9", "13.0")]
    [string]$Cuda = "12.9",
    [string]$PaddleVersion = "3.3.1"
)

$ErrorActionPreference = "Stop"
$venv = Join-Path $Root "videoEnv"
$venvPython = Join-Path $venv "Scripts\python.exe"
$requirements = Join-Path $Root "requirements-blackwell.txt"
$assetInstaller = Join-Path $Root "download-assets.ps1"

if (-not (Test-Path -LiteralPath $requirements)) {
    throw "Missing dependency list: $requirements"
}

if (-not (Test-Path -LiteralPath $assetInstaller)) {
    throw "Missing runtime asset installer: $assetInstaller"
}

& $assetInstaller -Root $Root -Platform windows
if (-not $?) { throw "Failed to prepare OCR models and native tools." }

if (-not (Test-Path -LiteralPath $venvPython)) {
    $pyLauncher = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        & $pyLauncher.Source -3.12 -m venv $venv
    }
    else {
        $python = Get-Command "python.exe" -ErrorAction Stop
        & $python.Source -m venv $venv
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create the Python environment. Install 64-bit Python 3.12."
    }
}

$packageChannel = "cu" + $Cuda.Replace(".", "")
$paddleIndex = "https://www.paddlepaddle.org.cn/packages/stable/$packageChannel/"

& $venvPython -m pip install --upgrade pip wheel setuptools
if ($LASTEXITCODE -ne 0) { throw "Failed to update pip." }

& $venvPython -m pip uninstall -y paddlepaddle
& $venvPython -m pip install "paddlepaddle-gpu==$PaddleVersion" -i $paddleIndex
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install PaddlePaddle GPU $PaddleVersion from $paddleIndex"
}

& $venvPython -m pip install -r $requirements
if ($LASTEXITCODE -ne 0) { throw "Failed to install VSE dependencies." }

$env:PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK = "True"
& $venvPython -c `
    "import paddle; assert paddle.device.is_compiled_with_cuda(); print('Paddle', paddle.__version__, 'CUDA', paddle.version.cuda(), paddle.device.get_device())"
if ($LASTEXITCODE -ne 0) { throw "Paddle GPU verification failed." }

Write-Host ""
Write-Host "Blackwell environment is ready." -ForegroundColor Green
Write-Host "Run: powershell -NoProfile -ExecutionPolicy Bypass -File `"$Root\diagnose.ps1`""
