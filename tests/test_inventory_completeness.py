import os
import shutil
import tempfile
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
import pytest

from app.config import DEFAULT_USER_ID, DEFAULT_USER_ROLE
from database.repositories.repository import BizOpsRepository
from database.seed import seed_database
from database.migrations import run_migrations
from security.safety_gateway import SafetyGateway
from agent.agent import BizOpsAgent
from services.inventory_service import InventoryDomainService


@pytest.fixture(autouse=True)
def set_test_environment(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")


@asynccontextmanager
async def create_inventory_test_agent(seed_items=True):
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_inv.db")
    if seed_items:
        seed_database(db_path, force=True)
    else:
        # Create empty DB with schema only
        run_migrations(db_path)

    repo = BizOpsRepository(db_path=db_path)
    safety_gw = SafetyGateway(repo=repo)
    agent = BizOpsAgent(
        user_id=DEFAULT_USER_ID,
        user_role=DEFAULT_USER_ROLE,
        auto_confirm_writes=True,
        safety_gateway=safety_gw,
        verbose=False,
    )
    # Offline deterministic execution for unit testing
    agent.llm.is_available = AsyncMock(return_value=False)
    await agent.initialize()
    try:
        yield agent, repo
    finally:
        await agent.close()
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_low_stock_returns_all_eight_items():
    """
    Verifies that low stock query returns all 8 items whose available stock is positive
    and at or below the reorder threshold.
    Expected product IDs: P1002, P1003, P1004, P1007, P1008, P1010, P1014, P1015.
    (P1001 has available_qty == 0, classified as OUT_OF_STOCK).
    """
    async with create_inventory_test_agent(seed_items=True) as (agent, repo):
        # Verify domain service directly has exactly 8 items classified as LOW_STOCK
        inv_service = InventoryDomainService(repo=repo)
        low_res = inv_service.get_low_stock_items()
        assert low_res["total_count"] == 8
        expected_ids = {"P1002", "P1003", "P1004", "P1007", "P1008", "P1010", "P1014", "P1015"}
        assert set(item["product_id"] for item in low_res["items"]) == expected_ids

        # Query agent
        response = await agent.run_query("Which items are low in stock?")
        assert response is not None
        assert "8 products" in response or "Low Stock" in response or "LOW_STOCK" in response

        # Verify all 8 product IDs appear in agent response
        for pid in expected_ids:
            assert pid in response, f"Missing product {pid} in agent response"


@pytest.mark.asyncio
async def test_low_stock_excludes_products_above_threshold():
    """
    Verifies that products with healthy stock (above reorder threshold) are not returned
    in low-stock advice or listing.
    Example: P1006 (Parle-style Glucose: on-hand 200, reorder_threshold 50) -> healthy stock.
    """
    async with create_inventory_test_agent(seed_items=True) as (agent, repo):
        response = await agent.run_query("Which items are low in stock?")
        # Healthy items like P1006, P1009, P1011, P1013 must not appear in the low stock list
        assert "P1006" not in response
        assert "P1011" not in response
        assert "P1013" not in response


@pytest.mark.asyncio
async def test_low_stock_empty_inventory_handled_gracefully():
    """
    Verifies that an empty inventory or inventory with no low stock items
    returns a clear, graceful informative response instead of an error.
    """
    async with create_inventory_test_agent(seed_items=False) as (agent, repo):
        response = await agent.run_query("Which items are low in stock?")
        assert response is not None
        resp_lower = response.lower()
        assert any(phrase in resp_lower for phrase in ["no items", "healthy", "sufficient", "normal", "0 products", "zero"])


@pytest.mark.asyncio
async def test_low_stock_markdown_table_formatting():
    """
    Verifies that the low-stock advice response formats items cleanly as a Markdown table.
    """
    async with create_inventory_test_agent(seed_items=True) as (agent, repo):
        response = await agent.run_query("Which items are low in stock?")
        assert "|" in response
        assert "Product ID" in response or "Product" in response
        assert "Available Qty" in response or "Reorder" in response
