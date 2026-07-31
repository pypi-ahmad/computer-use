@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM Usage:
REM   setup.bat
REM   setup.bat --bootstrap-only
REM   setup.bat --clean

set "CLEAN=0"
set "BOOTSTRAP_ONLY=0"

:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="--clean" (
  set "CLEAN=1"
  shift
  goto parse_args
)
if /I "%~1"=="--bootstrap-only" (
  set "BOOTSTRAP_ONLY=1"
  shift
  goto parse_args
)
if /I "%~1"=="--help" goto show_help
if /I "%~1"=="-h" goto show_help
echo [ERROR] Unknown option: %~1
exit /b 1

:show_help
echo Usage:
echo   setup.bat [--clean] [--bootstrap-only]
echo.
echo Options:
echo   --clean           Destructive Docker cleanup before rebuilding.
echo   --bootstrap-only  Prepare the environment but do not launch dev.py.
echo   --help            Show this help text.
exit /b 0

:args_done

echo [INFO] Checking prerequisites...

where docker >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Docker CLI not found. Install Docker Desktop.
  exit /b 1
)

where uv >nul 2>&1
if errorlevel 1 (
  echo [ERROR] uv not found. Install it from https://docs.astral.sh/uv/.
  exit /b 1
)

uv python install 3.12

where node >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Node.js not found.
  exit /b 1
)

docker info >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Docker daemon is not running. Start Docker Desktop and retry.
  exit /b 1
)

echo [INFO] All prerequisites met.

REM Destructive cleanup only when explicitly requested
if "%CLEAN%"=="1" (
  echo [WARN] Running destructive Docker cleanup ^(--clean^)...
  docker compose down --rmi all -v
  docker system prune -a --volumes -f
) else (
  echo [INFO] Purging previous CUA container and image before rebuild...
  REM Stop + remove the compose-managed container and its anonymous volumes.
  REM This is scoped to this project only -- unrelated Docker resources are untouched.
  docker compose down -v >nul 2>&1
  REM Remove any leftover container by name (in case it was started outside compose).
  docker rm -f cua-environment >nul 2>&1
  REM Remove the previously built image so the next build is from scratch.
  docker image rm -f cua-ubuntu:latest >nul 2>&1
  echo [INFO] Previous CUA Docker artifacts removed.
)

echo [INFO] Building Docker image (compose)...
docker compose build --no-cache
if errorlevel 1 (
  echo [ERROR] Docker compose build failed.
  exit /b 1
)
echo [INFO] Docker image built successfully.

echo [INFO] Installing Python dependencies...
uv sync --frozen
if errorlevel 1 (
  echo [ERROR] Failed to install Python dependencies.
  exit /b !errorlevel!
)
echo [INFO] Python dependencies installed.

echo [INFO] Installing frontend dependencies...
pushd frontend >nul
call npm ci
if errorlevel 1 (
  popd >nul
  echo [ERROR] Failed to install frontend dependencies.
  exit /b !errorlevel!
)
popd >nul
echo [INFO] Frontend dependencies installed.

echo.
echo === Setup complete! ===
if "%BOOTSTRAP_ONLY%"=="1" (
  echo [INFO] Bootstrap-only mode requested; not launching dev.py.
  echo [INFO] Run "uv run python dev.py" for day-to-day startup.
  echo.
  endlocal
  exit /b 0
)

echo [INFO] Launching the full stack...
echo [INFO] The browser UI will be available at http://localhost:3000 once Vite is ready.
uv run --frozen python "%~dp0dev.py"
set "EXIT_CODE=%ERRORLEVEL%"

endlocal
exit /b %EXIT_CODE%
