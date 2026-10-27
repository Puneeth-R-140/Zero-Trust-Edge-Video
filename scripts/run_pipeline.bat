@echo off
setlocal

set INPUT_TYPE=%1
if "%INPUT_TYPE%"=="" set INPUT_TYPE=file

set POLICY=%2
if "%POLICY%"=="" set POLICY=STANDARD

set DEVICE=%3
if "%DEVICE%"=="" set DEVICE=GPU

set OUTPUT_TYPE=%4
if "%OUTPUT_TYPE%"=="" set OUTPUT_TYPE=display

set INPUT_VIDEO=%5
if "%INPUT_VIDEO%"=="" set INPUT_VIDEO=%~dp0..\video.mp4

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_pipeline.ps1" -InputType %INPUT_TYPE% -Policy %POLICY% -Device %DEVICE% -OutputType %OUTPUT_TYPE% -InputVideo "%INPUT_VIDEO%"

endlocal
