"""
Integration test conftest — Phase 2.

Runs in a SEPARATE pytest session from unit tests (tests/) to avoid
sys.modules stub conflicts. Real packages used: gymnasium, highway_env,
lancedb, sentence_transformers, paho.mqtt.

Only the comms/OTel layers that are never installed in this project's
Python environment are stubbed here.
"""

import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Required for sentence-transformers on this machine (TF protobuf conflict workaround)
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

# Stub non-installed packages (comms/OTel/ML-serving layers, not the compute libs)
_STUBS = [
    "kuksa_client", "kuksa_client.grpc",
    "influxdb_client", "influxdb_client.client", "influxdb_client.client.write_api",
    "opentelemetry", "opentelemetry.sdk", "opentelemetry.sdk.resources",
    "opentelemetry.sdk.trace", "opentelemetry.sdk.trace.export",
    "opentelemetry.exporter", "opentelemetry.exporter.otlp",
    "opentelemetry.exporter.otlp.proto", "opentelemetry.exporter.otlp.proto.http",
    "opentelemetry.exporter.otlp.proto.http.trace_exporter",
    "opentelemetry.trace",
    "pyroscope",
    # NOTE: optimum intentionally NOT stubbed here — transformers uses
    # importlib.util.find_spec("optimum") which raises ValueError when optimum
    # is a MagicMock with __spec__=None. Leaving it out lets find_spec search
    # the filesystem, return None, and set _optimum_available=False safely.
]

for _name in _STUBS:
    sys.modules.setdefault(_name, MagicMock())

REPO = Path(__file__).resolve().parent.parent


def load_service(service_name: str, *, env_overrides: dict | None = None):
    """
    Load services/<service_name>/main.py with an optional env-var override
    applied only during module exec (so module-level constants pick them up).
    """
    old = {}
    if env_overrides:
        for k, v in env_overrides.items():
            old[k] = os.environ.get(k)
            os.environ[k] = v

    path = REPO / "services" / service_name / "main.py"
    mod_name = f"_sdv_int_{service_name.replace('-', '_')}"
    if mod_name in sys.modules:
        mod_name += "_" + str(id(env_overrides))   # unique name for re-load
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)

    if env_overrides:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    return mod


# ── Connectivity helpers (used by skip conditions) ────────────────────────────

def mqtt_available(host="localhost", port=1883) -> bool:
    try:
        import paho.mqtt.client as mqtt
        c = mqtt.Client()
        c.connect(host, port, keepalive=1)
        c.disconnect()
        return True
    except Exception:
        return False


def pyroscope_available(url="http://localhost:4040") -> bool:
    try:
        import requests
        r = requests.get(f"{url}/ready", timeout=1)
        return r.status_code == 200
    except Exception:
        return False
