#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/build"
STAGE="$BUILD/lambda"
ARTIFACT="$BUILD/cip-lambda.zip"

rm -rf "$STAGE" "$ARTIFACT"
mkdir -p "$STAGE"

uv export --frozen --no-dev --no-emit-project --format requirements-txt \
  --output-file "$BUILD/requirements.txt"

uv pip install \
  --requirement "$BUILD/requirements.txt" \
  --target "$STAGE" \
  --python-platform aarch64-manylinux2014 \
  --python-version 3.13 \
  --only-binary :all:

cp -R "$ROOT/src/cip" "$STAGE/cip"
mkdir -p "$STAGE/policies"
cp "$ROOT/policies/investment-policy.yaml" "$STAGE/policies/"

find "$STAGE" -name "__pycache__" -type d -prune -exec rm -rf {} +
find "$STAGE" -exec touch -h -t 202001010000 {} +
(cd "$STAGE" && find . -type f | LC_ALL=C sort | zip -X -q "$ARTIFACT" -@)

echo "Built $ARTIFACT ($(du -h "$ARTIFACT" | cut -f1))"
