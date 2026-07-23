#!/bin/zsh

# Double-clickable macOS launcher for the local WorldEval Lab.
# It owns only processes it starts and never reads or prints credentials.
set -u

LAB_ROOT="$(cd "$(dirname "$0")" && pwd)"
RUNTIME_DIR="$LAB_ROOT/.worldeval-runtime"
BACKEND_URL="http://127.0.0.1:8000/health"
LAB_URL="http://127.0.0.1:5173"
BACKEND_LOG="$RUNTIME_DIR/backend.log"
DASHBOARD_LOG="$RUNTIME_DIR/dashboard.log"

mkdir -p "$RUNTIME_DIR"

wait_for_url() {
  local url="$1"
  local label="$2"
  local attempts=0
  while (( attempts < 40 )); do
    if curl --fail --silent --show-error "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.25
    (( attempts += 1 ))
  done
  echo "WorldEval Lab could not start $label."
  return 1
}

if ! curl --fail --silent --show-error "$BACKEND_URL" >/dev/null 2>&1; then
  if [[ ! -x "$LAB_ROOT/.venv/bin/uvicorn" ]]; then
    echo "Python dependencies are missing. Run: python3 -m venv .venv && .venv/bin/pip install -e '.[dev,benchmark]'"
    exit 1
  fi
  echo "Starting WorldEval API…"
  # `nohup` matters here: Finder launches a .command in a short-lived shell,
  # and the lab must remain available after that shell (or its Terminal window)
  # has gone away.  Output stays in the ignored runtime directory.
  (
    cd "$LAB_ROOT"
    nohup "$LAB_ROOT/.venv/bin/uvicorn" genesis_arena.main:app --host 127.0.0.1 --port 8000 --no-access-log \
      >"$BACKEND_LOG" 2>&1 < /dev/null &
    echo $! >"$RUNTIME_DIR/backend.pid"
  )
fi

if ! wait_for_url "$BACKEND_URL" "the API"; then
  echo "Recent API output:"
  tail -20 "$BACKEND_LOG" 2>/dev/null || true
  exit 1
fi

if ! curl --fail --silent --show-error "$LAB_URL" >/dev/null 2>&1; then
  if [[ ! -d "$LAB_ROOT/dashboard/node_modules" ]]; then
    echo "Dashboard dependencies are missing. Run: cd dashboard && npm install"
    exit 1
  fi
  echo "Starting WorldEval Lab…"
  (
    cd "$LAB_ROOT/dashboard"
    nohup npm run dev -- --host 127.0.0.1 --port 5173 \
      >"$DASHBOARD_LOG" 2>&1 < /dev/null &
    echo $! >"$RUNTIME_DIR/dashboard.pid"
  )
fi

if ! wait_for_url "$LAB_URL" "the dashboard"; then
  echo "Recent dashboard output:"
  tail -20 "$DASHBOARD_LOG" 2>/dev/null || true
  exit 1
fi

echo "WorldEval Lab is ready: $LAB_URL"
open "$LAB_URL"
