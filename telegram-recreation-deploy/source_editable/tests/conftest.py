"""Offline tests must never access private model weights or charge WaveSpeed.

Tests specifically exercising face QA opt in and mock model inference.
Production defaults are independent and use the local ONNX models.
"""
import pytest


@pytest.fixture(autouse=True)
def no_real_identity_models_in_unit_tests(monkeypatch):
    monkeypatch.setenv('RECREATION_FACE_QA_MODE','off')
