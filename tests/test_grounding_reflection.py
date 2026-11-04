import os
import shutil
import tempfile
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch
import pytest

from app.config import DEFAULT_USER_ID, DEFAULT_USER_ROLE
from database.repositories.repository import BizOpsRepository
from database.seed import seed_database
from security.safety_gateway import SafetyGateway
from agent.agent import BizOpsAgent
from agent.react_loop import ExecutionResult


@pytest.fixture(autouse=True)
def set_test_environment(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")


@asynccontextmanager
async def create_test_agent():
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_gr.db")
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
        yield agent, repo
    finally:
        await agent.close()
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_incorrect_stock_quantity_rejected_and_repaired():
    """
    Verifies that if an answer claims an ungrounded stock quantity (e.g. 999 units for P1001 when obs has 0),
    the validation gate intercepts it, rejects the 999 claim, and repairs it with authoritative observation data.
    """
    async with create_test_agent() as (agent, repo):
        # Mock react_engine.execute to simulate a hallucinating model
        mock_transcript = [{
            "iteration": 1,
            "action": "inventory.get_stock",
            "args": {"product_id": "P1001"},
            "observation": {
                "status": "SUCCESS",
                "data": {
                    "product_id": "P1001",
                    "product_name": "Wireless Optical Mouse (USB)",
                    "available_qty": 0,
                    "on_hand_qty": 0,
                    "reserved_qty": 0,
                    "allocated_qty": 0,
                    "reorder_threshold": 25,
                    "location": "Mumbai Central WH-A",
                    "classification": "OUT_OF_STOCK",
                },
            },
        }]
        hallucinated_answer = (
            "**Answer**: P1001 Wireless Optical Mouse has 999 units available in stock.\n\n"
            "**Evidence**: Verified stock level.\n\n"
            "**Status**: IN_STOCK"
        )
        fake_result = ExecutionResult(
            is_final=True,
            final_answer=hallucinated_answer,
            transcript=mock_transcript,
            used_fallback=False,
            llm_display_label="LLM: groq/qwen",
            elapsed_seconds=0.5,
            status="SUCCESS",
        )

        with patch.object(agent.react_engine, "execute", return_value=fake_result):
            response = await agent.run_query("Check stock for P1001")

            # Must NEVER return the 999 hallucinated units
            assert "999" not in response
            # Must reflect verified 0 units or OUT_OF_STOCK from authoritative repair
            assert "0" in response or "OUT_OF_STOCK" in response or "UNVERIFIED_DATA_REFUSAL" in response


@pytest.mark.asyncio
async def test_invented_order_identifier_rejected():
    """
    Verifies that an invented order ID (e.g. ORD-9999) not present in observations or user query
    is rejected and not returned in the user-facing response.
    """
    async with create_test_agent() as (agent, repo):
        mock_transcript = [{
            "iteration": 1,
            "action": "orders.pending_orders",
            "args": {},
            "observation": {
                "status": "SUCCESS",
                "count": 1,
                "orders": [{"order_id": "ORD-1042", "status": "Pending", "total_amount": 1200.0}],
            },
        }]
        hallucinated_answer = (
            "**Answer**: The active order is ORD-9999 for Customer Om Sai.\n\n"
            "**Evidence**: Order details for ORD-9999.\n\n"
            "**Status**: SUCCESS"
        )
        fake_result = ExecutionResult(
            is_final=True,
            final_answer=hallucinated_answer,
            transcript=mock_transcript,
            used_fallback=False,
            llm_display_label="LLM: groq/qwen",
            elapsed_seconds=0.5,
            status="SUCCESS",
        )

        with patch.object(agent.react_engine, "execute", return_value=fake_result):
            response = await agent.run_query("What orders are pending?")

            # The invented ORD-9999 must NOT be returned to user
            assert "ORD-9999" not in response


@pytest.mark.asyncio
async def test_unsupported_financial_totals_rejected():
    """
    Verifies that an unsupported financial total (e.g. ₹9,999,999.00) not present
    in tool observations is rejected.
    """
    async with create_test_agent() as (agent, repo):
        mock_transcript = [{
            "iteration": 1,
            "action": "finance.financial_summary",
            "args": {"month": "2026-10"},
            "observation": {
                "status": "SUCCESS",
                "total_invoiced": 56350.0,
                "total_collected": 43650.0,
                "total_outstanding": 12700.0,
            },
        }]
        hallucinated_answer = (
            "**Answer**: Total invoiced revenue was ₹9,999,999.00 for October.\n\n"
            "**Status**: SUCCESS"
        )
        fake_result = ExecutionResult(
            is_final=True,
            final_answer=hallucinated_answer,
            transcript=mock_transcript,
            used_fallback=False,
            llm_display_label="LLM: groq/qwen",
            elapsed_seconds=0.5,
            status="SUCCESS",
        )

        with patch.object(agent.react_engine, "execute", return_value=fake_result):
            response = await agent.run_query("What is our revenue this month?")

            assert "9,999,999" not in response


@pytest.mark.asyncio
async def test_correct_tool_grounded_answer_passes():
    """
    Verifies that a genuine, grounded answer with correct IDs and numbers passes
    validation seamlessly.
    """
    async with create_test_agent() as (agent, repo):
        mock_transcript = [{
            "iteration": 1,
            "action": "inventory.get_stock",
            "args": {"product_id": "P1002"},
            "observation": {
                "status": "SUCCESS",
                "data": {
                    "product_id": "P1002",
                    "product_name": "Mechanical Gaming Keyboard",
                    "available_qty": 5,
                    "on_hand_qty": 5,
                    "reserved_qty": 0,
                    "allocated_qty": 0,
                    "reorder_threshold": 15,
                    "location": "Mumbai Central WH-A",
                    "classification": "LOW_STOCK",
                },
            },
        }]
        grounded_answer = (
            "**Answer**: There are currently **5 units** available for **Mechanical Gaming Keyboard** at Mumbai Central WH-A.\n\n"
            "**Evidence**: Product: Mechanical Gaming Keyboard (P1002), Available: 5 units.\n\n"
            "**Status**: LOW_STOCK_ALERT"
        )
        fake_result = ExecutionResult(
            is_final=True,
            final_answer=grounded_answer,
            transcript=mock_transcript,
            used_fallback=False,
            llm_display_label="LLM: groq/qwen",
            elapsed_seconds=0.5,
            status="SUCCESS",
        )

        with patch.object(agent.react_engine, "execute", return_value=fake_result):
            response = await agent.run_query("Check stock for P1002")

            assert "P1002" in response
            assert "5 units" in response
            assert "LOW_STOCK" in response
            assert agent.last_grounding["is_grounded"] is True
            assert agent.last_reflection.outcome == "SUCCESS"


@pytest.mark.asyncio
async def test_failed_validation_terminates_safely_when_unrepairable():
    """
    Verifies that when an answer fails validation and tool observations are empty/failed
    such that repair cannot succeed, it returns UNVERIFIED_DATA_REFUSAL without crashing.
    """
    async with create_test_agent() as (agent, repo):
        # Tool execution failed entirely
        mock_transcript = [{
            "iteration": 1,
            "action": "inventory.get_stock",
            "args": {"product_id": "P1099"},
            "observation": {
                "status": "SERVICE_FAILURE",
                "message": "Database disk image is malformed",
            },
        }]
        hallucinated_answer = (
            "**Answer**: P1099 is in stock with 50 units.\n\n**Status**: SUCCESS"
        )
        fake_result = ExecutionResult(
            is_final=True,
            final_answer=hallucinated_answer,
            transcript=mock_transcript,
            used_fallback=False,
            llm_display_label="LLM: groq/qwen",
            elapsed_seconds=0.5,
            status="SUCCESS",
        )

        with patch.object(agent.react_engine, "execute", return_value=fake_result):
            response = await agent.run_query("Check stock for P1099")

            # Must safely refuse rather than claim 50 units in stock
            assert "50 units" not in response
            assert "UNVERIFIED_DATA_REFUSAL" in response
            assert agent.last_grounding["is_grounded"] is False
            assert agent.last_reflection.outcome == "FAILED"
