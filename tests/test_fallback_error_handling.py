import pytest
from unittest.mock import AsyncMock, patch

from agent.react_loop import DeterministicFallbackEngine, ReActAction
from agent.response_formatter import ResponseFormatter
from domain.entity_resolver import EntityResolver, EntityResolutionResult, EntityMatch
from mcp.router import ToolRegistry
from database.models import DiscoveredTool, ToolMetadata


def test_fallback_engine_get_stock_uses_product_id():
    """
    Verifies that DeterministicFallbackEngine invokes inventory.get_stock
    with schema-compliant argument {'product_id': ...} rather than 'product_id_or_name'.
    """
    registry = ToolRegistry()
    meta = ToolMetadata(
        domain="inventory",
        operation_type="read",
        risk_level="none",
        requires_confirmation=False,
        required_role="Operator",
        idempotent=True,
    )
    tool = DiscoveredTool(
        name="get_stock",
        description="Get stock info",
        input_schema={"type": "object", "properties": {"product_id": {"type": "string"}}, "required": ["product_id"]},
        owning_server="inventory",
        metadata=meta,
    )
    registry.register(tool)

    resolver = EntityResolver()
    entity_res = resolver.resolve_query("Check stock for P1001")
    transcript = []

    action = DeterministicFallbackEngine.determine_next_action(
        user_query="Check stock for P1001",
        registry=registry,
        transcript=transcript,
        entity_res=entity_res,
    )

    assert isinstance(action, ReActAction)
    assert action.action == "inventory.get_stock"
    assert "product_id" in action.args
    assert action.args["product_id"] == "P1001"
    assert "product_id_or_name" not in action.args


def test_tool_execution_error_does_not_fabricate_zero_stock():
    """
    Verifies that a tool error (SERVICE_FAILURE, EXECUTION_ERROR, TIMEOUT)
    returns TOOL_EXECUTION_ERROR and does not fabricate '0 units available' or 'OUT_OF_STOCK'.
    """
    error_obs = {
        "inventory.get_stock": {
            "status": "SERVICE_FAILURE",
            "message": "Database connection pool exhausted.",
        }
    }
    resolver = EntityResolver()
    entity_res = resolver.resolve_query("Check stock for P1001")

    formatted = ResponseFormatter.format_business_response(
        query="Check stock for P1001",
        observations=error_obs,
        entity_res=entity_res,
    )

    assert "**Status**: TOOL_EXECUTION_ERROR" in formatted
    assert "Database connection pool exhausted." in formatted
    assert "completely OUT OF STOCK" not in formatted
    assert "0 units available" not in formatted


def test_missing_product_returns_product_not_found_not_zero_stock():
    """
    Verifies that a missing product (NO_DATA) returns PRODUCT_NOT_FOUND,
    not fabricated zero stock or OUT_OF_STOCK.
    """
    no_data_obs = {
        "inventory.get_stock": {
            "status": "NO_DATA",
            "message": "No product found matching 'P9999'",
        }
    }

    formatted = ResponseFormatter.format_business_response(
        query="Check stock for P9999",
        observations=no_data_obs,
        entity_res=None,
    )

    assert "**Status**: PRODUCT_NOT_FOUND" in formatted
    assert "Product could not be found" in formatted
    assert "completely OUT OF STOCK" not in formatted


def test_genuine_zero_stock_correctly_classified():
    """
    Verifies that genuine zero stock from a SUCCESS tool observation
    is properly formatted as OUT_OF_STOCK.
    """
    success_zero_obs = {
        "inventory.get_stock": {
            "status": "SUCCESS",
            "data": {
                "product_id": "P1001",
                "product_name": "Wireless Optical Mouse",
                "sku": "SKU-P1001",
                "on_hand_qty": 0,
                "reserved_qty": 0,
                "allocated_qty": 0,
                "available_qty": 0,
                "reorder_threshold": 25,
                "is_low_stock": False,
                "is_out_of_stock": True,
                "classification": "OUT_OF_STOCK",
                "location": "Warehouse A",
            },
        }
    }

    formatted = ResponseFormatter.format_business_response(
        query="Check stock for P1001",
        observations=success_zero_obs,
        entity_res=None,
    )

    assert "**Status**: OUT_OF_STOCK" in formatted
    assert "**completely OUT OF STOCK** (0 units available)" in formatted
    assert "Warehouse A" in formatted


def test_financial_summary_error_does_not_fabricate_zero_revenue():
    """
    Verifies that a failed finance tool call does not report ₹0.00 invoiced revenue
    or 'no invoices recorded'.
    """
    err_obs = {
        "finance.financial_summary": {
            "status": "TIMEOUT",
            "message": "Query timed out after 30 seconds",
        }
    }

    formatted = ResponseFormatter.format_business_response(
        query="What is our revenue this month?",
        observations=err_obs,
        entity_res=None,
    )

    assert "**Status**: TOOL_EXECUTION_ERROR" in formatted
    assert "Query timed out after 30 seconds" in formatted
    assert "₹0.00" not in formatted
    assert "no invoices or sales transactions were recorded" not in formatted


def test_pending_orders_error_does_not_fabricate_zero_orders():
    """
    Verifies that a failed orders tool call does not report 'There were 0 pending orders'.
    """
    err_obs = {
        "orders.pending_orders": {
            "status": "SERVICE_FAILURE",
            "message": "Orders MCP server crashed.",
        }
    }

    formatted = ResponseFormatter.format_business_response(
        query="How many pending orders do we have?",
        observations=err_obs,
        entity_res=None,
    )

    assert "**Status**: TOOL_EXECUTION_ERROR" in formatted
    assert "Orders MCP server crashed." in formatted
    assert "There were 0 pending orders" not in formatted
    assert "0 pending orders" not in formatted


def test_pending_payments_error_does_not_fabricate_all_accounts_settled():
    """
    Verifies that a failed payment status tool call does not report 'All customer accounts are fully settled'.
    """
    err_obs = {
        "finance.get_payment_status": {
            "status": "EXECUTION_ERROR",
            "message": "Database disk I/O error.",
        }
    }

    formatted = ResponseFormatter.format_business_response(
        query="Who owes us money?",
        observations=err_obs,
        entity_res=None,
    )

    assert "**Status**: TOOL_EXECUTION_ERROR" in formatted
    assert "Database disk I/O error." in formatted
    assert "All customer accounts are fully settled" not in formatted
