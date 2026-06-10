"""Integration tests for the auth middleware (fix #3: fail-closed enforcement).

Uses FastAPI's TestClient against the real app. These paths never reach the DB:
the middleware rejects unauthenticated protected requests before the route runs.
"""
import pytest

from app.storage import supabase_client as sc


@pytest.fixture()
def client():
    # Only meaningful when Supabase is configured (auth enforced). Skip otherwise.
    if not sc and not hasattr(sc, "get_client"):
        pytest.skip("supabase_client unavailable")
    import app.api as api
    if not api._SUPABASE_CONFIGURED or api._AUTH_DISABLED:
        pytest.skip("auth not enforced in this environment")
    from fastapi.testclient import TestClient
    return TestClient(api.app)


def test_public_landing_page_is_served_without_token(client):
    resp = client.get("/")
    assert resp.status_code == 200


def test_protected_endpoint_requires_token(client):
    resp = client.get("/modules")
    assert resp.status_code == 401


def test_protected_daily_dashboard_requires_token(client):
    resp = client.get("/daily/dashboard")
    assert resp.status_code == 401


def test_bogus_bearer_is_rejected(client):
    # A malformed/invalid token must be rejected (verified against Supabase).
    resp = client.get("/modules", headers={"Authorization": "Bearer not-a-real-jwt"})
    assert resp.status_code == 401
