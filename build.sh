#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
RELEASE_DIR="$PROJECT_DIR/release"
ENTRY="$PROJECT_DIR/src/photofetch/__main__.py"
OUTPUT_NAME="photofetch"

cd "$PROJECT_DIR"

echo "=== PhotoFetch Build ==="

# 1. Setup venv
if [ ! -d "$VENV_DIR" ]; then
    echo "[1/4] Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
else
    echo "[1/4] Virtual environment exists."
fi
source "$VENV_DIR/bin/activate"

# 2. Install dependencies
echo "[2/4] Installing dependencies..."
pip install -q -e ".[dev]"
pip install -q nuitka ordered-set

# 3. Run tests
echo "[3/4] Running unit tests..."
pytest -v
echo "       All tests passed."

# 4. Build executable
echo "[4/4] Building executable with Nuitka..."
rm -rf "$RELEASE_DIR"
mkdir -p "$RELEASE_DIR"

python -m nuitka \
    --standalone \
    --onefile \
    --output-filename="$OUTPUT_NAME" \
    --output-dir="$RELEASE_DIR" \
    --include-package=photofetch \
    --include-data-dir="$PROJECT_DIR/src/photofetch/templates=photofetch/templates" \
    --include-data-dir="$PROJECT_DIR/src/photofetch/static=photofetch/static" \
    --include-data-files="$PROJECT_DIR/LICENSE=LICENSE" \
    "$ENTRY"

# Cleanup nuitka build artifacts
rm -rf "$RELEASE_DIR/__main__.build" "$RELEASE_DIR/__main__.dist" "$RELEASE_DIR/__main__.onefile-build"

echo ""
echo "=== Build complete ==="
echo "Executable: $RELEASE_DIR/$OUTPUT_NAME"
ls -lh "$RELEASE_DIR/$OUTPUT_NAME"
