#!/usr/bin/env bash
# setup.sh — One-command setup for CUA
#
# Usage:
#   bash setup.sh          # normal setup
#   bash setup.sh --clean  # DESTRUCTIVE: compose down + prune ALL images/volumes

set -euo pipefail

YELLOW='\033[1;33m'
GREEN='\033[1;32m'
RED='\033[1;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── Check prerequisites ──────────────────────────────────────────────────────
command -v docker >/dev/null 2>&1 || error "Docker is required. Install: https://docs.docker.com/get-docker/"
command -v python3 >/dev/null 2>&1 || error "Python 3 is required."
command -v node >/dev/null 2>&1 || error "Node.js is required."

docker info >/dev/null 2>&1 || error "Docker daemon is not running. Start Docker and retry."

info "All prerequisites met."

# ── Optional destructive cleanup ─────────────────────────────────────────────
if [[ "${1:-}" == "--clean" ]]; then
  warn "Running destructive Docker cleanup (--clean): removing compose containers/images/volumes and pruning ALL Docker images/volumes..."
  docker compose down --rmi all -v || true
  docker system prune -a --volumes -f
fi

# ── Build via Compose (source of truth) ──────────────────────────────────────
info "Building Docker image (compose)..."
docker compose build
info "Docker image built."

# ── Install Python deps ──────────────────────────────────────────────────────
info "Installing Python dependencies..."
if [[ ! -d ".venv" ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
info "Python dependencies installed."

# ── Install frontend deps ────────────────────────────────────────────────────
info "Installing frontend dependencies..."
pushd frontend >/dev/null
npm install
popd >/dev/null
info "Frontend dependencies installed."

info ""
info "=== Setup complete! ==="
info ""
info "To run the system:"
info "  1. Start container: docker compose up -d --build"
info "  2. Start backend:   source .venv/bin/activate && python -m backend.main"
info "  3. Start frontend:  cd frontend && npm run dev"
info "  4. Open http://localhost:3000"
info ""