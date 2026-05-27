#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3.12}"

DIST_DIR="$ROOT/dist/shortform-ai"

usage() {
  cat <<'EOF'
Usage: scripts/release_shortform_ai.sh

Build and validate local shortform-ai release artifacts.

Checks performed:
  - CLI unit tests
  - sensitive information scan over release source files
  - Python wheel + sdist build
  - sensitive information scan over built artifacts
  - Homebrew formula Ruby syntax check, when Ruby is available

Outputs:
  dist/shortform-ai/shortform_ai-<version>-py3-none-any.whl
  dist/shortform-ai/shortform_ai-<version>.tar.gz
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

cd "$ROOT"

echo "==> Using Python: $PYTHON_BIN"
"$PYTHON_BIN" --version

if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'; then
  echo "shortform-ai requires Python 3.12+. Set PYTHON_BIN=/path/to/python3.12 and retry." >&2
  exit 1
fi

if ! "$PYTHON_BIN" -m build --version >/dev/null 2>&1; then
  echo "Python build tooling is missing. Run: $PYTHON_BIN -m pip install build" >&2
  exit 1
fi

echo "==> Running CLI tests"
PYTHONPATH="$ROOT" "$PYTHON_BIN" -m unittest discover "$ROOT/tests"
PYTHONPATH="$ROOT" "$PYTHON_BIN" -m content_ai_runtime.cli --version >/dev/null
PYTHONPATH="$ROOT" "$PYTHON_BIN" -m shortform_ai.cli --version >/dev/null

echo "==> Running source sensitive-information check"
"$PYTHON_BIN" scripts/check_shortform_release.py

echo "==> Cleaning dist"
rm -rf "$DIST_DIR"
mkdir -p "$DIST_DIR"

echo "==> Building Python wheel and sdist"
"$PYTHON_BIN" -m build . --outdir "$DIST_DIR"

echo "==> Running built-artifact sensitive-information check"
"$PYTHON_BIN" scripts/check_shortform_release.py --dist "$DIST_DIR"

if command -v ruby >/dev/null 2>&1; then
  echo "==> Checking Homebrew formula Ruby syntax"
  ruby -c Formula/shortform-ai.rb >/dev/null
fi

echo ""
echo "shortform-ai release artifacts are ready:"
ls -lh "$DIST_DIR"
