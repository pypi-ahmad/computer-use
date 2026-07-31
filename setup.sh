#!/usr/bin/env bash
# setup.sh — One-command setup for CUA
#
# Usage:
#   bash setup.sh                   # bootstrap and launch the full stack
#   bash setup.sh --bootstrap-only  # bootstrap only, do not launch dev.py
#   bash setup.sh --clean           # destructive bootstrap, then launch the full stack

set -euo pipefail

YELLOW='\033[1;33m'
GREEN='\033[1;32m'
RED='\033[1;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

usage() {
  cat <<'EOF'
Usage:
  bash setup.sh [--clean] [--bootstrap-only]

Options:
  --clean           Destructive Docker cleanup before rebuilding.
  --bootstrap-only  Prepare the environment but do not launch dev.py.
  --help            Show this help text.
EOF
}

CLEAN=0
BOOTSTRAP_ONLY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --clean)
      CLEAN=1
      shift
      ;;
    --bootstrap-only)
      BOOTSTRAP_ONLY=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      error "Unknown option: $1"
      ;;
  esac
done

# ── Check prerequisites ──────────────────────────────────────────────────────
command -v docker >/dev/null 2>&1 || error "Docker is required. Install: https://docs.docker.com/get-docker/"
command -v uv >/dev/null 2>&1 || error "uv is required. Install: https://docs.astral.sh/uv/"
command -v node >/dev/null 2>&1 || error "Node.js is required."

uv python install 3.12

docker info >/dev/null 2>&1 || error "Docker daemon is not running. Start Docker and retry."

info "All prerequisites met."

# ── Optional destructive cleanup ─────────────────────────────────────────────
if [[ "$CLEAN" == "1" ]]; then
  warn "Running destructive Docker cleanup (--clean): removing compose containers/images/volumes and pruning ALL Docker images/volumes..."
  docker compose down --rmi all -v || true
  docker system prune -a --volumes -f
else
  info "Purging previous CUA container and image before rebuild..."
  # Scoped to this project only — unrelated Docker resources are untouched.
  docker compose down -v >/dev/null 2>&1 || true
  docker rm -f cua-environment >/dev/null 2>&1 || true
  docker image rm -f cua-ubuntu:latest >/dev/null 2>&1 || true
  info "Previous CUA Docker artifacts removed."
fi

# ── Build via Compose (source of truth) ──────────────────────────────────────
info "Building Docker image (compose)..."
docker compose build --no-cache
info "Docker image built."

# ── Install Python deps ──────────────────────────────────────────────────────
info "Installing Python dependencies..."
uv sync --frozen
info "Python dependencies installed."

# ── Install frontend deps ────────────────────────────────────────────────────
info "Installing frontend dependencies..."
pushd frontend >/dev/null
npm ci
popd >/dev/null
info "Frontend dependencies installed."

info ""
info "=== Setup complete! ==="
info ""

if [[ "$BOOTSTRAP_ONLY" == "1" ]]; then
  info "Bootstrap-only mode requested; not launching dev.py."
  info "Run 'uv run python dev.py' for day-to-day startup."
  info ""
  exit 0
fi

info "Launching the full stack..."
info "The browser UI will be available at http://localhost:3000 once Vite is ready."
exec uv run --frozen python dev.py
