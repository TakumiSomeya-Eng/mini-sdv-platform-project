# MQTT Bridge — Dockerfile
# ──────────────────────────────────────────────────────────────────────────────
# Builds a minimal Python container for the MQTT bridge service.
# Same conventions as ecu-simulator and dashboard (M1):
#   - python:3.11-slim   — minimal image, smaller attack surface
#   - requirements first  — maximises Docker layer cache on code-only changes
#   - PYTHONUNBUFFERED=1  — real-time log visibility in docker compose logs
#   - USER 1000           — non-root execution

FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

USER 1000

CMD ["python", "main.py"]
