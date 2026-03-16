FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV DISPLAY=:99
ENV SCREEN_WIDTH=1440
ENV SCREEN_HEIGHT=900
ENV SCREEN_DEPTH=24
ENV AGENT_SERVICE_PORT=9222
ENV LIBGL_ALWAYS_SOFTWARE=1
ENV PYTHONPATH=/app

# GUI/runtime dependencies + build prerequisites + utilities + desktop apps
# (single layer to minimise image size)
RUN apt-get update && apt-get install -y --no-install-recommends \
    xfce4 \
    xfce4-terminal \
    xfce4-goodies \
    dbus-x11 \
    x11vnc \
    xvfb \
    x11-utils \
    websockify \
    novnc \
    ffmpeg \
    scrot \
    wmctrl \
    xclip \
    xdotool \
    fonts-liberation \
    fonts-noto-core \
    fonts-dejavu-core \
    gir1.2-gtk-3.0 \
    python3-gi \
    python3-gi-cairo \
    python3 \
    python3-pip \
    python3-venv \
    wget \
    curl \
    gnupg \
    ca-certificates \
    socat \
    # misc utilities
    flameshot \
    xournalpp \
    htop \
    lsof \
    strace \
    # desktop apps & tools
    vlc \
    libreoffice \
    libreoffice-gtk3 \
    evince \
    gedit \
    gnome-terminal \
    file \
    unzip \
    p7zip-full \
    jq \
    git \
    net-tools \
    iputils-ping \
    dnsutils \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

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

# 5900 = VNC, 6080 = noVNC web UI, 9222 = Agent Service API
EXPOSE 5900 6080 9222

# Health check — agent_service /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:9222/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
