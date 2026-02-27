# ── Stage 1: Build the Rust extension ──────────────────────────────────────
FROM python:3.11-slim AS builder

# Install Rust toolchain + build essentials
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl build-essential pkg-config && \
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | \
        sh -s -- -y --default-toolchain stable && \
    rm -rf /var/lib/apt/lists/*

ENV PATH="/root/.cargo/bin:${PATH}"

WORKDIR /app

# Install maturin first (cached layer)
RUN pip install --no-cache-dir maturin>=1.0,<2.0

# Copy Rust source and build the wheel
COPY rust_engine/ rust_engine/
RUN cd rust_engine && maturin build --release && \
    pip install target/wheels/chess_engine-*.whl

# ── Stage 2: Final slim image ─────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Copy requirements and install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the pre-built Rust wheel from builder and install it
COPY --from=builder /app/rust_engine/target/wheels/ /tmp/wheels/
RUN pip install /tmp/wheels/chess_engine-*.whl && rm -rf /tmp/wheels

# Copy application code
COPY app.py encode.py decode.py util.py ./
COPY templates/ templates/
COPY static/ static/

# Create upload/output directories
RUN mkdir -p uploads outputs

# Render sets PORT env var; default to 10000
ENV PORT=10000
EXPOSE ${PORT}

# Run with gunicorn (production WSGI server)
CMD gunicorn app:app --bind 0.0.0.0:${PORT} --workers 2 --timeout 120
