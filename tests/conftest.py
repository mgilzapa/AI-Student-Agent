"""Shared pytest setup.

Tests run without an HTTP request context. By default the data layer now fails
closed when there is no authenticated user, so we permit the env/admin fallback
for the test session — exactly what the CLI does. Tests that assert the
fail-closed behavior toggle the flag off explicitly inside the test.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from app.storage import supabase_client as sc


@pytest.fixture(autouse=True)
def _supabase_test_context():
    prev = sc._allow_fallback
    sc.allow_fallback_user(True)
    # Start every test with a clean per-request context.
    sc._request_user_id.set(None)
    sc._request_token.set(None)
    sc._request_client.set(None)
    try:
        yield
    finally:
        sc._allow_fallback = prev
        sc._request_user_id.set(None)
        sc._request_token.set(None)
        sc._request_client.set(None)
