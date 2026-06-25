#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$ROOT_DIR/logs"

if [[ -f "$ROOT_DIR/.env.local" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env.local"
  set +a
fi

TDX_HOST="${TDX_HOST:-127.0.0.1}"
TDX_PORT="${TDX_PORT:-9002}"
BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8788}"
FRONTEND_HOST="${FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
START_TDX="${START_TDX:-1}"
FEED_URL="${MARKET_HTTP_URL:-http://$TDX_HOST:$TDX_PORT/ticks}"

if [[ -n "${PYTHON_BIN:-}" ]]; then
  PYTHON_BIN="$PYTHON_BIN"
elif [[ -x "$ROOT_DIR/.venv-multi/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/.venv-multi/bin/python"
else
  PYTHON_BIN="python3"
fi

if [[ -n "${TDX_PYTHON_BIN:-}" ]]; then
  TDX_PYTHON_BIN="$TDX_PYTHON_BIN"
elif [[ -x "$ROOT_DIR/.venv-tdx/bin/python" ]]; then
  TDX_PYTHON_BIN="$ROOT_DIR/.venv-tdx/bin/python"
else
  TDX_PYTHON_BIN="$PYTHON_BIN"
fi

PIDS=()
CLEANED_UP=0

cleanup() {
  local code=$?
  trap - INT TERM EXIT
  if [[ "$CLEANED_UP" == "1" ]]; then
    exit "$code"
  fi
  CLEANED_UP=1
  echo
  echo "Stopping services..."
  for pid in "${PIDS[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  wait 2>/dev/null || true
  exit "$code"
}

start_service() {
  local name="$1"
  local log_file="$2"
  shift 2

  echo "Starting ${name}, log: ${log_file}"
  (cd "$ROOT_DIR" && "$@" >"$log_file" 2>&1) &
  local pid=$!
  PIDS+=("$pid")
  sleep 1
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "${name} failed to start. Recent log:"
    tail -n 80 "$log_file" || true
    exit 1
  fi
}

wait_for_url() {
  local name="$1"
  local url="$2"
  local attempts="${3:-30}"

  for _ in $(seq 1 "$attempts"); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      echo "${name} is ready: ${url}"
      return 0
    fi
    sleep 1
  done
  echo "${name} readiness was not confirmed: ${url}"
}

trap cleanup INT TERM EXIT
mkdir -p "$LOG_DIR"

if [[ "$START_TDX" != "0" ]]; then
  start_service "TDX feed" "$LOG_DIR/tdx.log" \
    env TDX_HOST="$TDX_HOST" TDX_PORT="$TDX_PORT" "$TDX_PYTHON_BIN" "$ROOT_DIR/tdx_candidate_server.py"
  wait_for_url "TDX feed" "http://$TDX_HOST:$TDX_PORT/ticks" 20
fi

start_service "FastAPI backend" "$LOG_DIR/backend.log" \
  env DATA_SOURCE="${DATA_SOURCE:-http}" \
    MARKET_HTTP_URL="$FEED_URL" \
    MARKET_HTTP_INTERVAL="${MARKET_HTTP_INTERVAL:-1}" \
    "$PYTHON_BIN" -m uvicorn backend.app:app --host "$BACKEND_HOST" --port "$BACKEND_PORT"
wait_for_url "FastAPI backend" "http://$BACKEND_HOST:$BACKEND_PORT/api/snapshot" 30

if [[ ! -d "$ROOT_DIR/frontend/node_modules" ]]; then
  echo "frontend/node_modules not found. Running npm install first."
  (cd "$ROOT_DIR/frontend" && npm install)
fi

start_service "Vite frontend" "$LOG_DIR/frontend.log" \
  env VITE_PROXY_TARGET="${VITE_PROXY_TARGET:-http://$BACKEND_HOST:$BACKEND_PORT}" \
    npm --prefix "$ROOT_DIR/frontend" run dev -- --host "$FRONTEND_HOST" --port "$FRONTEND_PORT"
wait_for_url "Vite frontend" "http://$FRONTEND_HOST:$FRONTEND_PORT" 30

cat <<EOF

All services started:
- Frontend: http://$FRONTEND_HOST:$FRONTEND_PORT
- Backend: http://$BACKEND_HOST:$BACKEND_PORT
- Feed: $FEED_URL
- Logs: $LOG_DIR

Press Ctrl+C to stop all services.
EOF

wait
