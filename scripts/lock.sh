#!/usr/bin/env bash
# Regenerate requirements.lock: exact pins for every transitive dependency, for
# all platforms (Windows, macOS, Linux) on Python 3.12. Needs `uv` (free, MIT/Apache).
set -euo pipefail
cd "$(dirname "$0")/.."
uv pip compile pyproject.toml --extra dev --universal --python-version 3.12 \
  --no-header --annotation-style line -o requirements.lock
echo "requirements.lock updated"
