#!/usr/bin/env python3
"""Cross-platform app launcher.

Usage:
  python scripts/run_server.py
  python scripts/run_server.py --debug
  python scripts/run_server.py --production
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def str_to_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def run_gunicorn(host: str, port: int, workers: int) -> int:
    cmd = [
        sys.executable,
        "-m",
        "gunicorn",
        "app:app",
        "--bind",
        f"{host}:{port}",
        "--workers",
        str(workers),
        "--timeout",
        "120",
    ]
    return subprocess.call(cmd)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run ROOKHIDE server cross-platform")
    parser.add_argument("--host", default=os.getenv("FLASK_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "5000")))
    parser.add_argument("--debug", action="store_true", help="Enable Flask debug mode")
    parser.add_argument("--production", action="store_true", help="Use gunicorn when available")
    parser.add_argument("--workers", type=int, default=2, help="Gunicorn worker count in production mode")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    debug_env = str_to_bool(os.getenv("FLASK_DEBUG", "false"))
    debug = args.debug or debug_env

    os.environ["FLASK_HOST"] = args.host
    os.environ["PORT"] = str(args.port)
    os.environ["FLASK_DEBUG"] = "true" if debug else "false"

    if args.production and os.name != "nt":
        try:
            return run_gunicorn(args.host, args.port, args.workers)
        except Exception as exc:
            print(f"Gunicorn launch failed, falling back to Flask dev server: {exc}", file=sys.stderr)

    from app import app

    app.run(host=args.host, port=args.port, debug=debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
