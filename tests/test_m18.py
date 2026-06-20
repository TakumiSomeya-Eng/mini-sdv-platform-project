"""
Phase-1 unit tests — M18 Continuous Profiling
Tests: T18-01, T18-02
"""

import json
import sys
from unittest.mock import MagicMock, patch, call

import numpy as np
import pytest

from conftest import load_service

ame = load_service("ai-monitor-edge")


# ─────────────────────────────────────────────────────────────────────────────
# T18-01  _setup_pyroscope calls pyroscope.configure when PYROSCOPE_URL is set
# ─────────────────────────────────────────────────────────────────────────────
def test_pyroscope_configure_called_when_url_set():
    pyroscope_mock = sys.modules["pyroscope"]
    pyroscope_mock.configure.reset_mock()

    with patch.object(ame, "PYROSCOPE_URL", "http://localhost:4040"):
        ame._setup_pyroscope()

    pyroscope_mock.configure.assert_called_once_with(
        application_name="ai-monitor-edge",
        server_address="http://localhost:4040",
        tags={"vehicle_id": ame.VEHICLE_ID},
    )


def test_pyroscope_configure_not_called_when_url_empty():
    pyroscope_mock = sys.modules["pyroscope"]
    pyroscope_mock.configure.reset_mock()

    with patch.object(ame, "PYROSCOPE_URL", ""):
        ame._setup_pyroscope()

    pyroscope_mock.configure.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# T18-02  _infer_onnx wraps inference in pyroscope.tag_wrapper
# ─────────────────────────────────────────────────────────────────────────────
def test_tag_wrapper_applied_during_onnx_inference():
    pyroscope_mock = sys.modules["pyroscope"]
    pyroscope_mock.tag_wrapper.reset_mock()

    # Set up model/tokenizer mocks so _infer_onnx can complete
    mock_model     = MagicMock()
    mock_tokenizer = MagicMock()

    # tokenizer(prompt) returns dict with input_ids that has a real .shape
    mock_inputs = {"input_ids": MagicMock()}
    mock_inputs["input_ids"].shape = [1, 10]   # real list so shape[-1] == 10
    mock_tokenizer.return_value = mock_inputs

    # decode must return valid JSON
    mock_tokenizer.decode.return_value = (
        '{"severity": "NORMAL", "anomaly": false, "explanation": "OK"}'
    )

    original_model     = ame._model
    original_tokenizer = ame._tokenizer
    try:
        ame._model     = mock_model
        ame._tokenizer = mock_tokenizer

        history = [{"Vehicle.Speed": 80.0}]
        result  = ame._infer_onnx(history)
    finally:
        ame._model     = original_model
        ame._tokenizer = original_tokenizer

    # Verify tag_wrapper was called with the expected profiling tag
    pyroscope_mock.tag_wrapper.assert_called_once_with({"function": "onnx_inference"})
    assert result["engine"] == "phi4-mini-onnx"
    assert result["severity"] == "NORMAL"
