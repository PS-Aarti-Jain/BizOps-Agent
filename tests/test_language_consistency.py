"""
Regression test suite for Response Language Consistency and Hinglish Leakage.

Validates:
1. Language policy defaults to English and preserves active language across follow-ups.
2. Typos ('maonth', 'mont', 'pendng') do not switch language and are normalized.
3. Minor Hinglish particles (e.g. 'Aur low stock products?') in an English session do not switch language.
4. Explicit Hindi/Hinglish requests ('Pichle mahine ka revenue batao') are respected.
5. Clarification for unspecified product stock is asked in English.
6. Separation of language policy from business logic, tool execution, and date resolution.
"""

import pytest
from services.language_policy import LanguagePolicy
from services.query_normalizer import normalize_query_typos
from services.date_resolver import RelativeDateResolver
from agent.memory import ConversationMemory
from database.repositories.repository import BizOpsRepository
from services.order_service import OrderDomainService
from domain.entity_resolver import EntityResolver
from agent.react_loop import DeterministicFallbackEngine
from agent.response_formatter import ResponseFormatter
from mcp.router import ToolRegistry
from database.models import DiscoveredTool, ToolMetadata
from database.seed import seed_database
import os
import tempfile


@pytest.fixture
def test_repo():
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_lang.db")
    seed_database(db_path, force=True)
    return BizOpsRepository(db_path=db_path)


# --- Unit Tests: Language Policy & Typo Normalizer ---

def test_language_policy_defaults_to_english():
    assert LanguagePolicy.detect_language("What is our revenue?") == "en"
    assert LanguagePolicy.detect_language("Show available inventory.") == "en"
    assert LanguagePolicy.detect_language("How many pending orders?") == "en"


def test_language_policy_preserves_english_across_follow_ups_and_typos():
    session_lang = "en"
    assert LanguagePolicy.detect_language("Last month?", current_session_lang=session_lang) == "en"
    assert LanguagePolicy.detect_language("Last maonth?", current_session_lang=session_lang) == "en"
    assert LanguagePolicy.detect_language("revenue last mont", current_session_lang=session_lang) == "en"
    assert LanguagePolicy.detect_language("Aur low stock products?", current_session_lang=session_lang) == "en"


def test_language_policy_detects_explicit_hinglish_and_hindi():
    assert LanguagePolicy.detect_language("Pichle mahine ka revenue batao.") == "hinglish"
    assert LanguagePolicy.detect_language("Kaunse products ka stock kam hai?") == "hinglish"
    assert LanguagePolicy.detect_language("पिछले महीने का रेवेन्यू बताओ") == "hi"


def test_query_normalizer_fixes_business_typos():
    assert normalize_query_typos("last maonth?") == "last month?"
    assert normalize_query_typos("revenue last mont") == "revenue last month"
    assert normalize_query_typos("pendng orders") == "pending orders"
    assert normalize_query_typos("invetory status") == "inventory status"
    assert normalize_query_typos("suplier ranking") == "supplier ranking"


# --- Context & Follow-Up Understanding Tests ---

def test_pending_orders_follow_up_with_typo():
    memory = ConversationMemory()
    session_id = "test-session-orders"

    # Turn 1: User asks for pending orders
    memory.add_turn(
        session_id=session_id,
        query="How many pending orders?",
        answer="There are 5 pending orders in total.",
        intent="pending_orders",
        tool="orders.pending_orders",
    )

    # Turn 2: User asks follow-up with typo 'Last maonth?'
    resolved, params = memory.resolve_follow_up(session_id, "Last maonth?")
    assert "pending orders" in resolved.lower()
    assert "last month" in resolved.lower()
    assert params is not None
    assert params["retained_intent"] == "pending_orders"
    assert params["period"] == "last month"


def test_revenue_follow_up_with_last_month():
    memory = ConversationMemory()
    session_id = "test-session-rev"

    # Turn 1: User asks for revenue
    memory.add_turn(
        session_id=session_id,
        query="What is our revenue?",
        answer="Invoiced revenue for October 2026 is ₹1,56,350.",
        intent="financial_summary",
        tool="finance.financial_summary",
    )

    # Turn 2: User asks 'Last month?'
    resolved, params = memory.resolve_follow_up(session_id, "Last month?")
    assert "revenue" in resolved.lower()
    assert "last month" in resolved.lower()
    assert params is not None
    assert params["retained_intent"] == "financial_summary"
    assert params["period"] == "last month"


def test_order_service_pending_orders_date_filtering(test_repo):
    service = OrderDomainService(repo=test_repo)

    # All pending orders
    all_res = service.get_pending_orders()
    assert all_res["status"] == "SUCCESS"
    assert all_res["count"] >= 1

    # Filtered for September 2026 (last month)
    sep_res = service.get_pending_orders(month="2026-09")
    assert sep_res["status"] == "EMPTY_RESULT"
    assert sep_res["count"] == 0

    # Filtered for October 2026 (this month)
    oct_res = service.get_pending_orders(month="2026-10")
    assert oct_res["status"] == "SUCCESS"
    assert oct_res["count"] >= 1


def test_unspecified_product_stock_returns_english_clarification(test_repo):
    resolver = EntityResolver(repo=test_repo)
    entity_res = resolver.resolve_query("How much stock do we have?")
    assert entity_res.product is None

    registry = ToolRegistry()
    registry.register(
        DiscoveredTool(
            name="get_stock",
            description="Checks stock",
            input_schema={"properties": {"product_id_or_name": {"type": "string"}}},
            owning_server="inventory",
            metadata=ToolMetadata(
                domain="inventory",
                operation_type="read",
                risk_level="none",
                required_role="Operator",
            ),
        )
    )

    action = DeterministicFallbackEngine.determine_next_action(
        user_query="How much stock do we have?",
        registry=registry,
        transcript=[],
        entity_res=entity_res,
    )

    assert action.action == "final"
    assert action.answer is not None
    # Clarification must be in English asking for the product
    assert "which product" in action.answer.lower()
    assert "NEEDS_CLARIFICATION" in action.answer
    assert not any(w in action.answer for w in ["Aapka", "baare", "thoda", "kya jaanna"])


def test_response_formatter_pending_orders_english():
    obs = {
        "orders.pending_orders": {
            "status": "SUCCESS",
            "count": 5,
            "orders": [
                {"order_id": "ORD-1048", "order_date": "2026-10-30", "total_amount": 5500.0, "status": "Pending", "customer_id": "CUST-509"}
            ]
        }
    }
    formatted = ResponseFormatter.format_business_response("How many pending orders?", obs)
    assert "**Answer**:" in formatted
    assert "5 pending orders" in formatted
    assert "ORD-1048" in formatted
    assert "₹5,500.00" in formatted
    assert not any(w in formatted for w in ["Aapka", "baare", "thoda"])


def test_response_formatter_last_month_empty_pending_orders():
    obs = {
        "orders.pending_orders": {
            "status": "EMPTY_RESULT",
            "count": 0,
            "orders": [],
            "month": "2026-09"
        }
    }
    formatted = ResponseFormatter.format_business_response("How many pending orders last month?", obs)
    assert "**Answer**:" in formatted
    assert "0 pending orders" in formatted
    assert "October 2026" in formatted
    assert not any(w in formatted for w in ["Aapka", "baare", "thoda"])
