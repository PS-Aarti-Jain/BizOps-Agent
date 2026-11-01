import pytest
from unittest.mock import MagicMock

from agent.react_loop import DeterministicFallbackEngine
from domain.entity_resolver import EntityResolver
from mcp.router import ToolRegistry


@pytest.fixture
def registry():
    reg = ToolRegistry()
    from database.models import DiscoveredTool, ToolMetadata
    reg.register(
        DiscoveredTool(
            name="create_purchase_order",
            description="Creates purchase order",
            input_schema={},
            owning_server="suppliers",
            metadata=ToolMetadata(
                domain="suppliers",
                operation_type="write",
                risk_level="high",
                requires_confirmation=True,
                required_role="Manager",
            ),
        )
    )
    reg.register(
        DiscoveredTool(
            name="compare_suppliers",
            description="Compares suppliers",
            input_schema={},
            owning_server="suppliers",
            metadata=ToolMetadata(
                domain="suppliers",
                operation_type="read",
                risk_level="none",
                requires_confirmation=False,
                required_role="Operator",
            ),
        )
    )
    return reg


def test_missing_product_requires_clarification(registry):
    """Verifies that asking to create a PO without a product prompts for clarification."""
    resolver = EntityResolver()

    query = "Create a purchase order for 50 units"
    entity_res = resolver.resolve_query(query)

    action = DeterministicFallbackEngine.determine_next_action(
        user_query=query,
        registry=registry,
        transcript=[],
        entity_res=entity_res,
    )
    assert action.action == "final"
    assert "clarification needed" in action.answer.lower()
    assert "which product" in action.answer.lower()


def test_missing_quantity_requires_clarification(registry):
    """Verifies that asking to create a PO without a quantity prompts for clarification."""
    resolver = EntityResolver()

    query = "Create a purchase order for Wireless Mouse"
    entity_res = resolver.resolve_query(query)

    action = DeterministicFallbackEngine.determine_next_action(
        user_query=query,
        registry=registry,
        transcript=[],
        entity_res=entity_res,
    )
    assert action.action == "final"
    assert "clarification needed" in action.answer.lower()
    assert "specify the quantity" in action.answer.lower()


def test_unspecified_supplier_calls_compare_suppliers(registry):
    """Verifies that when product and quantity are given but supplier is not, compare_suppliers is called."""
    resolver = EntityResolver()

    query = "Create a purchase order for 50 units of Wireless Mouse"
    entity_res = resolver.resolve_query(query)

    action = DeterministicFallbackEngine.determine_next_action(
        user_query=query,
        registry=registry,
        transcript=[],
        entity_res=entity_res,
    )
    assert action.action == "suppliers.compare_suppliers"
    assert action.args["product_id_or_name"] == "P1001"
    assert action.args["required_quantity"] == 50


def test_compare_suppliers_result_used_deterministically(registry):
    """Verifies that after compare_suppliers runs, the top supplier and real unit price are used."""
    resolver = EntityResolver()

    query = "Create a purchase order for 50 units of Wireless Mouse"
    entity_res = resolver.resolve_query(query)

    # Simulated compare_suppliers observation
    transcript = [
        {
            "action": "suppliers.compare_suppliers",
            "args": {"product_id_or_name": "P1001", "required_quantity": 50},
            "observation": {
                "status": "SUCCESS",
                "recommendation": {
                    "supplier_id": "SUP-777",
                    "product_id": "P1001",
                    "unit_price": 230.50,
                },
                "ranked_suppliers": [
                    {"supplier_id": "SUP-777", "unit_price": 230.50},
                ],
            },
        }
    ]

    action = DeterministicFallbackEngine.determine_next_action(
        user_query=query,
        registry=registry,
        transcript=transcript,
        entity_res=entity_res,
    )
    assert action.action == "suppliers.create_purchase_order"
    assert action.args["supplier_id"] == "SUP-777"
    assert action.args["product_id"] == "P1001"
    assert action.args["quantity"] == 50
    assert action.args["unit_price"] == 230.50
