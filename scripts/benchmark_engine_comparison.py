#!/usr/bin/env python3
import argparse
import csv
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from statistics import mean
from time import perf_counter

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import decode as decode_mod
import encode as encode_mod


def _parse_sizes(raw: str) -> list[int]:
    values = []
    for part in raw.split(","):
        token = part.strip().lower()
        if not token:
            continue
        if token.endswith("kb"):
            values.append(int(float(token[:-2]) * 1024))
        elif token.endswith("mb"):
            values.append(int(float(token[:-2]) * 1024 * 1024))
        else:
            values.append(int(token))
    return values


def _format_size(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.2f} MB"
    return f"{n / 1024:.1f} KB"


def _set_mode(mode: str, rust_available: bool) -> None:
    if mode == "rust":
        encode_mod.RUST_ENGINE_AVAILABLE = rust_available
        decode_mod.RUST_ENGINE_AVAILABLE = rust_available
        return
    encode_mod.RUST_ENGINE_AVAILABLE = False
    decode_mod.RUST_ENGINE_AVAILABLE = False


def _run_single(mode: str, payload: bytes) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        input_path = os.path.join(tmp, "input.bin")
        pgn_path = os.path.join(tmp, "encoded.pgn")
        output_path = os.path.join(tmp, "decoded.bin")

        with open(input_path, "wb") as f:
            f.write(payload)

        t0 = perf_counter()
        meta = encode_mod.encode(input_path, pgn_path)
        encode_s = perf_counter() - t0

        t1 = perf_counter()
        decode_mod.decode(pgn_path, output_path)
        decode_s = perf_counter() - t1

        with open(output_path, "rb") as f:
            restored = f.read()

        return {
            "mode": mode,
            "source_bytes": len(payload),
            "encode_s": encode_s,
            "decode_s": decode_s,
            "total_s": encode_s + decode_s,
            "expansion_ratio": float(meta["expansion_ratio"]),
            "ok": restored == payload,
        }


def run_benchmarks(sizes: list[int], repeats: int, include_rust: bool) -> list[dict]:
    rust_available = include_rust and encode_mod.RUST_ENGINE_AVAILABLE and decode_mod.RUST_ENGINE_AVAILABLE
    modes = ["python"] + (["rust"] if rust_available else [])

    results = []
    for size in sizes:
        payload = os.urandom(size)
        for mode in modes:
            _set_mode(mode, rust_available)
            samples = [_run_single(mode, payload) for _ in range(repeats)]

            row = {
                "mode": mode,
                "size_bytes": size,
                "size_label": _format_size(size),
                "repeats": repeats,
                "encode_s_mean": mean(s["encode_s"] for s in samples),
                "decode_s_mean": mean(s["decode_s"] for s in samples),
                "total_s_mean": mean(s["total_s"] for s in samples),
                "expansion_ratio_mean": mean(s["expansion_ratio"] for s in samples),
                "throughput_kb_s": (size / 1024) / mean(s["total_s"] for s in samples),
                "ok": all(s["ok"] for s in samples),
            }
            results.append(row)
    return results


def save_csv(results: list[dict], out_csv: Path) -> None:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "mode",
        "size_bytes",
        "size_label",
        "repeats",
        "encode_s_mean",
        "decode_s_mean",
        "total_s_mean",
        "expansion_ratio_mean",
        "throughput_kb_s",
        "ok",
    ]
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)


def save_plots(results: list[dict], out_png: Path) -> None:
    labels = sorted({r["size_label"] for r in results}, key=lambda x: float(x.split()[0]))
    by_mode = {"python": [], "rust": []}
    for mode in by_mode:
        mode_rows = [r for r in results if r["mode"] == mode]
        mode_rows.sort(key=lambda r: r["size_bytes"])
        by_mode[mode] = mode_rows

    fig, axes = plt.subplots(1, 3, figsize=(16, 4.8))

    for mode, color in (("python", "#f97316"), ("rust", "#22c55e")):
        rows = by_mode[mode]
        if not rows:
            continue
        x = [r["size_bytes"] / 1024 for r in rows]
        axes[0].plot(x, [r["total_s_mean"] for r in rows], marker="o", label=mode, color=color)
        axes[1].plot(x, [r["throughput_kb_s"] for r in rows], marker="o", label=mode, color=color)
        axes[2].plot(x, [r["expansion_ratio_mean"] for r in rows], marker="o", label=mode, color=color)

    axes[0].set_title("Total Time vs Input Size")
    axes[0].set_xlabel("Input Size (KB)")
    axes[0].set_ylabel("Seconds")
    axes[0].grid(alpha=0.3)

    axes[1].set_title("Round-Trip Throughput")
    axes[1].set_xlabel("Input Size (KB)")
    axes[1].set_ylabel("KB/s")
    axes[1].grid(alpha=0.3)

    axes[2].set_title("PGN Expansion Ratio")
    axes[2].set_xlabel("Input Size (KB)")
    axes[2].set_ylabel("x")
    axes[2].grid(alpha=0.3)

    for ax in axes:
        ax.legend()

    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=150)
    plt.close(fig)


def save_summary(results: list[dict], out_txt: Path) -> None:
    lines = []
    lines.append("ROOKHIDE Engine Comparison")
    lines.append("")
    all_ok = all(r["ok"] for r in results)
    lines.append(f"Round-trip verification: {'PASS' if all_ok else 'FAIL'}")

    rust_rows = [r for r in results if r["mode"] == "rust"]
    py_rows = [r for r in results if r["mode"] == "python"]

    if rust_rows and py_rows:
        speedups = []
        for py in py_rows:
            match = next((r for r in rust_rows if r["size_bytes"] == py["size_bytes"]), None)
            if match and match["total_s_mean"] > 0:
                speedups.append(py["total_s_mean"] / match["total_s_mean"])
        if speedups:
            lines.append(f"Average Rust speedup (total): {mean(speedups):.2f}x")

    lines.append("")
    lines.append("Per-size stats:")
    for r in sorted(results, key=lambda x: (x["size_bytes"], x["mode"])):
        lines.append(
            f"- {r['size_label']} | {r['mode']} | total={r['total_s_mean']:.4f}s | "
            f"throughput={r['throughput_kb_s']:.2f} KB/s | expansion={r['expansion_ratio_mean']:.3f}x"
        )

    out_txt.parent.mkdir(parents=True, exist_ok=True)
    out_txt.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare Python vs Rust engine performance")
    parser.add_argument(
        "--sizes",
        default="4kb,16kb,64kb,93kb,128kb",
        help="Comma-separated sizes (e.g., 4kb,16kb,64kb,93kb)",
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--out-dir", default="outputs/benchmarks")
    parser.add_argument("--no-rust", action="store_true", help="Run only Python mode")
    args = parser.parse_args()

    sizes = _parse_sizes(args.sizes)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir)
    out_csv = out_dir / f"engine_comparison_{stamp}.csv"
    out_png = out_dir / f"engine_comparison_{stamp}.png"
    out_txt = out_dir / f"engine_comparison_{stamp}.txt"

    results = run_benchmarks(sizes=sizes, repeats=args.repeats, include_rust=not args.no_rust)
    save_csv(results, out_csv)
    save_plots(results, out_png)
    save_summary(results, out_txt)

    print(f"CSV: {out_csv}")
    print(f"Graph: {out_png}")
    print(f"Summary: {out_txt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
