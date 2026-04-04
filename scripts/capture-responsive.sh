#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
python3 "$SCRIPT_DIR/capture_responsive.py" --base-url "${1:-http://127.0.0.1:5000}" --out-dir "${2:-qa_snapshots}"
