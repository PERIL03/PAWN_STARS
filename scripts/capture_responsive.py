#!/usr/bin/env python3
"""Cross-platform responsive screenshot helper using Playwright CLI via npx.

Usage:
  python scripts/capture_responsive.py
  python scripts/capture_responsive.py --base-url http://127.0.0.1:5000 --out-dir qa_snapshots
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


CAPTURE_MATRIX = [
    ("Desktop Chrome", "/", "home-desktop.png"),
    ("iPhone 13", "/", "home-mobile.png"),
    ("Desktop Chrome", "/file_upload", "file-upload-desktop.png"),
    ("iPhone 13", "/file_upload", "file-upload-mobile.png"),
    ("Desktop Chrome", "/preview", "preview-desktop.png"),
    ("iPhone 13", "/preview", "preview-mobile.png"),
    ("Desktop Chrome", "/visualizer", "visualizer-desktop.png"),
    ("iPhone 13", "/visualizer", "visualizer-mobile.png"),
    ("Desktop Chrome", "/about", "about-desktop.png"),
    ("iPhone 13", "/about", "about-mobile.png"),
    ("Desktop Chrome", "/get_in_touch", "contact-desktop.png"),
    ("iPhone 13", "/get_in_touch", "contact-mobile.png"),
]


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture responsive snapshots")
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--out-dir", default="qa_snapshots")
    args = parser.parse_args()

    if shutil.which("npx") is None:
        print("Error: npx is required. Install Node.js and npm first.", file=sys.stderr)
        return 1

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Capturing snapshots from {args.base_url} into {out_dir}")
    for device, route, filename in CAPTURE_MATRIX:
        run(
            [
                "npx",
                "--yes",
                "playwright@1.52.0",
                "screenshot",
                f"--device={device}",
                "--full-page",
                "--wait-for-timeout=1200",
                f"{args.base_url}{route}",
                str(out_dir / filename),
            ]
        )

    print(f"Done. Snapshot files are in {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
