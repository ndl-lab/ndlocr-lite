#!/usr/bin/env bash
# Build the ndlocr_web Python wheel and copy it to the Vite public directory.
#
# Usage:
#   bash scripts/build-wheel.sh
#
# Requires: pip install build (PEP 517 frontend)
# Output:   ndlocr-lite-web/public/wheels/ndlocr_web-0.1.0-py3-none-any.whl

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
SRC_DIR="$REPO_ROOT/src"
WHEELS_DIR="$REPO_ROOT/ndlocr-lite-web/public/wheels"

echo "==> Building ndlocr_web wheel from $SRC_DIR"

mkdir -p "$WHEELS_DIR"

# Build pure-Python wheel (no compilation needed)
python -m build --wheel --no-isolation --outdir "$WHEELS_DIR" "$SRC_DIR"

echo ""
echo "==> Wheel written to $WHEELS_DIR"
ls -lh "$WHEELS_DIR"/*.whl
