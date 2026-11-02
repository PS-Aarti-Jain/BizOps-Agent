"""
Smoke Check Script for BizOps Agent (Item 6).
Imports all modules, connects to all 5 MCP servers, verifies tools, resources,
and prompts discovery, and exits non-zero on any failure.
"""
import asyncio
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

def smoke_check_imports():
    print("[1/3] Verifying core and domain module imports...")
    try:
        import app.config
        import database.connection
        import database.repositories.repository
        import database.seed
        import domain.models
        import domain.entity_resolver
        import domain.policy_retriever
        import services.inventory_service
        import services.order_service
        import services.finance_service
        import services.supplier_service
        import services.date_resolver
        import services.language_policy
        import services.query_normalizer
        import agent.agent
        import agent.react_loop
        import agent.domain_guard
        import agent.tool_selector
        import agent.tool_validator
        import agent.response_formatter
        import agent.memory
        import agent.observability
        import agent.reflection
        import llm.base
        import llm.groq_client
        import llm.prompts
        import mcp.router
        import security.safety_gateway
        import security.redaction
        import security.confirmation
        import ui.app
        print(" -> All modules successfully imported.")
    except Exception as e:
        print(f"FAILED to import module: {e}")
        sys.exit(1)


async def smoke_check_mcp_servers():
    print("[2/3] Connecting to 5 MCP servers and discovering capabilities...")
    from mcp.router import MCPRouterManager

    router = MCPRouterManager()
    try:
        await router.initialize_connections()

        tools = router.registry.tools
        resources = router.registry.resources
        prompts = router.registry.prompts

        print(f" -> Discovered Tools ({len(tools)}):")
        for t_name, t_spec in tools.items():
            print(f"    - {t_name} [{t_spec.metadata.operation_type.upper()}] (role: {t_spec.metadata.required_role})")

        print(f" -> Discovered Resources ({len(resources)}):")
        for uri, r in resources.items():
            print(f"    - {uri} ({r.get('name')})")

        print(f" -> Discovered Prompts ({len(prompts)}):")
        for p_name, p in prompts.items():
            print(f"    - {p_name}: {p.get('description')}")

        # Verification boundaries
        if len(tools) < 14:
            raise ValueError(f"Expected at least 14 tools across 5 MCP servers, found {len(tools)}.")
        if len(resources) < 2:
            raise ValueError(f"Expected at least 2 MCP resources, found {len(resources)}.")
        if len(prompts) < 2:
            raise ValueError(f"Expected at least 2 MCP prompts, found {len(prompts)}.")

        print("[3/3] MCP capability checks passed successfully.")

    except Exception as e:
        print(f"FAILED during MCP server verification: {e}")
        sys.exit(1)
    finally:
        await router.close()


def main():
    print("=" * 60)
    print("BizOps Agent - Comprehensive Smoke Check")
    print("=" * 60)
    smoke_check_imports()
    asyncio.run(smoke_check_mcp_servers())
    print("=" * 60)
    print("Smoke check PASSED: System is healthy and operational.")
    print("=" * 60)


if __name__ == "__main__":
    main()
