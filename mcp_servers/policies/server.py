import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from mcp.server.fastmcp import FastMCP
from domain.policy_retriever import PolicyRetriever

mcp = FastMCP("Policy MCP Server")
retriever = PolicyRetriever()


@mcp.tool()
def search_policy(query: str, top_k: int = 2) -> dict:
    """Search company operational policies (credit-hold policy, reorder thresholds, supplier SLA) using keyword and TF-IDF retrieval.

    Metadata: {"operation_type": "read", "risk_level": "none", "requires_confirmation": false, "required_role": "Operator", "idempotent": true, "domain": "policies"}
    """
    results = retriever.search(query, top_k=top_k)
    return {
        "status": "SUCCESS" if results else "NO_DATA",
        "query": query,
        "count": len(results),
        "results": results,
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")

