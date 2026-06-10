"""Supabase client management + per-request user scoping.

Two client tiers:

  * ADMIN (service role) — built from SUPABASE_SERVICE_KEY. **Bypasses RLS.**
    Restricted to auth/token verification and genuine cross-user admin work.
    Never use it for per-user data access.

  * USER  (per request)  — built from the request's verified JWT (anon key +
    ``Authorization: Bearer <user_jwt>``) so Postgres/Storage RLS enforce tenant
    isolation as a *second* layer behind the app-level ``user_id`` filters.
    ``get_client()`` returns this.

Fail-closed: when there is no authenticated request context, ``get_user_id()``
and ``get_client()`` raise instead of silently falling back to a shared user /
the RLS-bypassing admin client. The CLI pipeline and explicit dev-no-auth mode
opt back into the fallback via ``allow_fallback_user(True)``.
"""
import logging
import os
from contextvars import ContextVar
from typing import Optional

from supabase import create_client, Client
from supabase.lib.client_options import SyncClientOptions

logger = logging.getLogger(__name__)

# Service-role client is a process-wide singleton (RLS-bypass, admin only).
_admin_client: Optional[Client] = None

# Per-request state. Each HTTP request runs in its own copied context, so these
# never bleed across requests.
_request_user_id: ContextVar[Optional[str]] = ContextVar("request_user_id", default=None)
_request_token: ContextVar[Optional[str]] = ContextVar("request_token", default=None)
_request_client: ContextVar[Optional[Client]] = ContextVar("request_client", default=None)

# When False (the default in the authenticated API server), a missing request
# context is a bug and we fail closed. The CLI pipeline / dev-no-auth mode flip
# this to True so their context-less calls keep working.
_allow_fallback: bool = False

_FALLBACK_USER_ID = "00000000-0000-0000-0000-000000000001"


def allow_fallback_user(enabled: bool = True) -> None:
    """Permit ``get_user_id()`` / ``get_client()`` to fall back to the env user-id
    and the service-role admin client when there is no request context.

    Enable ONLY for the CLI pipeline or an explicit dev-no-auth mode — never in
    the authenticated API server, where a missing context must fail closed.
    """
    global _allow_fallback
    _allow_fallback = enabled


# ── Admin (service-role) client ───────────────────────────────────────────────

def get_admin_client() -> Client:
    """Service-role Supabase client. **Bypasses RLS** — restrict to auth/token
    verification and cross-user admin tasks."""
    global _admin_client
    if _admin_client is None:
        url = os.getenv("SUPABASE_URL", "")
        key = os.getenv("SUPABASE_SERVICE_KEY", "")
        if not url or not key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in .env")
        _admin_client = create_client(url, key)
    return _admin_client


# ── User (per-request, RLS-enforced) client ───────────────────────────────────

def _build_user_client(token: str) -> Client:
    """Build a Supabase client that authenticates as the user behind ``token``.

    Uses the anon key as ``apikey`` and the user's JWT as the ``Authorization``
    bearer, so PostgREST/Storage run the request as that user and RLS applies.
    """
    url = os.getenv("SUPABASE_URL", "")
    anon = os.getenv("SUPABASE_ANON_KEY", "")
    if not url or not anon:
        raise RuntimeError("SUPABASE_URL and SUPABASE_ANON_KEY must be set in .env")
    return create_client(
        url, anon, SyncClientOptions(headers={"Authorization": f"Bearer {token}"})
    )


def get_client() -> Client:
    """User-scoped Supabase client for the current request (RLS-enforced).

    Built lazily from the request's verified JWT (stashed by the auth middleware)
    and cached for the duration of the request. Falls back to the RLS-bypassing
    admin client only when explicitly allowed (CLI / dev-no-auth); otherwise a
    missing context raises so we never silently bypass tenant isolation.
    """
    cached = _request_client.get()
    if cached is not None:
        return cached

    token = _request_token.get()
    if token:
        client = _build_user_client(token)
        _request_client.set(client)
        return client

    if _allow_fallback:
        return get_admin_client()

    raise RuntimeError(
        "get_client() called without an authenticated request context — refusing "
        "to fall back to the service-role client (that would bypass RLS / tenant "
        "isolation). This indicates auth middleware did not run for this request."
    )


# ── Per-request context setters ────────────────────────────────────────────────

def set_request_user_id(uid: str) -> None:
    """Called by the auth middleware to scope the current request to a user."""
    _request_user_id.set(uid)


def set_request_token(token: Optional[str]) -> None:
    """Stash the request's verified JWT so ``get_client()`` can build an
    RLS-enforcing user client. Clears any client cached for this context."""
    _request_token.set(token)
    _request_client.set(None)


def close_request_client() -> None:
    """Best-effort close of the per-request client's HTTP sessions to avoid
    file-descriptor leaks, then clear the per-request context. Safe to call when
    no client was built."""
    client = _request_client.get()
    if client is not None:
        for sub_attr in ("_postgrest", "_storage", "_functions"):
            sub = getattr(client, sub_attr, None)
            session = getattr(sub, "session", None)
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass
        auth = getattr(client, "auth", None)
        http_client = getattr(auth, "_http_client", None)
        if http_client is not None:
            try:
                http_client.close()
            except Exception:
                pass
    _request_client.set(None)
    _request_token.set(None)
    _request_user_id.set(None)


# ── User id ────────────────────────────────────────────────────────────────────

def get_user_id() -> str:
    """Authenticated user id for the current request.

    Fails closed when there is no request context, unless fallback is explicitly
    enabled (CLI / dev-no-auth), in which case the env ``SUPABASE_USER_ID`` is
    used.
    """
    uid = _request_user_id.get()
    if uid:
        return uid
    if _allow_fallback:
        return os.getenv("SUPABASE_USER_ID", _FALLBACK_USER_ID)
    raise RuntimeError(
        "get_user_id() called without an authenticated request context — refusing "
        "to fall back to the shared user. This indicates auth middleware did not "
        "run for this request."
    )
