import pytest
from database.models import DiscoveredTool, ToolMetadata
from agent.tool_validator import ToolArgumentValidator, GroundingValidator
from agent.tool_selector import ToolSelector
from mcp.router import ToolRegistry


@pytest.fixture
def sample_registry():
    reg = ToolRegistry()
    reg.register(
        DiscoveredTool(
            name="get_stock",
            description="Check product inventory",
            input_schema={
                "type": "object",
                "properties": {
                    "product_id_or_name": {"type": "string"},
                },
            },
            owning_server="inventory",
            metadata=ToolMetadata(
                domain="inventory",
                operation_type="read",
                risk_level="none",
                requires_confirmation=False,
                required_role="Operator",
            ),
        )
    )
    reg.register(
        DiscoveredTool(
            name="customer_orders",
            description="Fetch customer orders",
            input_schema={
                "type": "object",
                "properties": {
                    "customer_id_or_name": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["customer_id_or_name"],
            },
            owning_server="orders",
            metadata=ToolMetadata(
                domain="orders",
                operation_type="read",
                risk_level="none",
                requires_confirmation=False,
                required_role="Operator",
            ),
        )
    )
    reg.register(
        DiscoveredTool(
            name="customer_balance",
            description="Fetch customer balance",
            input_schema={
                "type": "object",
                "properties": {
                    "customer_id_or_name": {"type": "string"},
                },
                "required": ["customer_id_or_name"],
            },
            owning_server="finance",
            metadata=ToolMetadata(
                domain="finance",
                operation_type="read",
                risk_level="none",
                requires_confirmation=False,
                required_role="Operator",
            ),
        )
    )
    reg.register(
        DiscoveredTool(
            name="compare_suppliers",
            description="Compare suppliers",
            input_schema={
                "type": "object",
                "properties": {
                    "product_id_or_name": {"type": "string"},
                    "required_quantity": {"type": "integer"},
                },
                "required": ["product_id_or_name"],
            },
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


def test_tool_argument_validation_success(sample_registry):
    """Verifies that valid arguments pass schema validation."""
    tool = sample_registry.get_tool("orders.customer_orders")
    valid, cleaned, err = ToolArgumentValidator.validate_tool_arguments(
        tool, {"customer_id_or_name": "Om Sai Enterprises", "limit": 5}
    )
    assert valid is True
    assert err is None
    assert cleaned["customer_id_or_name"] == "Om Sai Enterprises"
    assert cleaned["limit"] == 5


def test_tool_argument_coercion(sample_registry):
    """Verifies that numeric string arguments are safely coerced to integer."""
    tool = sample_registry.get_tool("orders.customer_orders")
    valid, cleaned, err = ToolArgumentValidator.validate_tool_arguments(
        tool, {"customer_id_or_name": "Om Sai Enterprises", "limit": "10"}
    )
    assert valid is True
    assert cleaned["limit"] == 10


def test_tool_argument_missing_required(sample_registry):
    """Verifies rejection when a required argument is missing."""
    tool = sample_registry.get_tool("orders.customer_orders")
    valid, cleaned, err = ToolArgumentValidator.validate_tool_arguments(
        tool, {"limit": 10}
    )
    assert valid is False
    assert "Missing required parameter 'customer_id_or_name'" in err


def test_tool_argument_invalid_type(sample_registry):
    """Verifies rejection when an argument cannot be converted to expected type."""
    tool = sample_registry.get_tool("orders.customer_orders")
    valid, cleaned, err = ToolArgumentValidator.validate_tool_arguments(
        tool, {"customer_id_or_name": "Om Sai Enterprises", "limit": "invalid_number"}
    )
    assert valid is False
    assert "must be an integer" in err


def test_scenario_tool_selection_inventory(sample_registry):
    """Scenario 1: Stock queries filter down to inventory tools."""
    selected = ToolSelector.select_relevant_tools("How much stock do we have for P1001?", sample_registry)
    domains = {t.metadata.domain for t in selected}
    assert "inventory" in domains


def test_scenario_tool_selection_finance(sample_registry):
    """Scenario 2: Debt and invoice queries filter down to finance tools."""
    selected = ToolSelector.select_relevant_tools("Check overdue balance and credit limit for Om Sai", sample_registry)
    domains = {t.metadata.domain for t in selected}
    assert "finance" in domains


def test_scenario_tool_selection_orders_fulfillment(sample_registry):
    """Scenario 3: Shipment and dispatch queries select orders and inventory tools."""
    selected = ToolSelector.select_relevant_tools("Can we ship the Pune order?", sample_registry)
    domains = {t.metadata.domain for t in selected}
    assert "orders" in domains
    assert "inventory" in domains


def test_scenario_tool_selection_supplier_comparison(sample_registry):
    """Scenario 4: Supplier comparison queries select suppliers tools."""
    selected = ToolSelector.select_relevant_tools("Which supplier is cheapest for Wireless Mouse?", sample_registry)
    domains = {t.metadata.domain for t in selected}
    assert "suppliers" in domains


def test_grounding_validation_supported_entities():
    """Verifies that an answer mentioning IDs present in observations passes grounding check."""
    answer = "Order ORD-1042 contains product P1001 and has a shortage."
    observations = {
        "orders.customer_orders": {"order_id": "ORD-1042", "product_id": "P1001"}
    }
    res = GroundingValidator.validate_answer_grounding(answer, observations)
    assert res["is_grounded"] is True
    assert res["grounding_status"] == "VERIFIED"
    assert "ORD-1042" in res["supported_ids"]
    assert "P1001" in res["supported_ids"]


def test_grounding_validation_hallucinated_entities():
    """Verifies that an answer mentioning an invented order ID is flagged as ungrounded."""
    answer = "Order ORD-9999 was dispatched yesterday with tracking PO-8888."
    observations = {
        "orders.customer_orders": {"order_id": "ORD-1042"}
    }
    res = GroundingValidator.validate_answer_grounding(answer, observations)
    assert res["is_grounded"] is False
    assert res["grounding_status"] == "POTENTIAL_HALLUCINATION"
    assert "ORD-9999" in res["unsupported_ids"]
    assert "PO-8888" in res["unsupported_ids"]

