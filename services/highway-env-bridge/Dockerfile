FROM python:3.11-slim
WORKDIR /app
# highway-env pulls pygame which needs SDL libs
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsdl2-dev libsdl2-image-dev libsdl2-mixer-dev libsdl2-ttf-dev \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
CMD ["python", "-u", "main.py"]
