import asyncio
from contextlib import AsyncExitStack
import json
import logging
import os
from pathlib import Path
import re
import sys
from typing import Any, Callable, Dict, List, Optional

from app import config
from app.config import BASE_DIR, TOOL_EXECUTION_TIMEOUT_SECONDS
from database.models import DiscoveredTool, ToolMetadata
from integrations.google_sheets.tools import GOOGLE_SHEETS_TOOLS, TOOL_EXECUTORS
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)

# Base paths for MCP server scripts
MCP_SERVER_PATHS = {
    "inventory": BASE_DIR / "mcp_servers" / "inventory" / "server.py",
    "orders": BASE_DIR / "mcp_servers" / "orders" / "server.py",
    "finance": BASE_DIR / "mcp_servers" / "finance" / "server.py",
    "suppliers": BASE_DIR / "mcp_servers" / "suppliers" / "server.py",
    "policies": BASE_DIR / "mcp_servers" / "policies" / "server.py",
}


class ToolRegistry:
    LEGACY_COMPATIBILITY_ALIASES = {
        "low_stock_items": "inventory.list_inventory",
        "inventory.low_stock_items": "inventory.list_inventory",
        "out_of_stock_items": "inventory.list_inventory",
        "inventory.out_of_stock_items": "inventory.list_inventory",
        "available_items": "inventory.list_inventory",
        "inventory.available_items": "inventory.list_inventory",
        "all_inventory": "inventory.list_inventory",
        "inventory.all_inventory": "inventory.list_inventory",
        "pending_payments": "finance.get_payment_status",
        "finance.pending_payments": "finance.get_payment_status",
        "monthly_financial_summary": "finance.financial_summary",
        "finance.monthly_financial_summary": "finance.financial_summary",
    }

    def __init__(self):
        self.tools: Dict[str, DiscoveredTool] = {}  # namespaced_name -> DiscoveredTool
        self.aliases: Dict[str, str] = {}  # short_name -> namespaced_name
        self.sessions: Dict[str, ClientSession] = {}  # server_name -> ClientSession
        self.degraded_servers: set = set()  # set of server names that failed to start
        self.resources: Dict[str, Dict[str, Any]] = {}  # uri -> {name, description, uri, server, session}
        self.prompts: Dict[str, Dict[str, Any]] = {}  # name -> {name, description, arguments, server, session}
        self.local_executors: Dict[str, Callable] = {}  # namespaced_name -> callable

    def register(self, tool: DiscoveredTool, session: Optional[ClientSession] = None):
        namespaced = f"{tool.metadata.domain}.{tool.name}"
        self.tools[namespaced] = tool
        if session:
            self.sessions[tool.owning_server] = session

        # Register short name alias if no collision
        if tool.name not in self.aliases:
            self.aliases[tool.name] = namespaced
        else:
            logger.debug(f"Tool name collision for '{tool.name}'. Use namespaced '{namespaced}'.")

    def register_local_tool(self, tool: DiscoveredTool, executor: Callable):
        self.register(tool)
        namespaced = f"{tool.metadata.domain}.{tool.name}"
        self.local_executors[namespaced] = executor
        self.local_executors[tool.name] = executor

    def register_resource(self, resource: Any, server_name: str, session: ClientSession):
        uri = str(getattr(resource, "uri", ""))
        self.resources[uri] = {
            "name": getattr(resource, "name", uri),
            "description": getattr(resource, "description", "") or "",
            "uri": uri,
            "server": server_name,
            "session": session,
        }

    def register_prompt(self, prompt: Any, server_name: str, session: ClientSession):
        name = str(getattr(prompt, "name", ""))
        self.prompts[name] = {
            "name": name,
            "description": getattr(prompt, "description", "") or "",
            "arguments": getattr(prompt, "arguments", []),
            "server": server_name,
            "session": session,
        }

    def resolve_tool_name(self, name: str) -> Optional[str]:
        if name in self.tools:
            return name
        if name in self.aliases:
            return self.aliases[name]
        if name in self.LEGACY_COMPATIBILITY_ALIASES:
            target = self.LEGACY_COMPATIBILITY_ALIASES[name]
            if target in self.tools:
                return target
        return None

    def get_tool(self, name: str) -> Optional[DiscoveredTool]:
        resolved = self.resolve_tool_name(name)
        if resolved:
            return self.tools[resolved]
        return None

    def format_tools_description(self, filter_domain: Optional[str] = None) -> str:
        lines = []
        if self.degraded_servers:
            degraded_list = ", ".join(sorted(self.degraded_servers))
            lines.append(f"[WARNING] Decommissioned/degraded servers: {degraded_list}.\n")

        for namespaced_name, t in self.tools.items():
            if filter_domain and t.metadata.domain != filter_domain:
                continue
            schema = t.input_schema or {}
            props = {k: v.get("type", "any") for k, v in schema.get("properties", {}).items()}
            reqs = schema.get("required", [])
            param_desc = f"{props}" if props else "{}"
            if reqs:
                param_desc += f" (required: {reqs})"
            meta = t.metadata
            lines.append(
                f"- Tool: {namespaced_name} (Alias: '{t.name}')\n"
                f"  Domain: {meta.domain} | Operation: {meta.operation_type} | Risk: {meta.risk_level}\n"
                f"  Description: {t.description}\n"
                f"  Input Schema: {param_desc}\n"
            )

        return "\n".join(lines)


class MCPRouterManager:
    def __init__(self):
        self.registry = ToolRegistry()
        self.exit_stack = AsyncExitStack()
        # Register integration tools (e.g. Google Sheets)
        self._register_integrations()

    def _register_integrations(self):
        for t in GOOGLE_SHEETS_TOOLS:
            namespaced = f"{t.metadata.domain}.{t.name}"
            exec_fn = TOOL_EXECUTORS.get(namespaced) or TOOL_EXECUTORS.get(t.name)
            if exec_fn:
                self.registry.register_local_tool(t, exec_fn)
            else:
                self.registry.register(t)

    async def initialize_connections(self):
        """
        Connects concurrently to MCP server subprocesses via Stdio and discovers tools.
        """
        for server_name, server_path in MCP_SERVER_PATHS.items():
            if not server_path.exists():
                logger.warning(f"MCP server script not found at {server_path}")
                self.registry.degraded_servers.add(server_name)
                continue

            server_params = StdioServerParameters(
                command=sys.executable,
                args=[str(server_path)],
                env=dict(os.environ),
            )

            try:
                read_stream, write_stream = await self.exit_stack.enter_async_context(
                    stdio_client(server_params)
                )

                session = await self.exit_stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )
                await session.initialize()

                # Discover tools
                mcp_tools = await session.list_tools()
                for t in mcp_tools.tools:
                    parsed_meta = self._parse_tool_metadata(t.name, t.description, server_name)
                    discovered = DiscoveredTool(
                        name=t.name,
                        description=t.description,
                        input_schema=t.inputSchema,
                        owning_server=server_name,
                        metadata=parsed_meta,
                    )
                    self.registry.register(discovered, session)

                # Discover resources
                try:
                    mcp_resources = await session.list_resources()
                    for r in getattr(mcp_resources, "resources", []):
                        self.registry.register_resource(r, server_name, session)
                except Exception:
                    pass

                # Discover prompts
                try:
                    mcp_prompts = await session.list_prompts()
                    for p in getattr(mcp_prompts, "prompts", []):
                        self.registry.register_prompt(p, server_name, session)
                except Exception:
                    pass

                logger.info(f"Connected to {server_name} MCP server ({len(mcp_tools.tools)} tools).")

            except Exception as e:
                logger.error(f"Failed to connect to {server_name} MCP server: {e}")
                self.registry.degraded_servers.add(server_name)

    async def close(self):
        """Cleanly close all open MCP server sessions and stdio streams."""
        try:
            await asyncio.wait_for(self.exit_stack.aclose(), timeout=5.0)
        except Exception as e:
            logger.debug(f"Notice while closing MCP exit stack: {e}")

    async def execute_tool(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Routes tool execution dynamically to local integrations or MCP server sessions.
        """
        resolved_name = self.registry.resolve_tool_name(tool_name)
        if not resolved_name:
            return {
                "status": "INVALID_REQUEST",
                "message": f"Unknown tool '{tool_name}'. Not found in ToolRegistry.",
            }

        tool = self.registry.get_tool(resolved_name)
        timeout = getattr(config, "TOOL_EXECUTION_TIMEOUT_SECONDS", TOOL_EXECUTION_TIMEOUT_SECONDS)

        # Check local executor (e.g. Google Sheets)
        if resolved_name in self.registry.local_executors:
            try:
                executor = self.registry.local_executors[resolved_name]
                if asyncio.iscoroutinefunction(executor):
                    res = await asyncio.wait_for(executor(**args), timeout=timeout)
                else:
                    res = executor(**args)
                return res
            except asyncio.TimeoutError:
                return {
                    "status": "TIMEOUT",
                    "message": f"Execution of tool '{resolved_name}' timed out after {timeout}s.",
                    "tool": resolved_name,
                }
            except Exception as e:
                return {"status": "EXECUTION_ERROR", "message": f"Tool '{resolved_name}' failed: {e}"}

        # Check MCP Server session
        session = self.registry.sessions.get(tool.owning_server)
        if not session:
            return {
                "status": "SERVICE_FAILURE",
                "message": f"MCP server '{tool.owning_server}' session is unavailable.",
            }

        # Translate legacy arguments for consolidated tools if needed
        norm_name = tool_name.lower().strip()
        call_args = dict(args)
        if norm_name in ("low_stock_items", "inventory.low_stock_items") and "status_filter" not in call_args:
            call_args["status_filter"] = "low_stock"
        elif norm_name in ("out_of_stock_items", "inventory.out_of_stock_items") and "status_filter" not in call_args:
            call_args["status_filter"] = "out_of_stock"
        elif norm_name in ("available_items", "inventory.available_items") and "status_filter" not in call_args:
            call_args["status_filter"] = "available"
        elif norm_name in ("all_inventory", "inventory.all_inventory") and "status_filter" not in call_args:
            call_args["status_filter"] = "all"
        elif norm_name in ("pending_payments", "finance.pending_payments") and "status_filter" not in call_args:
            if call_args.get("overdue_only"):
                call_args["status_filter"] = "overdue"
            else:
                call_args["status_filter"] = "all"

        try:
            res = await asyncio.wait_for(
                session.call_tool(tool.name, arguments=call_args),
                timeout=timeout,
            )

            if res.content and len(res.content) > 0:
                first_item = res.content[0]
                if hasattr(first_item, "text"):
                    try:
                        return json.loads(first_item.text)
                    except json.JSONDecodeError:
                        return {"status": "SUCCESS", "raw_text": first_item.text}

            return {"status": "SUCCESS", "result": str(res)}

        except asyncio.TimeoutError:
            return {
                "status": "TIMEOUT",
                "message": f"Tool '{tool_name}' execution timed out on server '{tool.owning_server}'.",
            }
        except Exception as e:
            return {
                "status": "SERVICE_FAILURE",
                "message": f"Error executing tool '{tool_name}': {str(e)}",
            }

    def _parse_tool_metadata(self, tool_name: str, description: str, server_name: str) -> ToolMetadata:
        match = re.search(r'Metadata:\s*(\{.*?\})', description)
        if match:
            try:
                meta_dict = json.loads(match.group(1))
                return ToolMetadata(**meta_dict)
            except Exception:
                pass

        is_write = any(w in tool_name for w in ("create", "update", "delete", "receive", "append"))
        return ToolMetadata(
            operation_type="write" if is_write else "read",
            risk_level="medium" if is_write else "none",
            requires_confirmation=is_write,
            required_role="Manager" if is_write else "Operator",
            idempotent=True,
            domain=server_name,
        )

