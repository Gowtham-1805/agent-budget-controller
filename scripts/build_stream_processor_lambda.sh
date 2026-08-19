#!/usr/bin/env bash
# Stage the Lambda deployment package for the DynamoDB Streams consumer.
#
# apps/stream_processor/handler.py imports abc_gateway.domain, abc_gateway.repo
# (including the DynamoDB backend), abc_gateway.observability.logging and
# abc_gateway.runaway -- none of which exist on a bare Lambda runtime. Before
# this script, Terraform's `archive_file` zipped only apps/stream_processor/,
# so the deployed function would fail at import with ModuleNotFoundError on its
# first invocation. `terraform validate` cannot catch this because it never
# imports the Python it packages -- see docs/FINDINGS.md.
#
# This script stages handler.py alongside a copy of the abc_gateway package and
# the exact third-party runtime dependencies its import chain touches: boto3,
# botocore, structlog and tzdata. Versions are pinned to match pyproject.toml
# exactly, because this system's correctness depends on precise
# TransactWriteItems cancellation behaviour in a specific boto3/botocore
# version -- a version drifted in by the Lambda runtime's bundled SDK would
# silently invalidate that.
#
# Deliberately does NOT pip-install the abc_gateway package itself (it has no
# standalone pyproject.toml -- it is built from the repo root). The package is
# pure Python with no compiled extensions, so a plain copy is both correct and
# avoids needing hatchling metadata at build time.
set -euo pipefail

BUILD_DIR="${1:?usage: build_stream_processor_lambda.sh <build-dir>}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PY="$REPO_ROOT/.venv/Scripts/python.exe"
[ -x "$PY" ] || PY="$REPO_ROOT/.venv/bin/python"
if ! [ -x "$PY" ] 2>/dev/null; then
    PY="python3"
fi

rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

cp "$REPO_ROOT/apps/stream_processor/handler.py" "$BUILD_DIR/handler.py"
cp -r "$REPO_ROOT/apps/gateway/src/abc_gateway" "$BUILD_DIR/abc_gateway"
find "$BUILD_DIR/abc_gateway" -name "__pycache__" -type d -prune -exec rm -rf {} +

"$PY" -m pip install --quiet --no-compile --target "$BUILD_DIR" \
    boto3==1.43.73 \
    botocore==1.43.73 \
    structlog==26.1.0 \
    tzdata==2026.3

# Strip pip's dist-info/bytecode cruft; Lambda does not need it and it only
# grows the artifact.
find "$BUILD_DIR" -name "*.dist-info" -type d -prune -exec rm -rf {} +
find "$BUILD_DIR" -name "__pycache__" -type d -prune -exec rm -rf {} +

echo "staged stream processor package: $BUILD_DIR"
