from contextlib import asynccontextmanager
import os
import tempfile
import pytest
from unittest.mock import AsyncMock

from app.config import DEFAULT_USER_ID, DEFAULT_USER_ROLE
from database.repositories.repository import BizOpsRepository
from database.seed import seed_database
from security.safety_gateway import SafetyGateway
from agent.agent import BizOpsAgent


@pytest.fixture(autouse=True)
def set_test_environment(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")


@asynccontextmanager
async def create_e2e_agent(auto_confirm: bool = True, role: str = DEFAULT_USER_ROLE):
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_e2e.db")
    seed_database(db_path, force=True)
    repo = BizOpsRepository(db_path=db_path)
    safety_gw = SafetyGateway(repo=repo)
    agent = BizOpsAgent(
        user_id=DEFAULT_USER_ID,
        user_role=role,
        auto_confirm_writes=auto_confirm,
        safety_gateway=safety_gw,
        verbose=False,
    )
    agent.llm.is_available = AsyncMock(return_value=False)
    await agent.initialize()
    try:
        yield agent
    finally:
        await agent.close()
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_e2e_use_case_1_inventory_reorder():
    """E2E Use Case 1: Identify products needing reorder."""
    async with create_e2e_agent() as e2e_agent:
        response = await e2e_agent.run_query("Which products do I need to reorder?")
        assert isinstance(response, str)
        assert any(w in response.lower() for w in ["stock", "reorder", "p1001", "mouse", "units"])
        assert "Status" in response or "Answer" in response


@pytest.mark.asyncio
async def test_e2e_use_case_2_order_investigation():
    """E2E Use Case 2: Order investigation and fulfillment check for ORD-1042."""
    async with create_e2e_agent() as e2e_agent:
        response = await e2e_agent.run_query("Can we fulfill order ORD-1042?")
        assert isinstance(response, str)
        assert "ORD-1042" in response or "cannot" in response.lower() or "shortage" in response.lower()
        assert "Evidence" in response or "Status" in response


@pytest.mark.asyncio
async def test_e2e_use_case_3_supplier_comparison():
    """E2E Use Case 3: Compare suppliers for procurement decision."""
    async with create_e2e_agent() as e2e_agent:
        response = await e2e_agent.run_query("We need 100 wireless mice. Which supplier should we use?")
        assert isinstance(response, str)
        assert any(w in response for w in ["Pune Electronics", "SUP-102", "supplier", "recommend", "245"])


@pytest.mark.asyncio
async def test_e2e_use_case_4_finance_query():
    """E2E Use Case 4: Finance query on customer credit and overdue invoices."""
    async with create_e2e_agent() as e2e_agent:
        response = await e2e_agent.run_query("Check credit and outstanding balance for Shree Krishna General Stores")
        assert isinstance(response, str)
        assert any(w in response for w in ["Shree Krishna", "120,000", "Credit", "balance", "₹"])


@pytest.mark.asyncio
async def test_e2e_use_case_5_purchase_order_confirmation_workflow():
    """E2E Use Case 5: Purchase order creation intercepted for explicit Safety Gateway confirmation."""
    async with create_e2e_agent(auto_confirm=False, role="Manager") as agent:
        res = await agent.run_query("Create a purchase order for 100 units of P1001 from supplier SUP-102 at ₹245.00 each")
        assert isinstance(res, dict)
        assert res.get("status") == "PENDING_CONFIRMATION"
        assert "action_id" in res
        assert "card" in res
        assert "Purchase Order" in res["card"]
        assert "₹24,500.00" in res["card"] or "245" in res["card"]


@pytest.mark.asyncio
async def test_e2e_use_case_6_google_sheets_stock_query():
    """E2E Use Case 6: Google Sheets query for live stock inspection."""
    async with create_e2e_agent() as e2e_agent:
        response = await e2e_agent.run_query("Check low stock items in Google Sheets")
        assert isinstance(response, str)
        assert any(w in response.lower() for w in ["sheet", "stock", "p1002", "p1003", "p1006", "sunflower", "inventory"])
