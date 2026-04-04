#!/usr/bin/env python3
"""Cross-platform bootstrap script for local development.

Creates a virtual environment, installs Python dependencies, and optionally builds
Rust extension.

Usage:
  python scripts/bootstrap.py
  python scripts/bootstrap.py --with-rust
  python scripts/bootstrap.py --python python3.11
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=str(cwd) if cwd else None)


def venv_python_path(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap ROOKHIDE dev environment")
    parser.add_argument("--venv", default=".venv", help="Virtual environment directory")
    parser.add_argument("--python", default=sys.executable, help="Python executable to create venv")
    parser.add_argument("--with-rust", action="store_true", help="Also build Rust extension")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    venv_dir = project_root / args.venv

    if not venv_dir.exists():
        print(f"Creating virtual environment at {venv_dir}")
        run([args.python, "-m", "venv", str(venv_dir)])
    else:
        print(f"Using existing virtual environment at {venv_dir}")

    py = venv_python_path(venv_dir)
    if not py.exists():
        raise RuntimeError(f"Virtual environment Python not found: {py}")

    run([str(py), "-m", "pip", "install", "--upgrade", "pip"])
    run([str(py), "-m", "pip", "install", "-r", "requirements.txt"], cwd=project_root)

    if args.with_rust:
        run([str(py), "scripts/build_rust.py"], cwd=project_root)

    print("\nBootstrap complete.")
    if os.name == "nt":
        print("Activate with: .\\.venv\\Scripts\\Activate.ps1")
    else:
        print("Activate with: source .venv/bin/activate")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f"Command failed with exit code {exc.returncode}", file=sys.stderr)
        raise SystemExit(exc.returncode)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
