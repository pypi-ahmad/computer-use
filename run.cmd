@echo off
setlocal EnableExtensions EnableDelayedExpansion
title Computer Use Workbench
cd /d "%~dp0"

echo [INFO] Checking prerequisites...

call :ensure_command uv astral-sh.uv "uv"
if errorlevel 1 goto failed

uv python install 3.12
if errorlevel 1 (
  echo [ERROR] Failed to install Python 3.12 through uv.
  goto failed
)

call :ensure_command node OpenJS.NodeJS.LTS "Node.js LTS"
if errorlevel 1 goto failed

set "NODE_MAJOR=0"
for /f "delims=" %%V in ('node -p "parseInt(process.versions.node.split('.')[0], 10)" 2^>nul') do set "NODE_MAJOR=%%V"
if !NODE_MAJOR! LSS 22 (
  echo [INFO] Node.js 22 or newer is required; installing the current LTS release...
  call :install_package OpenJS.NodeJS.LTS "Node.js LTS"
  if errorlevel 1 goto failed
  set "NODE_MAJOR=0"
  for /f "delims=" %%V in ('node -p "parseInt(process.versions.node.split('.')[0], 10)" 2^>nul') do set "NODE_MAJOR=%%V"
  if !NODE_MAJOR! LSS 22 (
    echo [ERROR] Node.js 22 or newer is still unavailable. Restart Windows and run run.cmd again.
    goto failed
  )
)

call :ensure_command docker Docker.DockerDesktop "Docker Desktop"
if errorlevel 1 goto failed

call :ensure_docker_ready
if errorlevel 1 goto failed

call :ensure_local_env
if errorlevel 1 goto failed

echo [INFO] All prerequisites met.

docker image inspect cua-ubuntu:latest >nul 2>&1
if errorlevel 1 (
  echo [INFO] Docker image missing; building with cache...
  docker compose build
  if errorlevel 1 (
    echo [ERROR] Docker compose build failed.
    goto failed
  )
  echo [INFO] Docker image built successfully.
) else (
  echo [INFO] Docker image cua-ubuntu:latest already present.
)

if exist ".venv\Scripts\python.exe" (
  echo [INFO] Python environment already present.
) else (
  echo [INFO] Installing Python dependencies...
)
uv sync --frozen
if errorlevel 1 (
  echo [ERROR] Failed to install Python dependencies.
  goto failed
)
echo [INFO] Python dependencies are ready.

if exist "frontend\node_modules\vite\bin\vite.js" (
  echo [INFO] Frontend dependencies already present.
) else (
  echo [INFO] Installing frontend dependencies...
  pushd frontend >nul
  call npm ci
  set "NPM_EXIT=!ERRORLEVEL!"
  if not "!NPM_EXIT!"=="0" (
    popd >nul
    echo [ERROR] Failed to install frontend dependencies.
    goto failed
  )
  call npm rebuild esbuild --foreground-scripts
  set "NPM_EXIT=!ERRORLEVEL!"
  if not "!NPM_EXIT!"=="0" (
    popd >nul
    echo [ERROR] Failed to prepare the frontend build tool.
    goto failed
  )
  popd >nul
)

echo.
echo === Setup complete! ===
echo [INFO] Starting the app. Keep this window open; press Ctrl+C to stop.
echo [INFO] The dashboard opens at http://127.0.0.1:8505 after the backend health check succeeds.
uv run --frozen python "%~dp0dev.py" --open-browser
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" goto failed_with_code
endlocal
exit /b 0

:failed
set "EXIT_CODE=%ERRORLEVEL%"

:failed_with_code
echo.
echo [ERROR] Startup stopped with exit code %EXIT_CODE%.
echo [INFO] Fix the message above, then run run.cmd again.
pause
endlocal
exit /b %EXIT_CODE%

:refresh_path
set "PATH=%PATH%;%LOCALAPPDATA%\Microsoft\WinGet\Links;%USERPROFILE%\.local\bin;%ProgramFiles%\nodejs;%ProgramFiles%\Docker\Docker\resources\bin"
exit /b 0

:install_package
where winget >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Windows Package Manager ^(winget^) is required to install %~2.
  echo [INFO] Install Microsoft App Installer, then run run.cmd again.
  exit /b 1
)
echo [INFO] Installing %~2. Windows may request administrator approval...
winget install --id "%~1" --exact --source winget --silent --accept-source-agreements --accept-package-agreements --disable-interactivity
if errorlevel 1 (
  echo [ERROR] Could not install %~2 with winget.
  echo [INFO] If Windows requested a restart, restart and run run.cmd again.
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
  echo [INFO] Close this window and run run.cmd again.
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
echo [INFO] Complete any Docker/WSL setup or restart Windows, then run run.cmd again.
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
