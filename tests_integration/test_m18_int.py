"""
Phase-2 integration tests — M18 Continuous Profiling
T18-03: Pyroscope /ready returns 200 (skip if not running)
T18-04: ai-monitor-edge profiles appear in Pyroscope (skip if not running)
"""

import time

import pytest
import requests

from conftest import pyroscope_available

_PYROSCOPE_URL = "http://localhost:4040"

pytestmark = pytest.mark.skipif(
    not pyroscope_available(_PYROSCOPE_URL),
    reason=f"Pyroscope not running at {_PYROSCOPE_URL}",
)


# ─────────────────────────────────────────────────────────────────────────────
# T18-03  Pyroscope /ready endpoint responds 200
# ─────────────────────────────────────────────────────────────────────────────
def test_pyroscope_ready_endpoint():
    resp = requests.get(f"{_PYROSCOPE_URL}/ready", timeout=3)
    assert resp.status_code == 200


def test_pyroscope_api_apps_endpoint():
    """Pyroscope lists known applications via /api/apps."""
    resp = requests.get(f"{_PYROSCOPE_URL}/api/apps", timeout=3)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ─────────────────────────────────────────────────────────────────────────────
# T18-04  After ai-monitor-edge sends profiles, they appear in Pyroscope
#         (requires ai-monitor-edge running with PYROSCOPE_URL=localhost:4040)
# ─────────────────────────────────────────────────────────────────────────────
def test_ai_monitor_edge_profile_received():
    """
    Wait up to 30 s for 'ai-monitor-edge' to appear in Pyroscope's /api/apps.
    Requires ai-monitor-edge to be running and profiling.
    """
    deadline = time.time() + 30
    while time.time() < deadline:
        resp = requests.get(f"{_PYROSCOPE_URL}/api/apps", timeout=3)
        apps = [a.get("name", "") for a in resp.json()]
        if any("ai-monitor-edge" in name for name in apps):
            return
        time.sleep(2)

    pytest.fail(
        "ai-monitor-edge profiles not received within 30 s. "
        "Ensure ai-monitor-edge is running with PYROSCOPE_URL=http://localhost:4040"
    )
