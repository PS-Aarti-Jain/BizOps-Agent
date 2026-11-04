import json
import pytest
from httpx import AsyncClient, ASGITransport

from ui.app import app, _session_jobs, _session_progress


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def transport():
    return ASGITransport(app=app)


@pytest.mark.asyncio
async def test_non_demo_mode_rejects_unauthenticated_requests(transport, monkeypatch):
    """
    In non-demo mode (DEMO_MODE=false), accessing protected data endpoints
    without credentials returns 401 Unauthorized.
    """
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.delenv("TOKEN_USER_MAP", raising=False)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/data/inventory")
        assert res.status_code == 401
        assert "Authentication required" in res.json()["detail"]

        res_orders = await client.get("/api/data/orders")
        assert res_orders.status_code == 401

        res_fin = await client.get("/api/data/finance")
        assert res_fin.status_code == 401

        res_sup = await client.get("/api/data/suppliers")
        assert res_sup.status_code == 401

        res_po = await client.get("/api/data/purchase_orders")
        assert res_po.status_code == 401


@pytest.mark.asyncio
async def test_non_demo_mode_rejects_committed_demo_tokens(transport, monkeypatch):
    """
    In non-demo mode (DEMO_MODE=false), hardcoded demo tokens from DEFAULT_TOKEN_USER_MAP
    are strictly rejected with 401 Unauthorized.
    """
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.delenv("TOKEN_USER_MAP", raising=False)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for demo_tok in ["token-admin-1", "token-manager-1", "token-operator-1"]:
            res = await client.get("/api/data/inventory", headers={"Authorization": f"Bearer {demo_tok}"})
            assert res.status_code == 401, f"Expected 401 for demo token {demo_tok} in non-demo mode, got {res.status_code}"


@pytest.mark.asyncio
async def test_non_demo_mode_accepts_configured_token(transport, monkeypatch):
    """
    In non-demo mode (DEMO_MODE=false), tokens explicitly configured via the
    TOKEN_USER_MAP environment variable succeed.
    """
    monkeypatch.setenv("DEMO_MODE", "false")
    configured_tokens = {
        "prod-secret-token-xyz": {
            "user_id": "USR-PROD-01",
            "role": "Admin",
            "name": "Production Administrator",
        }
    }
    monkeypatch.setenv("TOKEN_USER_MAP", json.dumps(configured_tokens))

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/data/inventory", headers={"Authorization": "Bearer prod-secret-token-xyz"})
        assert res.status_code == 200
        assert res.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_session_ownership_enforced_in_non_demo_mode(transport, monkeypatch):
    """
    In non-demo mode (DEMO_MODE=false), session queries and results are isolated:
    User B cannot inspect User A's active session or results.
    """
    monkeypatch.setenv("DEMO_MODE", "false")
    configured_tokens = {
        "token-user-a": {"user_id": "USER-ALICE", "role": "Manager", "name": "Alice"},
        "token-user-b": {"user_id": "USER-BOB", "role": "Operator", "name": "Bob"},
    }
    monkeypatch.setenv("TOKEN_USER_MAP", json.dumps(configured_tokens))

    # Stage an active query job belonging to User A
    session_id = "SESS-SEC-ALICE-01"
    _session_jobs[session_id] = {
        "session_id": session_id,
        "user_id": "USER-ALICE",
        "user_role": "Manager",
        "status": "ok",
        "answer": "Sensitive financial report for Alice.",
        "steps": ["Step 1: queried db"],
    }
    _session_progress[session_id] = ["Step 1: queried db"]

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. User A accesses their own session -> 200 OK
            res_a = await client.get(f"/api/query/result?session_id={session_id}", headers={"Authorization": "Bearer token-user-a"})
            assert res_a.status_code == 200
            assert res_a.json()["answer"] == "Sensitive financial report for Alice."

            # 2. User B tries to access User A's session -> 403 Forbidden
            res_b = await client.get(f"/api/query/result?session_id={session_id}", headers={"Authorization": "Bearer token-user-b"})
            assert res_b.status_code == 403
            assert "Forbidden" in res_b.json()["detail"]

            # 3. User B tries to access User A's query progress -> 403 Forbidden
            res_b_prog = await client.get(f"/api/query/progress?session_id={session_id}", headers={"Authorization": "Bearer token-user-b"})
            assert res_b_prog.status_code == 403

    finally:
        _session_jobs.pop(session_id, None)
        _session_progress.pop(session_id, None)


@pytest.mark.asyncio
async def test_demo_mode_permits_unauthenticated_and_demo_tokens(transport, monkeypatch):
    """
    In demo mode (DEMO_MODE=true), unauthenticated access and committed demo tokens
    remain supported for local UI exploration.
    """
    monkeypatch.setenv("DEMO_MODE", "true")

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Unauthenticated request succeeds with demo role
        res = await client.get("/api/data/inventory")
        assert res.status_code == 200
        assert res.json()["status"] == "ok"

        # Committed demo token succeeds
        res_demo = await client.get("/api/data/inventory", headers={"Authorization": "Bearer token-admin-1"})
        assert res_demo.status_code == 200
        assert res_demo.json()["status"] == "ok"
