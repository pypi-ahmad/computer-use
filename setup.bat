@echo off
setlocal EnableExtensions

REM Usage:
REM   setup.bat
REM   setup.bat --clean

echo [INFO] Checking prerequisites...

where docker >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Docker CLI not found. Install Docker Desktop.
  exit /b 1
)

where python >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python not found.
  exit /b 1
)

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
if /I "%~1"=="--clean" (
  echo [WARN] Running destructive Docker cleanup ^(--clean^)...
  docker compose down --rmi all -v
  docker system prune -a --volumes -f
)

echo [INFO] Building Docker image (compose)...
docker compose build
if errorlevel 1 (
  echo [ERROR] Docker compose build failed.
  exit /b 1
)
echo [INFO] Docker image built successfully.

echo [INFO] Installing Python dependencies...
if not exist ".venv" (
  python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
echo [INFO] Python dependencies installed.

echo [INFO] Installing frontend dependencies...
pushd frontend >nul
call npm install
popd >nul
echo [INFO] Frontend dependencies installed.

echo.
echo === Setup complete! ===
echo.
echo To run the system:
echo   1. Start container: docker compose up -d --build
echo   2. Start backend:   .venv\Scripts\activate ^& python -m backend.main
echo   3. Start frontend:  cd frontend ^& npm run dev
echo   4. Open http://localhost:3000
echo.

endlocal