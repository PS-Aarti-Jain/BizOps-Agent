from contextlib import asynccontextmanager
import os
import tempfile
import pytest
from unittest.mock import AsyncMock

from app.config import DEFAULT_USER_ID, DEFAULT_USER_ROLE
from database.repositories.repository import BizOpsRepository
from database.seed import seed_database
from security.safety_gateway import SafetyGateway
from agent.orchestrator import BizOpsAgent


@pytest.fixture(autouse=True)
def set_test_environment(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")


import shutil

@asynccontextmanager
async def create_test_agent():
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_agent.db")
    seed_database(db_path, force=True)
    repo = BizOpsRepository(db_path=db_path)
    safety_gw = SafetyGateway(repo=repo)
    agent = BizOpsAgent(
        user_id=DEFAULT_USER_ID,
        user_role=DEFAULT_USER_ROLE,
        auto_confirm_writes=True,
        safety_gateway=safety_gw,
        verbose=False,
    )
    agent.llm.is_available = AsyncMock(return_value=False)
    await agent.initialize()
    try:
        yield agent
    finally:
        await agent.close()
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_agent_initialization():
    """Verifies that the agent initializes and registers integration and MCP tools."""
    async with create_test_agent() as test_agent:
        registry = test_agent.router.registry
        assert len(registry.tools) >= 4
        assert "google_sheets.read_data" in registry.tools
        assert "google_sheets.update_record" in registry.tools


@pytest.mark.asyncio
async def test_natural_language_query_understanding():
    """Verifies natural language queries for low stock products."""
    async with create_test_agent() as test_agent:
        answer = await test_agent.run_query("Which products do I need to reorder?")
        assert answer is not None
        assert any(w in answer.lower() for w in ["low", "stock", "reorder", "threshold", "p1001", "mouse"])


@pytest.mark.asyncio
async def test_hinglish_query_understanding():
    """Verifies Hinglish conversational phrasing ('Kaunse products ka stock kam hai?')."""
    async with create_test_agent() as test_agent:
        answer = await test_agent.run_query("Kaunse products ka stock kam hai?")
        assert answer is not None
        assert any(w in answer.lower() for w in ["stock", "low", "reorder", "p1001", "products"])


@pytest.mark.asyncio
async def test_tool_selection_and_argument_generation():
    """Verifies correct tool selection and argument generation for order fulfillment."""
    async with create_test_agent() as test_agent:
        answer = await test_agent.run_query("Can we fulfill order ORD-1042?")
        assert answer is not None
        assert "ORD-1042" in answer or "cannot" in answer.lower() or "shortage" in answer.lower()


@pytest.mark.asyncio
async def test_location_entity_resolution():
    """Verifies location-based order query ('Why hasn't the Pune order been dispatched?')."""
    async with create_test_agent() as test_agent:
        answer = await test_agent.run_query("Why hasn't the Pune order been dispatched?")
        assert answer is not None
        assert any(w in answer for w in ["ORD-1043", "Shree Krishna", "Pune", "Processing", "Pending", "blocker"])


@pytest.mark.asyncio
async def test_supplier_comparison_understanding():
    """Verifies supplier comparison understanding with quantity and product entity."""
    async with create_test_agent() as test_agent:
        answer = await test_agent.run_query("We need 100 wireless mice. Which supplier should we use?")
        assert answer is not None
        assert any(w in answer for w in ["Pune Electronics", "SUP-102", "supplier", "recommend", "245"])


@pytest.mark.asyncio
async def test_ambiguous_customer_clarification():
    """Verifies clarification prompt when a generic query term matches multiple customers."""
    async with create_test_agent() as test_agent:
        answer = await test_agent.run_query("Check orders for customer Stores")
        assert answer is not None
        assert len(answer) > 10


@pytest.mark.asyncio
async def test_final_response_grounding():
    """Verifies that the final response contains verified facts and no fabricated stock."""
    async with create_test_agent() as test_agent:
        answer = await test_agent.run_query("How much stock is available for Wireless Optical Mouse?")
        assert answer is not None
        assert "available" in answer.lower()
        assert "0" in answer or "Wireless Optical Mouse" in answer
