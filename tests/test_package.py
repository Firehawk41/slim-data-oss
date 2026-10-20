"""Every layer imports cleanly (also gives coverage something to measure from day one)."""

import importlib

import pytest

import slim_data


@pytest.mark.parametrize("layer", ["domain", "engine", "pipeline", "persistence", "service", "adapters"])
def test_layer_imports(layer):
    module = importlib.import_module(f"slim_data.{layer}")
    assert module.__doc__


def test_version():
    assert slim_data.__version__ == "0.1.0"
