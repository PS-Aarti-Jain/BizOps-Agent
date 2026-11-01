"""
MCP Client orchestration wrapper.
"""
from typing import Any, Dict
from mcp.router import MCPRouterManager


class MCPClient:
    def __init__(self, router: MCPRouterManager):
        self.router = router

    async def execute(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        return await self.router.execute_tool(tool_name, args)

