@echo off
setlocal
cd /d "%~dp0"

rem Stable NVIDIA CUDA/NVDEC profile. Existing process overrides take priority.
if not defined VSE_USE_VSF_RGB_IMAGES set "VSE_USE_VSF_RGB_IMAGES=1"
if not defined VSE_OCR_WORKERS set "VSE_OCR_WORKERS=2"
if not defined VSE_OCR_FRAME_BATCH_SIZE set "VSE_OCR_FRAME_BATCH_SIZE=8"
if not defined VSE_OCR_BATCH_WAIT_MS set "VSE_OCR_BATCH_WAIT_MS=0"
if not defined VSE_DECODE_WORKERS set "VSE_DECODE_WORKERS=1"
if not defined VSE_DECODE_PREFETCH set "VSE_DECODE_PREFETCH=8"
if not defined VSE_DECODE_COLLECT_WAIT_MS set "VSE_DECODE_COLLECT_WAIT_MS=2"
if not defined VSE_CANDIDATE_ENGINE set "VSE_CANDIDATE_ENGINE=nvdec"
if not defined VSE_TURBO_SAMPLE_FPS set "VSE_TURBO_SAMPLE_FPS=2.0"
if not defined VSE_AUTO_MOBILE_DET set "VSE_AUTO_MOBILE_DET=1"

call videoEnv\Scripts\activate.bat
if errorlevel 1 (
  echo VSE Python environment was not found. Run setup-blackwell.ps1 first.
  pause
  exit /b 1
)
python gui.py
set "VSE_EXIT_CODE=%ERRORLEVEL%"
pause
endlocal & exit /b %VSE_EXIT_CODE%
