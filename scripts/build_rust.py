#!/usr/bin/env python3
"""Cross-platform Rust extension build helper.

Usage:
  python scripts/build_rust.py
  python scripts/build_rust.py --target x86_64-apple-darwin
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)


def ensure_command(name: str, install_hint: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"Missing required command '{name}'. {install_hint}")


def ensure_maturin() -> None:
    if shutil.which("maturin") is not None:
        return

    print("maturin not found. Installing into current Python environment...")
    run([sys.executable, "-m", "pip", "install", "maturin"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and install Rust PyO3 extension")
    parser.add_argument("--target", default=None, help="Optional Rust target triple")
    parser.add_argument("--no-verify", action="store_true", help="Skip import verification")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    rust_dir = project_root / "rust_engine"

    if not rust_dir.exists():
        raise RuntimeError(f"Rust engine directory not found: {rust_dir}")

    ensure_command("cargo", "Install Rust via rustup: https://rustup.rs")
    ensure_maturin()

    cmd = ["maturin", "develop", "--release"]
    if args.target:
        cmd.extend(["--target", args.target])

    print("=== Building Rust chess_engine extension ===")
    run(cmd, cwd=rust_dir)

    if not args.no_verify:
        print("=== Verifying install ===")
        run(
            [
                sys.executable,
                "-c",
                "import chess_engine; print(chess_engine.benchmark(256))",
            ]
        )

    print("=== SUCCESS ===")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f"Build failed with exit code {exc.returncode}", file=sys.stderr)
        raise SystemExit(exc.returncode)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
