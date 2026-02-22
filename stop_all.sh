#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

if [ -z "${PREFIX:-}" ]; then
  echo "ERROR: PREFIX is not set. This script is intended to run inside Termux." >&2
  exit 1
fi

RUN_DIR="$PREFIX/var/run"
PID_FILE="$RUN_DIR/hardware-pulse.pids"

echo "Stopping (backend + crawler only)..."

if [ -f "$PID_FILE" ]; then
  echo "Killing recorded PIDs..."
  while read -r name pid; do
    [ -n "${pid:-}" ] || continue
    if kill -0 "$pid" >/dev/null 2>&1; then
      echo "- stop $name (pid=$pid)"
      kill "$pid" >/dev/null 2>&1 || true
    fi
  done <"$PID_FILE"
fi

# Fallbacks (best-effort; keep patterns narrow to avoid collateral damage).
pkill -f "crawler_wg_xianyu\.py" >/dev/null 2>&1 || true
pkill -f "java.*hardware-pulse-backend" >/dev/null 2>&1 || true
pkill -f "hardware-pulse-edge" >/dev/null 2>&1 || true

echo "OK"
