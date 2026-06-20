"""
Phase-1 unit tests — M17 Edge AI Deployment
Tests: T17-01 to T17-06
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

from conftest import load_service

ame = load_service("ai-monitor-edge")

# Sensor path constants (copied from service for readability)
_SPEED  = "Vehicle.Speed"
_SOC    = "Vehicle.Powertrain.TractionBattery.StateOfCharge.Current"
_CABIN  = "Vehicle.Cabin.HVAC.AmbientAirTemperature"


def _reading(speed=80.0, soc=60.0, cabin=22.0):
    return {_SPEED: speed, _SOC: soc, _CABIN: cabin}


# ─────────────────────────────────────────────────────────────────────────────
# T17-01  Single threshold violation → severity = WARNING
# ─────────────────────────────────────────────────────────────────────────────
def test_rules_single_violation_is_warning():
    history = [_reading(speed=200.0)]   # speed 200 > 130 → 1 violation
    result = ame._infer_rules(history)

    assert result["severity"] == "WARNING"
    assert result["anomaly"] is True
    assert result["engine"] == "rules"


# ─────────────────────────────────────────────────────────────────────────────
# T17-02  Two simultaneous violations → severity = CRITICAL
# ─────────────────────────────────────────────────────────────────────────────
def test_rules_two_violations_is_critical():
    history = [_reading(speed=200.0, soc=10.0)]  # speed + SoC both out of range
    result = ame._infer_rules(history)

    assert result["severity"] == "CRITICAL"
    assert result["anomaly"] is True


# ─────────────────────────────────────────────────────────────────────────────
# T17-03  All signals within normal range → severity = NORMAL
# ─────────────────────────────────────────────────────────────────────────────
def test_rules_normal_values_no_anomaly():
    history = [_reading(speed=80.0, soc=75.0, cabin=22.0)]
    result = ame._infer_rules(history)

    assert result["severity"] == "NORMAL"
    assert result["anomaly"] is False
    assert "within normal" in result["explanation"]


# ─────────────────────────────────────────────────────────────────────────────
# T17-04  Signal frozen for ≥ 5 consecutive readings → frozen signal detected
# ─────────────────────────────────────────────────────────────────────────────
def test_rules_frozen_signal_detected():
    # Speed is constant (in-range value), so only frozen detection fires
    history = [_reading(speed=50.0)] * 5

    result = ame._infer_rules(history)

    assert result["anomaly"] is True
    assert "frozen" in result["explanation"]


def test_rules_frozen_not_triggered_with_four_readings():
    # Fewer than 5 identical readings should NOT trigger frozen detection
    history = [_reading(speed=50.0)] * 4
    result = ame._infer_rules(history)
    assert "frozen" not in result["explanation"]


# ─────────────────────────────────────────────────────────────────────────────
# T17-05  _analyze falls back to rule engine when _model is None
# ─────────────────────────────────────────────────────────────────────────────
def test_analyze_uses_rules_when_model_absent():
    original_model = ame._model
    try:
        ame._model = None
        result = ame._analyze([_reading(speed=200.0)])
        assert result["engine"] == "rules"
    finally:
        ame._model = original_model


# ─────────────────────────────────────────────────────────────────────────────
# T17-06  MQTT alert topic follows sdv/{VEHICLE_ID}/alerts/ai-edge pattern
# ─────────────────────────────────────────────────────────────────────────────
def test_alert_topic_format():
    vehicle_id = ame.VEHICLE_ID   # "vehicle-001" (default)
    expected   = f"sdv/{vehicle_id}/alerts/ai-edge"
    assert ame.ALERT_TOPIC == expected
