"""Unit tests for the per-request tenant scoping in app/storage/supabase_client.

Covers the two security fixes:
  * #2 (A): get_user_id()/get_client() fail closed without a request context,
            instead of silently returning a shared user / RLS-bypassing client.
  * #2 (B1): get_client() builds a user-scoped client that carries the user's
             JWT (RLS enforces), while the admin client uses the service key.
"""
import pytest

from app.storage import supabase_client as sc


# ── Fail-closed behavior (fix #2 A) ───────────────────────────────────────────

def test_get_user_id_fails_closed_without_context():
    sc.allow_fallback_user(False)
    sc._request_user_id.set(None)
    with pytest.raises(RuntimeError):
        sc.get_user_id()


def test_get_client_fails_closed_without_context():
    sc.allow_fallback_user(False)
    sc._request_token.set(None)
    sc._request_client.set(None)
    with pytest.raises(RuntimeError):
        sc.get_client()


def test_get_user_id_uses_request_context_when_set():
    sc.allow_fallback_user(False)
    sc.set_request_user_id("user-abc")
    assert sc.get_user_id() == "user-abc"


def test_get_user_id_fallback_only_when_explicitly_enabled(monkeypatch):
    monkeypatch.setenv("SUPABASE_USER_ID", "11111111-1111-1111-1111-111111111111")
    sc.allow_fallback_user(True)
    sc._request_user_id.set(None)
    assert sc.get_user_id() == "11111111-1111-1111-1111-111111111111"


# ── User-scoped client carries the user JWT (fix #2 B1) ────────────────────────

def test_user_client_carries_user_jwt_not_service_key(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon_public_key")
    sc._request_client.set(None)
    sc.set_request_user_id("uid-1")
    sc.set_request_token("USER_JWT_TOKEN")

    client = sc.get_client()
    try:
        pg_headers = dict(client.postgrest.session.headers)
        st_headers = dict(client.storage.session.headers)
        # apikey identifies the project (anon), Authorization identifies the user.
        assert pg_headers.get("apikey") == "anon_public_key"
        assert pg_headers.get("authorization") == "Bearer USER_JWT_TOKEN"
        assert st_headers.get("authorization") == "Bearer USER_JWT_TOKEN"
    finally:
        sc.close_request_client()


def test_user_client_is_cached_per_request(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon_public_key")
    sc._request_client.set(None)
    sc.set_request_token("USER_JWT_TOKEN")
    try:
        assert sc.get_client() is sc.get_client()
    finally:
        sc.close_request_client()


def test_admin_client_uses_service_key(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "service_role_key")
    sc._admin_client = None
    try:
        client = sc.get_admin_client()
        pg_headers = dict(client.postgrest.session.headers)
        assert pg_headers.get("authorization") == "Bearer service_role_key"
    finally:
        try:
            sc._admin_client = None
        except Exception:
            pass


def test_set_request_token_invalidates_cached_client(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon_public_key")
    sc._request_client.set(None)
    sc.set_request_token("TOKEN_A")
    client_a = sc.get_client()
    sc.set_request_token("TOKEN_B")
    client_b = sc.get_client()
    try:
        assert client_a is not client_b
        assert dict(client_b.postgrest.session.headers).get("authorization") == "Bearer TOKEN_B"
    finally:
        sc.close_request_client()
