#!/usr/bin/env bash
# Run the Python worker unit test suite from anywhere in the repository.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKER_DIR="$REPO_ROOT/services/worker"
PYTHON="$WORKER_DIR/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  echo "Worker virtualenv not found at $PYTHON" >&2
  echo "Create it first: cd services/worker && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

cd "$WORKER_DIR"
PYTHONPATH="$WORKER_DIR" exec "$PYTHON" -m unittest discover -s "$REPO_ROOT/tests/worker" -v "$@"
