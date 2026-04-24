FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV DISPLAY=:99
# Viewport default is 1440x900 — single best-compromise across all four CU
# target models:
#   - Anthropic Opus 4.7 (native 1:1 up to 2576px / 3.75MP; opt into the
#     higher ceiling via CUA_OPUS47_HIRES=1 + larger docker-run viewport)
#   - Anthropic Sonnet 4.6 / Opus 4.6 (downscale internally; 1440x900 ≈ no-op)
#   - OpenAI GPT-5.4 (guide prefers 1440x900 / 1600x900)
#   - Google Gemini 3 Flash (docs recommend exactly 1440x900)
# For Opus 4.7 hi-fidelity sessions: set CUA_OPUS47_HIRES=1 and override
# SCREEN_WIDTH/SCREEN_HEIGHT up to 2560x1600 at docker run time. Do NOT
# set CUA_OPUS47_HIRES for other models — they downscale internally.
ENV SCREEN_WIDTH=1440
ENV SCREEN_HEIGHT=900
# ``WIDTH`` / ``HEIGHT`` are accepted as aliases of the canonical
# ``SCREEN_*`` names for parity with Anthropic's computer-use-demo
# quickstart container.  Overridden at docker-run time if either form
# is passed; the entrypoint reconciles them.
ENV WIDTH=1440
ENV HEIGHT=900
ENV SCREEN_DEPTH=24
ENV AGENT_SERVICE_PORT=9222
ENV LIBGL_ALWAYS_SOFTWARE=1
ENV PYTHONPATH=/app

# ──────────────────────────────────────────────────────────────────────────────
# D2 — Split installs into stable-to-volatile layers so churn in
# one tier (e.g. a desktop app change) does not invalidate the ~2 GB
# base-packages layer below it. Order: core → python → desktop/apps → chrome.
# ──────────────────────────────────────────────────────────────────────────────

# (a) Core build / network tools — changes rarely. Extended with the
# Anthropic computer-use-demo reference set (build-essential, sudo,
# software-properties-common, netcat-openbsd) so the union-of-best-
# practice packages are available for any of the four CU providers.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    curl \
    dnsutils \
    file \
    git \
    gnupg \
    iputils-ping \
    jq \
    lsof \
    net-tools \
    netcat-openbsd \
    p7zip-full \
    socat \
    software-properties-common \
    strace \
    sudo \
    unzip \
    wget \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# (b) Python runtime — changes only when we bump the minor version
RUN apt-get update && apt-get install -y --no-install-recommends \
    gir1.2-gtk-3.0 \
    python3 \
    python3-gi \
    python3-gi-cairo \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# (c) Desktop + X11 + apps — the volatile tier; invalidated most often.
# Extended with Anthropic reference desktop packages (mutter, xterm,
# imagemagick, firefox-esr, x11-apps, xpdf, tint2) so the sandbox is
# the union of the Anthropic computer-use-demo baseline and the existing
# XFCE4 stack. XFCE4 remains the primary WM; mutter is installed
# alongside for parity with Anthropic's reference. No removals.
RUN apt-get update && apt-get install -y --no-install-recommends \
    dbus-x11 \
    evince \
    ffmpeg \
    firefox-esr \
    flameshot \
    fonts-dejavu-core \
    fonts-liberation \
    fonts-noto-core \
    gedit \
    gnome-terminal \
    htop \
    imagemagick \
    libreoffice \
    libreoffice-gtk3 \
    mutter \
    novnc \
    scrot \
    tint2 \
    vlc \
    websockify \
    wmctrl \
    x11-apps \
    x11-utils \
    x11vnc \
    xclip \
    xdotool \
    xfce4 \
    xfce4-goodies \
    xfce4-terminal \
    xournalpp \
    xpdf \
    xterm \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

# OpenAI Computer Use guide mandates the sandbox be free of lockscreens
# and screensavers that would steal focus from xdotool / the agent.
# Idempotent: ``|| true`` so rebuilds with upstream removal succeed.
RUN apt-get remove -y light-locker xfce4-screensaver xfce4-power-manager 2>/dev/null || true

# Install Node.js LTS 20 for desktop tooling and optional in-container tasks
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get update \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*
RUN node -v && npm -v && npx -v

# Create application venv and install Python dependencies
RUN python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir --upgrade pip

COPY requirements.txt /tmp/requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt

# --- Google Chrome (item 1) ---
RUN wget -qO- https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google.gpg \
 && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
    > /etc/apt/sources.list.d/google-chrome.list \
 && apt-get update && apt-get install -y --no-install-recommends google-chrome-stable \
 && rm -rf /var/lib/apt/lists/*

# ── Pre-create Chrome profile to suppress first-run dialogs & keyring ────────
# Static profile creation only — NO browser execution during build.
RUN mkdir -p /tmp/chrome-profile/Default \
    && echo '{"browser":{"check_default_browser":false},"distribution":{"skip_first_run_ui":true}}' \
    > /tmp/chrome-profile/Default/Preferences \
    && touch /tmp/chrome-profile/First\ Run

# Setup working directory
WORKDIR /app

# Copy backend for shared tools (action aliases)
COPY backend /app/backend

# Copy agent service (desktop control)
COPY docker/agent_service.py /app/docker/agent_service.py

# Copy entrypoint
COPY docker/entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

# ── Non-root user ────────────────────────────────────────────────────────────
# Run the agent as UID 1000 to reduce blast radius if the model is tricked
# into running something malicious. `/app`, `/opt/venv`, and `/tmp/chrome-profile`
# are chown'd so the agent can read/write them; `/var/run/dbus` is created with
# world-writable perms so the session bus works (system bus is not required).
#
# NOTE: Ubuntu 24.04 base images ship with a default `ubuntu` user at UID 1000.
# Remove it (and its group) first so we can reuse that UID for `agent`.
RUN (userdel -r ubuntu 2>/dev/null || true) \
    && (groupdel ubuntu 2>/dev/null || true) \
    && useradd -m -u 1000 -s /bin/bash agent \
    && mkdir -p /var/run/dbus \
    && chmod 1777 /var/run/dbus \
    && chown -R agent:agent /app /opt/venv /tmp/chrome-profile \
    && chown agent:agent /entrypoint.sh
USER agent
WORKDIR /home/agent

# 5900 = VNC, 6080 = noVNC web UI, 9222 = Agent Service API
EXPOSE 5900 6080 9222

# Health check — agent_service /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:9222/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
