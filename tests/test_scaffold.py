"""Smoke tests for the package layout."""

import importlib

import pytest


@pytest.mark.parametrize("name", ["datagen", "checks"])
def test_package_imports(name: str) -> None:
    module = importlib.import_module(name)
    assert module.__doc__, f"{name} needs a module docstring stating its job"
