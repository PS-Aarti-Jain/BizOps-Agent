import asyncio
import pytest
from database.models import DiscoveredTool, ToolMetadata
from mcp.router import MCPRouterManager, ToolRegistry


@pytest.fixture
def test_registry():
    registry = ToolRegistry()
    t = DiscoveredTool(
        name="get_stock",
        description="Check stock levels",
        input_schema={"type": "object", "properties": {"product_id": {"type": "string"}}},
        owning_server="inventory",
        metadata=ToolMetadata(
            domain="inventory",
            operation_type="read",
            risk_level="low",
            required_role="Operator",
        ),
    )
    registry.register(t)
    return registry


def test_tool_registration_and_aliasing(test_registry):
    """Verifies namespaced and aliased tool lookup."""
    namespaced = "inventory.get_stock"
    assert test_registry.resolve_tool_name("inventory.get_stock") == namespaced
    assert test_registry.resolve_tool_name("get_stock") == namespaced
    assert test_registry.resolve_tool_name("non_existent_tool") is None

    tool = test_registry.get_tool("get_stock")
    assert tool is not None
    assert tool.name == "get_stock"
    assert tool.metadata.domain == "inventory"


def test_degraded_server_warning(test_registry):
    """Verifies that degraded servers are flagged in formatted descriptions."""
    test_registry.degraded_servers.add("policies")
    desc = test_registry.format_tools_description()
    assert "[WARNING] Decommissioned/degraded servers: policies" in desc
    assert "inventory.get_stock" in desc


@pytest.mark.asyncio
async def test_local_integration_routing():
    """Verifies that registered local integration tools (like Google Sheets) execute directly."""
    router = MCPRouterManager()
    try:
        resolved = router.registry.resolve_tool_name("google_sheets.read_data")
        assert resolved == "google_sheets.read_data"

        res = await router.execute_tool("google_sheets.read_data", {"worksheet_name": "Inventory"})
        assert res["status"] == "SUCCESS"
        assert "records" in res
        assert res["worksheet"] == "Inventory"
    finally:
        await router.close()


@pytest.mark.asyncio
async def test_unknown_tool_routing():
    """Verifies graceful error handling for unregistered tools."""
    router = MCPRouterManager()
    try:
        res = await router.execute_tool("phantom_server.teleport", {"distance": 50})
        assert res["status"] == "INVALID_REQUEST"
        assert "not found" in res["message"].lower()
    finally:
        await router.close()


@pytest.mark.asyncio
async def test_tool_execution_timeout():
    """Verifies timeout protection on hanging tool execution."""
    registry = ToolRegistry()
    async def slow_executor(**kwargs):
        await asyncio.sleep(2.0)
        return {"data": "done"}

    t = DiscoveredTool(
        name="slow_task",
        description="A slow task",
        input_schema={},
        owning_server="test",
        metadata=ToolMetadata(domain="test", operation_type="read", risk_level="low", required_role="Operator"),
    )
    registry.register_local_tool(t, slow_executor)
    
    router = MCPRouterManager()
    router.registry = registry

    import app.config as config
    original_timeout = config.TOOL_EXECUTION_TIMEOUT_SECONDS
    try:
        config.TOOL_EXECUTION_TIMEOUT_SECONDS = 0.1
        res = await router.execute_tool("slow_task", {})
        assert res["status"] in ["TIMEOUT", "EXECUTION_ERROR"]
        assert "timed out" in res["message"].lower()
    finally:
        config.TOOL_EXECUTION_TIMEOUT_SECONDS = original_timeout
        await router.close()
