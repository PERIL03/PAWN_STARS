#!/usr/bin/env python3
import argparse
import base64
import csv
import json
import os
import sys
import tempfile
from io import StringIO
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from decode import decode
from encode import encode


PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7V9EwAAAAASUVORK5CYII="
)


def build_cases() -> list[tuple[str, bytes]]:
    cases = []
    cases.append(("text_utf8.txt", "Hello from ROOKHIDE\nLine 2\nUnicode: cafe\n".encode("utf-8")))
    cases.append(("markdown.md", "# Title\n- alpha\n- beta\n".encode("utf-8")))
    cases.append(("json_data.json", json.dumps({"project": "rookhide", "ok": True, "nums": [1, 2, 3]}).encode("utf-8")))

    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "name", "score"])
    writer.writerow([1, "Alice", 97])
    writer.writerow([2, "Bob", 88])
    cases.append(("table.csv", buf.getvalue().encode("utf-8")))

    cases.append(("raw_binary.bin", os.urandom(8192)))
    cases.append(("sample.png", base64.b64decode(PNG_B64)))
    return cases


def run(password: str | None) -> int:
    cases = build_cases()
    all_ok = True

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        for name, original in cases:
            src = tdp / name
            pgn = tdp / f"{name}.pgn"
            restored = tdp / f"restored_{name}"
            src.write_bytes(original)

            try:
                meta = encode(str(src), str(pgn), password=password)
                decode(str(pgn), str(restored), password=password)
                out = restored.read_bytes()
                ok = out == original
                all_ok = all_ok and ok
                ratio = float(meta.get("expansion_ratio", 0.0))
                print(
                    f"{'PASS' if ok else 'FAIL'} | {name:16s} | "
                    f"src={len(original):6d} | pgn={pgn.stat().st_size:7d} | ratio={ratio:7.3f}x"
                )
            except Exception as exc:
                all_ok = False
                print(f"FAIL | {name:16s} | error={exc}")

    print("\nSUMMARY:")
    print("All datatype round-trips passed." if all_ok else "Some datatype round-trips failed.")
    return 0 if all_ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify encode/decode round-trip on multiple datatypes")
    parser.add_argument("--password", default=None, help="Optional password for encode/decode test")
    args = parser.parse_args()
    return run(password=args.password)


if __name__ == "__main__":
    raise SystemExit(main())
