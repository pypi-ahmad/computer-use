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

cd /d "%~dp0"

echo [INFO] Checking prerequisites...

call :ensure_command uv astral-sh.uv "uv"
if errorlevel 1 exit /b 1

uv python install 3.12
if errorlevel 1 (
  echo [ERROR] Failed to install Python 3.12 through uv.
  exit /b 1
)

call :ensure_command node OpenJS.NodeJS.LTS "Node.js LTS"
if errorlevel 1 exit /b 1

set "NODE_MAJOR=0"
for /f "delims=" %%V in ('node -p "parseInt(process.versions.node.split('.')[0], 10)" 2^>nul') do set "NODE_MAJOR=%%V"
if !NODE_MAJOR! LSS 22 (
  echo [INFO] Node.js 22 or newer is required; installing the current LTS release...
  call :install_package OpenJS.NodeJS.LTS "Node.js LTS"
  if errorlevel 1 exit /b 1
  set "NODE_MAJOR=0"
  for /f "delims=" %%V in ('node -p "parseInt(process.versions.node.split('.')[0], 10)" 2^>nul') do set "NODE_MAJOR=%%V"
  if !NODE_MAJOR! LSS 22 (
    echo [ERROR] Node.js 22 or newer is still unavailable. Restart Windows and run START.bat again.
    exit /b 1
  )
)

call :ensure_command docker Docker.DockerDesktop "Docker Desktop"
if errorlevel 1 exit /b 1

call :ensure_docker_ready
if errorlevel 1 exit /b 1

call :ensure_local_env
if errorlevel 1 exit /b 1

echo [INFO] All prerequisites met.

REM Destructive cleanup only when explicitly requested
if "%CLEAN%"=="1" (
  echo [WARN] Running destructive Docker cleanup ^(--clean^)...
  docker compose down --rmi all -v
  docker system prune -a --volumes -f
) else (
  echo [INFO] Building Docker image with the existing cache...
)

if "%CLEAN%"=="1" (
  docker compose build --no-cache
) else (
  docker compose build
)
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

for /f "delims=" %%H in ('powershell -NoProfile -Command "$stream = [IO.File]::OpenRead('frontend\package-lock.json'); try { $sha = [Security.Cryptography.SHA256]::Create(); try { [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','') } finally { $sha.Dispose() } } finally { $stream.Dispose() }"') do set "LOCK_HASH=%%H"
set "INSTALLED_HASH="
if exist "frontend\node_modules\.cua-package-lock.sha256" set /p INSTALLED_HASH=<"frontend\node_modules\.cua-package-lock.sha256"
if /I not "!LOCK_HASH!"=="!INSTALLED_HASH!" (
  echo [INFO] Installing frontend dependencies...
  pushd frontend >nul
  call npm ci
  if errorlevel 1 (
    popd >nul
    echo [ERROR] Failed to install frontend dependencies.
    exit /b !errorlevel!
  )
  call npm rebuild esbuild --foreground-scripts
  if errorlevel 1 (
    popd >nul
    echo [ERROR] Failed to prepare the frontend build tool.
    exit /b !errorlevel!
  )
  popd >nul
  >"frontend\node_modules\.cua-package-lock.sha256" echo !LOCK_HASH!
) else (
  echo [INFO] Frontend dependencies already match package-lock.json.
)

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

:refresh_path
set "PATH=%PATH%;%LOCALAPPDATA%\Microsoft\WinGet\Links;%USERPROFILE%\.local\bin;%ProgramFiles%\nodejs;%ProgramFiles%\Docker\Docker\resources\bin"
exit /b 0

:install_package
where winget >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Windows Package Manager ^(winget^) is required to install %~2.
  echo [INFO] Install Microsoft App Installer, then double-click START.bat again.
  exit /b 1
)
echo [INFO] Installing %~2. Windows may request administrator approval...
winget install --id "%~1" --exact --source winget --silent --accept-source-agreements --accept-package-agreements --disable-interactivity
if errorlevel 1 (
  echo [ERROR] Could not install %~2 with winget.
  echo [INFO] If Windows requested a restart, restart and run START.bat again.
  exit /b 1
)
call :refresh_path
exit /b 0

:ensure_command
where %~1 >nul 2>&1
if not errorlevel 1 exit /b 0
call :install_package "%~2" "%~3"
if errorlevel 1 exit /b 1
where %~1 >nul 2>&1
if errorlevel 1 (
  echo [ERROR] %~3 was installed but is not available in this terminal.
  echo [INFO] Close this window and double-click START.bat again.
  exit /b 1
)
exit /b 0

:ensure_docker_ready
docker info >nul 2>&1
if not errorlevel 1 exit /b 0
if exist "%ProgramFiles%\Docker\Docker\Docker Desktop.exe" (
  echo [INFO] Starting Docker Desktop...
  start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
)
echo [INFO] Waiting for the Docker engine ^(up to five minutes^)...
for /L %%I in (1,1,60) do (
  docker info >nul 2>&1
  if not errorlevel 1 exit /b 0
  timeout /t 5 /nobreak >nul
)
echo [ERROR] Docker Desktop did not become ready.
echo [INFO] Complete any Docker/WSL setup or restart Windows, then run START.bat again.
exit /b 1

:ensure_local_env
if not exist ".env" (
  echo [INFO] Creating .env from .env.example...
  copy /Y ".env.example" ".env" >nul
  if errorlevel 1 (
    echo [ERROR] Could not create .env.
    exit /b 1
  )
)
powershell -NoProfile -Command ^
  "$path = [IO.Path]::GetFullPath('.env');" ^
  "$text = [IO.File]::ReadAllText($path);" ^
  "$rng = [Security.Cryptography.RandomNumberGenerator]::Create();" ^
  "$agentBytes = New-Object byte[] 32; $rng.GetBytes($agentBytes);" ^
  "$agent = [Convert]::ToBase64String($agentBytes).TrimEnd('=').Replace('+','-').Replace('/','_');" ^
  "$vncBytes = New-Object byte[] 4; $rng.GetBytes($vncBytes);" ^
  "$vnc = [BitConverter]::ToString($vncBytes).Replace('-','').ToLowerInvariant();" ^
  "function Set-Missing([string]$name, [string]$value) {" ^
  "  $pattern = '(?m)^' + [regex]::Escape($name) + '=(.*)$';" ^
  "  $match = [regex]::Match($script:text, $pattern);" ^
  "  if ($match.Success -and [string]::IsNullOrWhiteSpace($match.Groups[1].Value)) {" ^
  "    $script:text = ([regex]::new($pattern)).Replace($script:text, $name + '=' + $value, 1);" ^
  "  } elseif (-not $match.Success) {" ^
  "    $script:text = $script:text.TrimEnd() + [Environment]::NewLine + $name + '=' + $value + [Environment]::NewLine;" ^
  "  }" ^
  "}" ^
  "Set-Missing 'AGENT_SERVICE_TOKEN' $agent;" ^
  "Set-Missing 'VNC_PASSWORD' $vnc;" ^
  "$rng.Dispose();" ^
  "[IO.File]::WriteAllText($path, $text, (New-Object Text.UTF8Encoding($false)));"
if errorlevel 1 (
  echo [ERROR] Could not generate required local sandbox secrets.
  exit /b 1
)
echo [INFO] Local configuration is ready; existing .env values were preserved.
exit /b 0
