#!/usr/bin/env bash
# stop.sh — stop and optionally wipe the full event pipeline stack
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

YELLOW="\033[93m"
RED="\033[91m"
BOLD="\033[1m"
RESET="\033[0m"

log()  { echo -e "${YELLOW}${BOLD}[stop]${RESET} $*"; }
warn() { echo -e "${RED}${BOLD}[stop]${RESET} $*"; }

VOLUMES=false
if [[ "${1:-}" == "--volumes" || "${1:-}" == "-v" ]]; then
  VOLUMES=true
fi

log "Stopping all services..."
docker compose down

if $VOLUMES; then
  warn "Removing volumes (database + queue data will be lost)..."
  docker compose down -v
fi

echo
if $VOLUMES; then
  echo -e "  ${BOLD}Stack stopped and volumes removed.${RESET}"
else
  echo -e "  ${BOLD}Stack stopped.${RESET} Data volumes preserved."
  echo -e "  To also remove volumes:  ${BOLD}bash scripts/stop.sh --volumes${RESET}"
fi
echo
