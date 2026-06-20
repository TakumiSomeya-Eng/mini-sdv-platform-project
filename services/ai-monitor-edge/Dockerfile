FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
# Model is mounted at runtime via hostPath volume (see k8s manifest).
# The service runs in rule-based mode until /models/phi4-mini-onnx is present.
CMD ["python", "-u", "main.py"]
