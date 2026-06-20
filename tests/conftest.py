"""
conftest.py — session-wide stubs for heavy deps.

Runs before any test module is imported, so service main.py files
can be loaded via importlib without their non-test dependencies installed.
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Stub every package that is not installed in the minimal test environment.
# Using setdefault so a package that IS installed (e.g. numpy, flask) is not replaced.
_STUBS = [
    "gymnasium",
    "highway_env",
    "kuksa_client",
    "kuksa_client.grpc",
    "paho",
    "paho.mqtt",
    "paho.mqtt.client",
    "influxdb_client",
    "influxdb_client.client",
    "influxdb_client.client.write_api",
    "opentelemetry",
    "opentelemetry.sdk",
    "opentelemetry.sdk.resources",
    "opentelemetry.sdk.trace",
    "opentelemetry.sdk.trace.export",
    "opentelemetry.exporter",
    "opentelemetry.exporter.otlp",
    "opentelemetry.exporter.otlp.proto",
    "opentelemetry.exporter.otlp.proto.http",
    "opentelemetry.exporter.otlp.proto.http.trace_exporter",
    "opentelemetry.trace",
    "optimum",
    "optimum.onnxruntime",
    "transformers",
    "pyroscope",
]

for _name in _STUBS:
    sys.modules.setdefault(_name, MagicMock())

# Shared helper used by all test files
REPO = Path(__file__).resolve().parent.parent


def load_service(service_name: str):
    """Load services/<service_name>/main.py under a unique module name."""
    path = REPO / "services" / service_name / "main.py"
    mod_name = f"_sdv_{service_name.replace('-', '_')}"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod
