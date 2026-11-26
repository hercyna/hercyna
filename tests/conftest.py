"""Default session setup for tests: accept CLI terms automatically unless a test clears HERCYNA_ACCEPT_TERMS."""
from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _default_accept_terms_for_tests():
    had = "HERCYNA_ACCEPT_TERMS" in os.environ
    prev = os.environ.get("HERCYNA_ACCEPT_TERMS")
    if not had:
        os.environ["HERCYNA_ACCEPT_TERMS"] = "1"
    yield
    if not had:
        os.environ.pop("HERCYNA_ACCEPT_TERMS", None)
    elif prev is not None:
        os.environ["HERCYNA_ACCEPT_TERMS"] = prev
