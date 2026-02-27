# ── Stage 1: Build the Rust extension ──────────────────────────────────────
FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl build-essential pkg-config && \
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | \
        sh -s -- -y --default-toolchain stable && \
    rm -rf /var/lib/apt/lists/*

ENV PATH="/root/.cargo/bin:${PATH}"

WORKDIR /app

RUN pip install --no-cache-dir "maturin>=1.0,<2.0"

COPY rust_engine/ rust_engine/
RUN cd rust_engine && maturin build --release -i python3.11 && \
    mkdir -p /tmp/wheels && \
    cp target/wheels/*.whl /tmp/wheels/

# ── Stage 2: Final slim image ─────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install the pre-built Rust wheel
COPY --from=builder /tmp/wheels/ /tmp/wheels/
RUN pip install --no-cache-dir /tmp/wheels/*.whl && rm -rf /tmp/wheels

# Copy application code
COPY app.py encode.py decode.py util.py ./
COPY templates/ templates/
COPY static/ static/

# Create runtime directories
RUN mkdir -p uploads outputs

# Render sets PORT env var; default to 10000
ENV PORT=10000
EXPOSE ${PORT}

CMD gunicorn app:app --bind "0.0.0.0:${PORT}" --workers 2 --timeout 120
