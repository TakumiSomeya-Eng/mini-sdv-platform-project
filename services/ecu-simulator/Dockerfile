# ECU Simulator — Dockerfile
# ──────────────────────────────────────────────────────────────────────────────
# Builds a minimal Python container for the ECU simulator service.
#
# Design decisions:
#   - python:3.11-slim over the full image: smaller attack surface, faster pull.
#   - requirements.txt copied before source: maximises Docker layer cache hits
#     so that code-only changes skip the pip install step on rebuild.
#   - PYTHONUNBUFFERED=1: critical for real-time log visibility in
#     `docker compose logs`. Without this, Python buffers stdout and log lines
#     appear in bursts rather than in real time.
#   - USER 1000: run as non-root — security baseline for container services.

FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install dependencies first (cached layer — only invalidated when
# requirements.txt changes, not when main.py changes).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# Non-root execution
USER 1000

CMD ["python", "main.py"]
