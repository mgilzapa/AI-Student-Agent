"""Proves /daily/dashboard fans the per-module loads out concurrently.

Each module's bundle is mocked to block for 0.2s. Sequentially, 5 modules would
take ~1.0s; parallelized they finish in ~0.2s. We assert well under the
sequential time, and that the response shape is preserved.
"""
import asyncio
import time

import app.api as api


def test_dashboard_parallelizes_module_loads(monkeypatch):
    names = ["m1", "m2", "m3", "m4", "m5"]
    monkeypatch.setattr(api, "_all_module_names", lambda: names)
    # Avoid any real Supabase access (pre-warm + bulk slug resolution).
    monkeypatch.setattr(api, "_supa_client", lambda: None)
    monkeypatch.setattr(api.mp, "all_slugs", lambda: {})

    def slow_bundle(module_name, slug=None):
        time.sleep(0.2)
        # one module has a plan, to exercise both branches
        if module_name == "m1":
            return ({"daily_hours": 2.0, "progress": {"done": 1, "total": 3}, "topics": []}, [])
        return (None, [])

    monkeypatch.setattr(api.dt, "load_dashboard_bundle", slow_bundle)

    start = time.perf_counter()
    result = asyncio.run(api.daily_dashboard())
    elapsed = time.perf_counter() - start

    assert len(result["today_plans"]) == 5
    assert sum(1 for p in result["today_plans"] if p["has_plan"]) == 1
    # 5 x 0.2s sequential = 1.0s; parallel ≈ 0.2s. Generous bound proves concurrency.
    assert elapsed < 0.6, f"dashboard ran sequentially ({elapsed:.2f}s)"


def test_dashboard_empty_modules_returns_empty(monkeypatch):
    monkeypatch.setattr(api, "_all_module_names", lambda: [])
    monkeypatch.setattr(api.mp, "all_slugs", lambda: {})
    result = asyncio.run(api.daily_dashboard())
    assert result["today_plans"] == []
    assert result["recent_completed"] == []
