# ROOKHIDE Developer Guide

This document is for developers onboarding to ROOKHIDE. It explains architecture, runtime flow, data formats, and common development workflows.

## 1. What ROOKHIDE Does

ROOKHIDE encodes arbitrary binary files into valid chess PGN game(s), then decodes them back to the original bytes.

Core properties:
- The output is legal chess notation.
- Encode and decode can run through a Rust-accelerated path.
- Compression and password-based encryption are supported.
- Technical metadata is hidden from visible PGN headers and stored in an invisible trailer.

## 2. High-Level Architecture

- `app.py`: Flask web server and request orchestration.
- `encode.py`: encode pipeline (payload prep -> chess encoding -> hidden metadata trailer).
- `decode.py`: decode pipeline (restore hidden metadata -> chess decode -> payload restore).
- `rust_engine/src/lib.rs`: PyO3 Rust engine for high-performance encode/decode.
- `templates/` + `static/`: UI pages and browser behavior.
- `tests/`: unit and route tests.

### Request/Response Flow

```mermaid
flowchart TD
    A[Browser] --> B[/encode route in app.py]
    B --> C[encode.py]
    C --> D{Rust available?}
    D -- yes --> E[rust_engine rust_encode_pgn]
    D -- no --> F[Python encode path]
    E --> G[PGN + hidden trailer]
    F --> G
    G --> H[Flask sends encoded_output.pgn + telemetry headers]

    A --> I[/decode route in app.py]
    I --> J[decode.py]
    J --> K[Restore hidden metadata trailer]
    K --> L{Rust decode path?}
    L -- yes --> M[rust_engine rust_decode_pgn]
    L -- no --> N[Python decode path]
    M --> O[Envelope decrypt/decompress if needed]
    N --> O
    O --> P[Flask sends decoded output file]
```

## 3. Runtime Modes

### Rust path (default when extension loads)
- `encode.py` imports `chess_engine` and calls `_rust.rust_encode_pgn(...)`.
- `decode.py` uses Rust for rust-tagged PGN (`[Engine "rust-..."]`).
- Best throughput and lower CPU usage.

### Python fallback
- Activated automatically when Rust import fails.
- Behavior remains compatible, but performance is lower.

## 4. Encode Pipeline Details (`encode.py`)

1. Read source bytes (`read_input_file`).
2. Optional compression (`prepare_payload_for_encoding`) with user level 0-9.
3. Optional encryption (`encrypt_payload`) with AES-GCM + PBKDF2 key derivation.
4. Build binary envelope (`build_payload_envelope`) with metadata flags.
5. Encode payload bits into move choices:
   - Rust path via `rust_encode_pgn`.
   - Python path via legal move candidate selection and bit packing.
6. Hide technical headers (`Seed`, `DataBits`, `Engine`, etc.) from visible PGN header block.
7. Append hidden metadata trailer (invisible whitespace):
   - v2 format = compressed JSON payload (`zlib`) prefixed with trailer version byte.
8. Return encode telemetry metadata (compression and expansion metrics).

### Key Telemetry Generated
- Source bytes.
- Payload bytes after compression stage.
- Expansion bytes and expansion ratio (`final_pgn_size / source_size`).
- Flags: compression/encryption/deterministic-seed/engine-guided/opening-camouflage/rust-path.

## 5. Decode Pipeline Details (`decode.py`)

1. Read PGN text.
2. Restore hidden technical headers from invisible trailer (`restore_technical_headers_from_comment`).
3. Decide decode path:
   - Rust path for rust-tagged PGN.
   - Python path otherwise.
4. Reconstruct payload bytes from move sequence.
5. Decode envelope (`decode_payload_envelope`):
   - Decrypt if encrypted.
   - Decompress if compressed.
6. Write final recovered file.

### Backward Compatibility
Decode supports:
- New trailer v2 (compressed JSON).
- Legacy trailer v1.
- Older plain JSON trailer payloads.

## 6. Important Data Formats

## 6.1 Envelope Format (`ENVELOPE_MAGIC = RH2`)
Binary layout:
- magic: 3 bytes
- version: 1 byte
- flags: 1 byte
- compression_level: 1 byte
- salt_len: 1 byte
- nonce_len: 1 byte
- original_size: u64
- kdf_iterations: u32
- followed by `salt | nonce | payload`

Flags:
- bit 0: compressed
- bit 1: encrypted

## 6.2 Hidden Metadata Trailer
- Stored as trailing spaces/tabs line in PGN (invisible in normal viewers).
- Prefix bits: sentinel (`0xA55A`) + payload length.
- Payload v2: `version_byte + zlib_compressed_json`.

## 7. Flask API Surface (`app.py`)

### Routes
- `POST /encode`
- `POST /decode`
- `GET /health`
- `POST /contact`
- `GET /preview` and `POST /preview`

### Encode Response Headers
- `X-Compression-Used`
- `X-Source-Bytes`
- `X-Payload-Bytes`
- `X-Payload-Ratio`
- `X-Expansion-Bytes`
- `X-Expansion-Ratio`
- `X-Encryption-Used`
- `X-Compression-Level`
- `X-Deterministic-Seed`
- `X-Engine-Guided`
- `X-Opening-Camouflage`
- `X-Rust-Path`

## 8. File Lifecycle and Reliability Safeguards

- Unique temp paths are generated per request.
- Response hooks remove request output files after send.
- Stale cleanup periodically removes old artifacts in `uploads/` and `outputs/`.
- Cleanup behavior is tunable:
  - `ROOKHIDE_FILE_RETENTION_SECONDS`
  - `ROOKHIDE_CLEANUP_INTERVAL_SECONDS`
- Client-facing error strings are sanitized for encode/decode internals.

## 9. Rust Engine Internals (`rust_engine/src/lib.rs`)

Main exported functions:
- `rust_encode(...)`
- `rust_encode_pgn(...)`
- `rust_decode(...)`
- `rust_decode_pgn(...)`
- `benchmark(...)`

Notable implementation details:
- Deterministic PRNG (`SplitMix64`) for fast move shuffling.
- SAN generation and parsing in Rust.
- Optional guided move candidate reduction for stealth strategy.
- Optional opening camouflage support.

## 10. Frontend Workflow (`templates/file.html`)

- User chooses encode/decode and file type.
- Form sends multipart request to Flask routes.
- Browser auto-downloads returned file.
- Encode notifications display:
  - compression impact
  - encryption/stealth flags
  - final PGN size and expansion ratio

## 11. Development Workflow

### Setup
```bash
python scripts/bootstrap.py
python scripts/build_rust.py
python scripts/run_server.py
```

### Test
```bash
python -m unittest discover -s tests -v
```

### Optional production-style local run
```bash
python scripts/run_server.py --production --host 0.0.0.0 --port 5000
```

## 12. Where To Start Reading

Recommended order for new developers:
1. `app.py` (route contracts and orchestration).
2. `encode.py` and `decode.py` (pipeline logic and compatibility rules).
3. `rust_engine/src/lib.rs` (performance-critical implementation).
4. `templates/file.html` (UI behavior and telemetry presentation).
5. `tests/test_app.py` and `tests/test_compression.py` (expected behavior).

## 13. Common Extension Points

- Add new hidden metadata keys:
  - include in encode hidden-header handling
  - ensure decode restoration and compatibility
- Add new encode options:
  - validate in `app.py`
  - plumb to encode/decode path and telemetry headers
  - add tests
- Tune expansion/performance:
  - adjust candidate strategy in Rust/Python
  - benchmark with representative payloads

## 14. Troubleshooting

- Rust extension not loaded:
  - rebuild with `python scripts/build_rust.py`
  - verify with import and benchmark call.
- Decode fails with password errors:
  - verify envelope metadata and password consistency.
- Unexpected expansion increase:
  - inspect source entropy (compression may not help random input)
  - inspect number of games generated and move-capacity distribution.
- PGN authenticity concerns:
  - confirm technical headers remain hidden in visible header block.

## 15. Operational Notes

- Keep `FLASK_DEBUG=false` outside local debugging.
- Keep contact API key only in environment (`WEB3FORMS_ACCESS_KEY`).
- Health checks are available at `/health` for deployment probes.
