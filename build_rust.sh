#!/bin/bash
# ── Build script for the Rust chess engine PyO3 extension ──────────────────
# Usage: ./build_rust.sh
#
# Prerequisites:
#   - Rust toolchain (curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh)
#   - maturin (pip install maturin)
#
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUST_DIR="$SCRIPT_DIR/rust_engine"

echo "=== Building Rust Chess Engine Extension ==="

# Source Rust environment
if [ -f "$HOME/.cargo/env" ]; then
    source "$HOME/.cargo/env"
fi

# Check prerequisites
if ! command -v cargo &> /dev/null; then
    echo "ERROR: Rust/Cargo not found. Install via: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
    exit 1
fi

if ! command -v maturin &> /dev/null; then
    echo "Installing maturin..."
    pip install maturin
fi

cd "$RUST_DIR"

# Detect Python architecture
PYTHON_ARCH=$(python3 -c "import platform; print(platform.machine())")
SYSTEM_ARCH=$(uname -m)

echo "System arch: $SYSTEM_ARCH"
echo "Python arch: $PYTHON_ARCH"
echo "Python: $(python3 --version) at $(which python3)"

if [ "$PYTHON_ARCH" = "$SYSTEM_ARCH" ] || [ "$PYTHON_ARCH" = "arm64" -a "$SYSTEM_ARCH" = "arm64" ]; then
    echo "Native build..."
    maturin build --release
else
    echo "Cross-compiling for $PYTHON_ARCH..."
    case "$PYTHON_ARCH" in
        x86_64)
            rustup target add x86_64-apple-darwin 2>/dev/null || true
            maturin build --release --target x86_64-apple-darwin
            ;;
        arm64|aarch64)
            rustup target add aarch64-apple-darwin 2>/dev/null || true
            maturin build --release --target aarch64-apple-darwin
            ;;
        *)
            echo "Unknown architecture: $PYTHON_ARCH"
            exit 1
            ;;
    esac
fi

# Install the wheel
WHEEL=$(ls -t "$RUST_DIR/target/wheels/"*.whl 2>/dev/null | head -1)
if [ -n "$WHEEL" ]; then
    echo "Installing $WHEEL..."
    pip install "$WHEEL" --force-reinstall
    echo ""
    echo "=== Verifying install ==="
    python3 -c "import chess_engine; print(chess_engine.benchmark(256))"
    echo ""
    echo "=== SUCCESS ==="
else
    echo "ERROR: No wheel found in target/wheels/"
    exit 1
fi
