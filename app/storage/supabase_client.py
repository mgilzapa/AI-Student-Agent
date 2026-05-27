"""Supabase client singleton and per-request user-ID helper."""
import os
from contextvars import ContextVar
from typing import Optional
from supabase import create_client, Client

_client: Client | None = None
_request_user_id: ContextVar[Optional[str]] = ContextVar("request_user_id", default=None)


def get_client() -> Client:
    global _client
    if _client is None:
        url = os.getenv("SUPABASE_URL", "")
        key = os.getenv("SUPABASE_SERVICE_KEY", "")
        if not url or not key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in .env")
        _client = create_client(url, key)
    return _client


def set_request_user_id(uid: str) -> None:
    """Called by auth middleware to scope the current request to a user."""
    _request_user_id.set(uid)


def get_user_id() -> str:
    """Returns the authenticated user ID for the current request, or the env fallback."""
    uid = _request_user_id.get()
    if uid:
        return uid
    return os.getenv("SUPABASE_USER_ID", "00000000-0000-0000-0000-000000000001")
