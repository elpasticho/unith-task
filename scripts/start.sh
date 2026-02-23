#!/usr/bin/env bash
# start.sh — build and start the full event pipeline stack
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

GREEN="\033[92m"
CYAN="\033[96m"
BOLD="\033[1m"
RESET="\033[0m"

log()  { echo -e "${CYAN}${BOLD}[start]${RESET} $*"; }
ok()   { echo -e "${GREEN}${BOLD}[start]${RESET} $*"; }

log "Building and starting all services..."
docker compose up --build -d

log "Waiting for API to be ready..."
until curl -sf http://localhost:8000/health/ready | grep -q '"status":"ok"' 2>/dev/null; do
  sleep 2
done

ok "Stack is up."
echo
echo -e "  ${BOLD}API${RESET}              http://localhost:8000"
echo -e "  ${BOLD}Interactive docs${RESET} http://localhost:8000/docs"
echo -e "  ${BOLD}Prometheus${RESET}       http://localhost:8000/metrics"
echo -e "  ${BOLD}Receiver${RESET}         http://localhost:9001"
echo -e "  ${BOLD}RabbitMQ UI${RESET}      http://localhost:15672  (guest / guest)"
echo
echo -e "  Run the E2E suite:  ${BOLD}python scripts/e2e_test.py${RESET}"
echo -e "  Follow logs:        ${BOLD}docker compose logs -f consumer delivery_worker${RESET}"
echo -e "  Stop everything:    ${BOLD}bash scripts/stop.sh${RESET}"
echo
