#!/usr/bin/env bash
# Cutroom setup for macOS and Linux. Finds (or installs) Python 3.12, then runs
# setup/bootstrap.py, which installs FFmpeg/Python deps and runs `cutroom doctor`.
#   ./setup.sh            interactive
#   ./setup.sh --yes      answer yes to install prompts
#   ./setup.sh --dry-run  show what would happen
set -euo pipefail
cd "$(dirname "$0")"

find_python() {
  for c in python3.12 python3 python; do
    if command -v "$c" >/dev/null 2>&1; then
      if "$c" -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)' 2>/dev/null; then
        command -v "$c"; return 0
      fi
    fi
  done
  if command -v uv >/dev/null 2>&1; then
    uv python find 3.12 2>/dev/null && return 0
  fi
  return 1
}

PY="$(find_python || true)"
if [ -z "$PY" ]; then
  echo "[setup] Python 3.12 not found."
  if [ "$(uname)" = "Darwin" ] && command -v brew >/dev/null 2>&1; then
    echo "[setup] Installing python@3.12 with Homebrew..."
    brew install python@3.12
  else
    echo "[setup] Installing Python 3.12 with uv (free, MIT/Apache; installs into your user folder)."
    if ! command -v uv >/dev/null 2>&1; then
      curl -LsSf https://astral.sh/uv/install.sh | sh
      export PATH="$HOME/.local/bin:$PATH"
    fi
    uv python install 3.12
  fi
  PY="$(find_python || true)"
  [ -n "$PY" ] || { echo "[setup] Could not get Python 3.12. Install it from python.org and re-run."; exit 1; }
fi

echo "[setup] Using $PY"
exec "$PY" setup/bootstrap.py "$@"
