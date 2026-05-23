# Dashboard — Dockerfile
# ──────────────────────────────────────────────────────────────────────────────
# Builds the Streamlit dashboard container.
#
# Design decisions (same principles as ecu-simulator):
#   - python:3.11-slim base
#   - Layer cache optimisation (requirements before source)
#   - PYTHONUNBUFFERED=1 for real-time logs
#   - Non-root execution (USER 1000)
#
# Streamlit flags:
#   --server.headless=true   Disables the "would you like to share?" prompt
#                            that appears in non-headless mode — required for
#                            running inside a container without a terminal.
#   --server.address=0.0.0.0 Makes the dashboard reachable from outside the
#                            container (without this, it binds to 127.0.0.1
#                            only and is unreachable from the host machine).

FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

USER 1000

EXPOSE 8501

CMD ["streamlit", "run", "main.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true"]
