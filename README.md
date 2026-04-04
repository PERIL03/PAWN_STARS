# ROOKHIDE ♟️🔐

A steganography system that hides arbitrary files inside valid chess games. Data is encoded into move choices — each legal position offers multiple valid moves, and by selecting specific ones we embed bits of information that appear as natural chess gameplay.

## Features

- **Any File Type** — encode text, images, documents, or any binary data
- **Natural-Looking Games** — all games follow standard chess rules and appear legitimate
- **Self-Destruct Timer** — optional expiry mechanism for time-sensitive data
- **Rust-Accelerated Engine** — native Rust extension via PyO3 for high-performance encoding/decoding
- **Web Interface** — upload, encode, decode, and visualize through a Flask web app
- **Live Visualizer** — watch data being encoded into chess moves in real-time

## Performance

| File Size | Encode | Decode | Total |
|-----------|--------|--------|-------|
| 1 KB      | 0.01s  | 0.01s  | 0.02s |
| 100 KB    | 0.22s  | 0.15s  | 0.37s |
| 1 MB      | 2.1s   | 1.5s   | 3.6s  |

Throughput: ~1M moves/sec (encode), ~1.4M moves/sec (decode) on Apple Silicon.

## Developer Documentation

For architecture, data flow, and implementation details, see [docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md).

## Tech Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| Backend | **Python 3.8+ / Flask** | Web server, encode/decode orchestration |
| Engine | **Rust + PyO3** | High-performance move generation, SAN/PGN generation & parsing |
| Chess | **chess crate (Rust)** + **python-chess** (fallback) | Board state, legal moves, PGN handling |
| PRNG | **SplitMix64** (custom) | Deterministic move shuffling keyed by seed |
| Frontend | **HTML/CSS/JS** | File upload, chess board visualizer (chessboard.js + chess.js) |
| Deploy | **Docker + Gunicorn** | Multi-stage Dockerfile, Render-ready |

## Project Structure

```
├── app.py                  # Flask web server
├── encode.py               # Encoding orchestration (dispatches to Rust or Python)
├── decode.py               # Decoding orchestration (dispatches to Rust or Python)
├── rust_engine/
│   ├── Cargo.toml          # Rust dependencies (pyo3, chess, rand)
│   ├── pyproject.toml      # maturin build config
│   └── src/
│       └── lib.rs          # Rust engine: encode, decode, SAN, PGN, benchmark
├── templates/              # Jinja2 HTML templates
│   ├── home.html           # Landing page
│   ├── file.html           # Encrypt/Decrypt interface
│   ├── preview.html        # Live preview
│   ├── visualizer.html     # Chess board visualizer
│   ├── About.html          # Team page
│   └── touch.html          # Contact page
├── static/
│   ├── style.css           # Main stylesheet
│   └── js/
│       ├── file-upload.js  # File upload handling
│       ├── visualizer.js   # Chess board replay
│       └── visualizer.css  # Visualizer styles
├── Dockerfile              # Multi-stage build (Rust + Python)
├── render.yaml             # Render deployment blueprint
├── requirements.txt        # Python dependencies
├── build_rust.sh           # Backward-compatible wrapper for Rust build helper
├── scripts/
│   ├── bootstrap.py        # Cross-platform venv + dependency bootstrap
│   ├── bootstrap.sh        # POSIX wrapper for bootstrap
│   ├── bootstrap.ps1       # PowerShell wrapper for bootstrap
│   ├── build_rust.py       # Cross-platform Rust build helper
│   ├── build_rust.ps1      # Windows PowerShell wrapper for Rust build
│   ├── capture_responsive.py # Cross-platform responsive snapshot helper
│   ├── capture-responsive.sh # Backward-compatible wrapper for snapshot helper
│   ├── capture-responsive.ps1 # Windows PowerShell wrapper for snapshots
│   └── run_server.py       # Cross-platform app launcher
└── .gitignore
```

## Quick Start

### Prerequisites

- Python 3.8+
- Rust toolchain (`rustup`)
- maturin (`pip install maturin`)

### Install & Run

```bash
# Clone
git clone https://github.com/<your-username>/rookhide.git
cd rookhide

# Bootstrap virtualenv + dependencies
python scripts/bootstrap.py

# Build Rust extension (cross-platform helper)
python scripts/build_rust.py

# Run (cross-platform launcher)
python scripts/run_server.py
```

The app will be available at `http://localhost:5000`.

### Platform Notes (Windows / macOS / Linux)

- Use `python -m pip ...` to ensure packages install into the active Python environment.
- Rust toolchain installation: https://rustup.rs
- One-command bootstrap wrappers:

```bash
# macOS/Linux
bash scripts/bootstrap.sh

# Windows PowerShell
powershell -ExecutionPolicy Bypass -File scripts/bootstrap.ps1
```

```bash
# Include Rust build during bootstrap
python scripts/bootstrap.py --with-rust
```

- Rust build wrappers:

```bash
# macOS/Linux
bash build_rust.sh

# Windows PowerShell
powershell -ExecutionPolicy Bypass -File scripts/build_rust.ps1
```

- Snapshot automation is cross-platform:

```bash
python scripts/capture_responsive.py --base-url http://127.0.0.1:5000 --out-dir qa_snapshots
```

```bash
# macOS/Linux wrapper
bash scripts/capture-responsive.sh

# Windows PowerShell wrapper
powershell -ExecutionPolicy Bypass -File scripts/capture-responsive.ps1
```

- Production-style launch (non-Windows):

```bash
python scripts/run_server.py --production --host 0.0.0.0 --port 5000
```

### Without Rust (pure-Python fallback)

If you skip the Rust build, the app still works — it falls back to a pure-Python encoder/decoder (significantly slower for large files).

## Usage

### Web Interface

1. Open `http://localhost:5000`
2. Go to **Encrypt/Decrypt**
3. Upload a file, optionally set a self-destruct timer and custom PGN headers
4. Download the resulting `.pgn` file
5. To decrypt, upload the `.pgn` and download the recovered file

### Python API

```python
from encode import encode
from decode import decode

# Encode a file into chess games
encode("secret.png", "output.pgn")

# With self-destruct (1 hour) and custom headers
encode("secret.png", "output.pgn",
       self_destruct_timer=3600,
       custom_headers={"White": "Alice", "Black": "Bob"})

# Decode back to original
decode("output.pgn", "recovered.png")
```

## How It Works

### Encoding

1. File bytes → bit stream
2. At each chess position, legal moves are shuffled using a deterministic PRNG (SplitMix64) seeded per-game
3. The move index encodes `floor(log₂(n))` bits, where `n` = number of legal moves
4. When a game ends (checkmate/stalemate/150 moves), a new game continues encoding
5. Output is standard PGN with `Seed`, `DataBits`, and `Engine` headers

### Decoding

1. Parse PGN headers → extract seed, data bit count, engine version
2. Replay each game: regenerate the shuffled move list, find the played move's index
3. Convert indices back to bits → bytes → original file

