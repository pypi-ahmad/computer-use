#!/bin/bash

set -euo pipefail

export DISPLAY=:99
export SCREEN_WIDTH=${SCREEN_WIDTH:-1440}
export SCREEN_HEIGHT=${SCREEN_HEIGHT:-900}
export SCREEN_DEPTH=${SCREEN_DEPTH:-24}
export PATH="$PATH:/usr/bin:/usr/local/bin"
export PYTHONPATH=/app

echo "=== CUA Container Starting (XFCE4 Mode) ==="

# ─────────────────────────────────────────────
# 1. DBus (system + session)
# ─────────────────────────────────────────────
mkdir -p /var/run/dbus
dbus-daemon --system --fork 2>/dev/null || true
eval $(dbus-launch --sh-syntax)
export DBUS_SESSION_BUS_ADDRESS
echo "[DBus] Session bus: $DBUS_SESSION_BUS_ADDRESS"

# ─────────────────────────────────────────────
# 2. Xvfb (virtual framebuffer)
# ─────────────────────────────────────────────
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
Xvfb :99 -screen 0 ${SCREEN_WIDTH}x${SCREEN_HEIGHT}x${SCREEN_DEPTH} \
    -ac +extension GLX +render -noreset &
XVFB_PID=$!

# Wait for Xvfb to be ready (poll for DISPLAY)
for i in $(seq 1 20); do
    if xdpyinfo -display :99 >/dev/null 2>&1; then
        echo "[Xvfb] Display :99 ready"
        break
    fi
    sleep 0.25
done

if ! kill -0 $XVFB_PID 2>/dev/null; then
    echo "ERROR: Xvfb failed to start"
    exit 1
fi

# Verify X server is reachable (critical for xdotool)
if ! xdpyinfo -display :99 >/dev/null 2>&1; then
    echo "ERROR: X server on :99 not reachable"
    exit 1
fi

# ─────────────────────────────────────────────
# 3. XFCE4 Desktop + Window Manager
# ─────────────────────────────────────────────
echo "[Desktop] Starting XFCE4..."
startxfce4 &
XFCE_PID=$!

# Wait for the window manager to be fully operational
for i in $(seq 1 30); do
    if xdotool getactivewindow >/dev/null 2>&1; then
        echo "[Desktop] Window manager ready"
        break
    fi
    sleep 0.5
done

# Give desktop time to stabilise
sleep 1

# D1 — verify XFCE4 launcher is still alive (set -e does not catch
# background fork-then-exit failures).
if ! kill -0 "$XFCE_PID" 2>/dev/null; then
    echo "ERROR: startxfce4 exited unexpectedly"
    exit 1
fi
if ! pgrep -x xfwm4 >/dev/null 2>&1 && ! pgrep -x xfce4-session >/dev/null 2>&1; then
    echo "ERROR: no xfwm4 / xfce4-session process found after startup"
    exit 1
fi

# ─────────────────────────────────────────────
# 4. x11vnc
# ─────────────────────────────────────────────
echo "[VNC] Starting x11vnc..."
VNC_DIR="${HOME:-/tmp}/.vnc"
VNC_LOG="${HOME:-/tmp}/x11vnc.log"
mkdir -p "$VNC_DIR"
if [ -n "${VNC_PASSWORD:-}" ]; then
    x11vnc -storepasswd "$VNC_PASSWORD" "$VNC_DIR/passwd"
    x11vnc -display :99 -forever -rfbauth "$VNC_DIR/passwd" -shared -rfbport 5900 -bg -o "$VNC_LOG"
else
    echo "[VNC] WARNING: No VNC_PASSWORD set — VNC access is unauthenticated"
    x11vnc -display :99 -forever -nopw -shared -rfbport 5900 -bg -o "$VNC_LOG"
fi

# D1 — x11vnc uses -bg (daemonise) so we need to confirm a process
# actually survived and is listening on :5900 before moving on.
sleep 1
if ! pgrep -x x11vnc >/dev/null 2>&1; then
    echo "ERROR: x11vnc failed to start (see $VNC_LOG)"
    exit 1
fi

# ─────────────────────────────────────────────
# 5. noVNC (Web access)
# ─────────────────────────────────────────────
echo "[noVNC] Starting websockify..."
websockify --web=/usr/share/novnc/ 6080 localhost:5900 &
WS_PID=$!

# D1 — websockify is a foreground daemon; verify it didn't crash in
# the first second (e.g. port 6080 already bound).
sleep 1
if ! kill -0 "$WS_PID" 2>/dev/null; then
    echo "ERROR: websockify exited unexpectedly (is port 6080 in use?)"
    exit 1
fi

# ─────────────────────────────────────────────
# 5b. Browser bootstrap — default browser + pre-warm Chrome profile
# ─────────────────────────────────────────────
echo "[Browser] Configuring default browser..."
# Set Google Chrome as the default web browser for xdg-open
if command -v google-chrome >/dev/null 2>&1; then
    xdg-settings set default-web-browser google-chrome.desktop 2>/dev/null || true
    # Ensure Chrome profile directory exists (seeded at build time)
    mkdir -p /tmp/chrome-profile/Default
    echo "[Browser] ✓ Chrome set as default browser (profile seeded at build time)"
elif command -v firefox >/dev/null 2>&1; then
    xdg-settings set default-web-browser firefox.desktop 2>/dev/null || true
    echo "[Browser] ✓ Firefox set as default browser"
else
    echo "[Browser] WARNING: No browser found for xdg-settings"
fi

# ─────────────────────────────────────────────
# 8. Pre-flight verification
# ─────────────────────────────────────────────
echo "[Verify] Running pre-flight checks..."

# X server check
if xdotool getmouselocation >/dev/null 2>&1; then
    echo "[Verify] ✓ X server + xdotool operational"
else
    echo "[Verify] ✗ xdotool cannot reach X server on DISPLAY=$DISPLAY"
fi

# ─────────────────────────────────────────────
# 9. Agent service
# ─────────────────────────────────────────────
# ── Hard-check desktop tool binaries ─────────────────────────────────
command -v xdotool  || echo "ERROR: xdotool missing from PATH"
command -v wmctrl   || echo "ERROR: wmctrl missing from PATH"
command -v xclip    || echo "ERROR: xclip missing from PATH"

echo "=== XFCE4 Desktop Ready ==="
echo "Access via: http://localhost:6080"

# Run agent as PID 1 so it receives SIGTERM directly from Docker
echo "[Agent] Starting internal agent service (exec)..."
exec env PYTHONPATH=/app /opt/venv/bin/python /app/docker/agent_service.py
