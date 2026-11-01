import os
import pytest

def pytest_configure(config):
    """Ensure test environment flag is set globally before any tests execute."""
    os.environ["ENVIRONMENT"] = "test"


@pytest.fixture(autouse=True)
def ensure_test_environment(monkeypatch):
    """Guarantee that ENVIRONMENT is set to 'test' for every test."""
    monkeypatch.setenv("ENVIRONMENT", "test")

