#!/usr/bin/env bash
# ============================================================================
#  ACE Studio v2 launcher for macOS / Linux.
#  Starts the FastAPI backend, waits for it, then the React (Vite) frontend.
#  Double-click on macOS after renaming to run.command, or run:  bash run.sh
# ============================================================================
set -e
cd "$(dirname "$0")"
DEST="$(pwd)"

echo ""
echo "  ACE Studio v2"
echo "  -------------"

# ---- Automatic GitHub updates are intentionally disabled. ----
# ---- Python backend: venv + deps ----------------------------------------
if [ ! -x backend/.venv/bin/python ]; then
  [ -d backend/.venv ] && rm -rf backend/.venv
  if command -v python3 >/dev/null 2>&1; then PY=python3; else
    echo "Python 3 was not found. Install it from https://www.python.org/downloads/ and run again."
    exit 1
  fi
  echo "[setup] creating Python virtual environment ($($PY --version))..."
  "$PY" -m venv backend/.venv
fi
VPY=backend/.venv/bin/python
if ! cmp -s backend/requirements.txt backend/.venv/requirements.lock 2>/dev/null; then
  echo "[setup] installing backend dependencies (can take a few minutes)..."
  "$VPY" -m pip install --quiet --upgrade pip
  "$VPY" -m pip install --quiet -r backend/requirements.txt
  cp backend/requirements.txt backend/.venv/requirements.lock
fi

# ---- Frontend deps ------------------------------------------------------
if [ ! -d frontend/node_modules ]; then
  echo "[setup] installing frontend dependencies (first run only)..."
  ( cd frontend && npm install )
fi

# ---- Launch: backend FIRST, wait until it answers, then frontend --------
echo "[run] starting backend on http://127.0.0.1:8766"
"$VPY" -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8766 &
BACK_PID=$!
# Stop the backend when this script is interrupted or exits.
cleanup() { kill "$BACK_PID" >/dev/null 2>&1 || true; }
trap cleanup INT TERM EXIT

echo "[run] waiting for the backend to come up..."
if command -v curl >/dev/null 2>&1; then
  for _ in $(seq 1 90); do
    curl -sf -o /dev/null "http://127.0.0.1:8766/api" 2>/dev/null && { echo "[run] backend is up."; break; }
    sleep 0.8
  done
else
  sleep 4
fi

echo "[run] starting frontend on http://localhost:5173"
# Open the browser once, shortly after Vite starts.
( sleep 4; (open "http://localhost:5173" 2>/dev/null || xdg-open "http://localhost:5173" 2>/dev/null) >/dev/null 2>&1 ) &

echo ""
echo "  ACE Studio is starting. Keep this window open while you use it (Ctrl-C to stop)."
echo ""
( cd frontend && exec npm run dev )
