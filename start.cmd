@echo off
setlocal

title Cascade Forge

set "PROJECT_ROOT=%~dp0"
set "UDF_ROOT=%PROJECT_ROOT%UDF"
set "PYTHON_EXE=%UDF_ROOT%\.venv\Scripts\python.exe"
set "PORT=%~1"

if not defined PORT set "PORT=8000"

if not exist "%PYTHON_EXE%" (
    echo [Cascade Forge] The project virtual environment was not found.
    echo.
    echo Open a terminal in "%UDF_ROOT%" and run:
    echo     uv sync --extra dev
    echo.
    pause
    exit /b 1
)

pushd "%UDF_ROOT%"
echo [Cascade Forge] Starting on http://127.0.0.1:%PORT%/
echo [Cascade Forge] Press Ctrl+C to stop the server.
echo.

"%PYTHON_EXE%" -m cascade_forge.cli serve ^
    --dataset "datasets/geometry-v1.0.0" ^
    --v2-dataset "datasets/geometry-v2.1.0" ^
    --workspace "datasets/workspace-v2" ^
    --naca65-dataset "datasets/naca65-compressor-benchmark-v1.0.0" ^
    --host "127.0.0.1" ^
    --port "%PORT%"

set "EXIT_CODE=%ERRORLEVEL%"
popd

if not "%EXIT_CODE%"=="0" (
    echo.
    echo [Cascade Forge] The server stopped with exit code %EXIT_CODE%.
    pause
)

exit /b %EXIT_CODE%
