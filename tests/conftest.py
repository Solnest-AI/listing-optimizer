"""Shared test settings."""
import pytest


@pytest.fixture(autouse=True)
def _no_update_check(monkeypatch):
    """The pipeline checks GitHub once a day for a newer release. Tests never reach the network;
    tests of the check itself delete this variable."""
    monkeypatch.setenv("LO_NO_UPDATE_CHECK", "1")
