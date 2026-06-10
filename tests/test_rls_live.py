"""Live end-to-end proof that RLS blocks cross-tenant access (fix #2 B1).

Off by default — it creates throwaway users and rows in the real Supabase
project. Run explicitly:

    RUN_LIVE_RLS=1 python -m pytest tests/test_rls_live.py -v        # bash
    $env:RUN_LIVE_RLS=1; .venv\\Scripts\\python -m pytest tests/test_rls_live.py -v   # PowerShell

It creates two users, has user A insert a module, then asserts:
  * user B's client cannot SEE user A's module (RLS read isolation),
  * user B cannot INSERT a row spoofing user A's user_id (RLS with-check),
  * user A CAN read its own row.
All created users/rows are cleaned up in teardown.
"""
import os
import uuid

import pytest
from dotenv import load_dotenv

load_dotenv()

from app.storage import supabase_client as sc

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_RLS") != "1",
    reason="live RLS test is opt-in (set RUN_LIVE_RLS=1)",
)


def _sign_in(email: str, password: str) -> str:
    from supabase import create_client

    c = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_ANON_KEY"])
    res = c.auth.sign_in_with_password({"email": email, "password": password})
    assert res.session and res.session.access_token, "sign-in returned no access token"
    return res.session.access_token


def test_rls_blocks_cross_tenant_module_access():
    admin = sc.get_admin_client()
    password = "Pw!" + uuid.uuid4().hex
    email_a = f"rlstest+{uuid.uuid4().hex}@example.com"
    email_b = f"rlstest+{uuid.uuid4().hex}@example.com"

    user_a = admin.auth.admin.create_user(
        {"email": email_a, "password": password, "email_confirm": True}
    )
    user_b = admin.auth.admin.create_user(
        {"email": email_b, "password": password, "email_confirm": True}
    )
    id_a = user_a.user.id
    id_b = user_b.user.id

    created_module_ids = []
    try:
        token_a = _sign_in(email_a, password)
        token_b = _sign_in(email_b, password)
        client_a = sc._build_user_client(token_a)
        client_b = sc._build_user_client(token_b)

        # User A creates a module.
        slug = "rls-" + uuid.uuid4().hex[:10]
        inserted = (
            client_a.table("modules")
            .insert({"user_id": id_a, "name": "SECRET-A", "slug": slug})
            .execute()
        ).data
        assert inserted, "user A could not insert its own module"
        module_id_a = inserted[0]["id"]
        created_module_ids.append(module_id_a)

        # User B must NOT see user A's module (read isolation).
        rows_b = client_b.table("modules").select("id, user_id, name").execute().data or []
        assert all(r["user_id"] == id_b for r in rows_b), "user B saw rows owned by another user"
        assert module_id_a not in [r["id"] for r in rows_b], "RLS LEAK: B can read A's module"

        # User B must NOT be able to spoof a row as user A (with-check).
        spoofed = False
        try:
            res = (
                client_b.table("modules")
                .insert({"user_id": id_a, "name": "SPOOF", "slug": "spoof-" + uuid.uuid4().hex[:8]})
                .execute()
            )
            # Some client versions return data instead of raising; treat any row as a leak.
            if res.data:
                created_module_ids.append(res.data[0]["id"])
                spoofed = True
        except Exception:
            spoofed = False
        assert not spoofed, "RLS LEAK: B inserted a row owned by A"

        # User A CAN read its own module.
        rows_a = client_a.table("modules").select("id").execute().data or []
        assert module_id_a in [r["id"] for r in rows_a], "user A could not read its own module"
    finally:
        for mid in created_module_ids:
            try:
                admin.table("modules").delete().eq("id", mid).execute()
            except Exception:
                pass
        for uid in (id_a, id_b):
            try:
                admin.auth.admin.delete_user(uid)
            except Exception:
                pass
