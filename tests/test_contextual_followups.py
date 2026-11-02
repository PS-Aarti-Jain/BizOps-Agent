import datetime
import os
import shutil
import tempfile
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
import pytest

from app.config import DEFAULT_USER_ID, DEFAULT_USER_ROLE
from database.repositories.repository import BizOpsRepository
from database.seed import seed_database
from security.safety_gateway import SafetyGateway
from agent.agent import BizOpsAgent
from agent.domain_guard import OUT_OF_DOMAIN_REJECTION


@pytest.fixture(autouse=True)
def set_test_environment(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")


@asynccontextmanager
async def create_followup_test_agent(user_role=DEFAULT_USER_ROLE, auto_confirm=True):
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_followups.db")
    seed_database(db_path, force=True)
    repo = BizOpsRepository(db_path=db_path)
    safety_gw = SafetyGateway(repo=repo)
    agent = BizOpsAgent(
        user_id=DEFAULT_USER_ID,
        user_role=user_role,
        auto_confirm_writes=auto_confirm,
        safety_gateway=safety_gw,
        verbose=False,
    )
    # Unit tests run with deterministic offline fallback to guarantee repeatability
    agent.llm.is_available = AsyncMock(return_value=False)
    await agent.initialize()
    try:
        yield agent, repo
    finally:
        await agent.close()
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_followup_month_before_last_revenue():
    """
    Verifies the sequence:
    User: 'what is last month revenue?' -> October 2026 revenue.
    User: 'month before last?' -> September 2026 revenue (₹77,000.00 invoiced, INV-2001, INV-2006).
    """
    async with create_followup_test_agent() as (agent, repo):
        session_id = "test-sess-mbl"
        # Turn 1
        r1 = await agent.run_query("what is last month revenue?", session_id=session_id)
        assert r1 is not None
        assert OUT_OF_DOMAIN_REJECTION not in r1
        assert "October" in r1 or "10-2026" in r1 or "56,350" in r1

        # Turn 2: 'month before last?'
        r2 = await agent.run_query("month before last?", session_id=session_id)
        assert r2 is not None
        assert OUT_OF_DOMAIN_REJECTION not in r2
        assert "September" in r2 or "09-2026" in r2 or "77,000" in r2


@pytest.mark.asyncio
async def test_followup_what_about_september():
    """
    Verifies contextual follow-up with a named month:
    User: 'what is last month revenue?'
    User: 'what about September?' -> Returns September 2026 revenue.
    """
    async with create_followup_test_agent() as (agent, repo):
        session_id = "test-sess-sep"
        await agent.run_query("what is last month revenue?", session_id=session_id)
        r2 = await agent.run_query("what about September?", session_id=session_id)

        assert r2 is not None
        assert OUT_OF_DOMAIN_REJECTION not in r2
        assert "September" in r2 or "09-2026" in r2 or "77,000" in r2


@pytest.mark.asyncio
async def test_followup_and_the_previous_month():
    """
    Verifies contextual follow-up with relative shift 'and the previous month?':
    User: 'what is last month revenue?'
    User: 'and the previous month?' -> Returns September 2026 revenue.
    """
    async with create_followup_test_agent() as (agent, repo):
        session_id = "test-sess-prev"
        await agent.run_query("what is last month revenue?", session_id=session_id)
        r2 = await agent.run_query("and the previous month?", session_id=session_id)

        assert r2 is not None
        assert OUT_OF_DOMAIN_REJECTION not in r2
        assert "September" in r2 or "09-2026" in r2 or "77,000" in r2


@pytest.mark.asyncio
async def test_followup_january_year_boundary():
    """
    Verifies date resolver year-boundary arithmetic:
    In January 2027 context:
    - 'last month' -> December 2026
    - 'month before last' -> November 2026
    """
    async with create_followup_test_agent() as (agent, repo):
        session_id = "test-sess-jan"
        jan_date = datetime.date(2027, 1, 15)

        # Set reference date on agent and run turn 1
        agent.ref_date = jan_date
        r1 = await agent.run_query("what is last month revenue?", session_id=session_id, ref_date=jan_date)
        assert r1 is not None

        # Turn 2: 'month before last?' should resolve to November 2026
        resolved, params = agent.memory.resolve_follow_up(session_id, "month before last?", ref_date=jan_date)
        assert "November 2026" in resolved or "2026-11-01" in str(params)


@pytest.mark.asyncio
async def test_standalone_ood_query_rejected():
    """
    Verifies that a standalone out-of-domain query ('What is 9 + 10?') is rejected
    immediately by DomainGuard without calling MCP tools or processing memory.
    """
    async with create_followup_test_agent() as (agent, repo):
        session_id = "test-sess-ood"
        r = await agent.run_query("What is 9 + 10?", session_id=session_id)
        assert OUT_OF_DOMAIN_REJECTION in r


@pytest.mark.asyncio
async def test_ambiguous_followup_triggers_clarification():
    """
    Verifies that vague follow-ups ('what about it?', 'tell me more') after a query
    prompt the user for clarification rather than crashing or hallucinating.
    """
    async with create_followup_test_agent() as (agent, repo):
        session_id = "test-sess-amb"
        await agent.run_query("what is last month revenue?", session_id=session_id)
        r2 = await agent.run_query("what about it?", session_id=session_id)

        assert r2 is not None
        assert "clarify" in r2.lower() or "what specific" in r2.lower()


@pytest.mark.asyncio
async def test_session_isolation():
    """
    Verifies that conversation memory and follow-up resolution are strictly isolated per session.
    Session A: 'what is last month revenue?'
    Session B: 'month before last?' -> Session B must NOT inherit Session A's context,
    and should be rejected as an out-of-domain query.
    """
    async with create_followup_test_agent() as (agent, repo):
        sess_a = "session-A-101"
        sess_b = "session-B-202"

        # Session A establishes context
        await agent.run_query("what is last month revenue?", session_id=sess_a)

        # Session B starts cold with follow-up phrasing
        r_b = await agent.run_query("month before last?", session_id=sess_b)
        assert OUT_OF_DOMAIN_REJECTION in r_b


@pytest.mark.asyncio
async def test_confirmation_followup_yes_need_all():
    """
    Verifies Issue Three confirmation handling:
    User: 'Which items are low in stock?' -> Returns low-stock items.
    User: 'Yes, I need all.' -> Resolves to retrieving and displaying all 8 low-stock items.
    """
    async with create_followup_test_agent() as (agent, repo):
        session_id = "test-sess-need-all"
        r1 = await agent.run_query("Which items are low in stock?", session_id=session_id)
        assert r1 is not None

        r2 = await agent.run_query("Yes, I need all.", session_id=session_id)
        assert r2 is not None
        assert OUT_OF_DOMAIN_REJECTION not in r2

        # Check that all 8 low stock products are present
        expected_ids = ["P1002", "P1003", "P1004", "P1007", "P1008", "P1010", "P1014", "P1015"]
        for pid in expected_ids:
            assert pid in r2, f"Expected {pid} in confirmation response"


@pytest.mark.asyncio
async def test_confirmation_missing_details_prompts_clarification():
    """
    Verifies that attempting to create a purchase order without necessary parameters
    (product, supplier, quantity) prompts for clarification instead of creating an empty or corrupted PO.
    """
    async with create_followup_test_agent() as (agent, repo):
        session_id = "test-sess-po-missing"
        r = await agent.run_query("Create a purchase order", session_id=session_id)
        assert r is not None
        # Must request details or state missing information
        r_lower = r.lower()
        assert any(term in r_lower for term in ["specify", "supplier", "product", "quantity", "clarify", "details"])


@pytest.mark.asyncio
async def test_safety_gateway_rbac_enforcement_on_confirmation():
    """
    Verifies that a user with 'viewer' role is forbidden from executing write actions
    even if attempting to confirm.
    """
    async with create_followup_test_agent(user_role="viewer") as (agent, repo):
        session_id = "test-sess-viewer"
        # Attempt to confirm or trigger purchase order write
        r = await agent.run_query("Create a purchase order for P1002 with 50 units from SUP-102", session_id=session_id)
        assert r is not None
        # Safety gateway must enforce RBAC
        r_lower = r.lower()
        assert any(term in r_lower for term in ["permission", "denied", "forbidden", "viewer", "unauthorized", "cannot create"])

