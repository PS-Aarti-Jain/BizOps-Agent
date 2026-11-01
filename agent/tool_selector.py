from typing import Any, Dict, List, Optional
from database.models import DiscoveredTool
from mcp.router import ToolRegistry


class ToolSelector:
    """
    Dynamic Tool Selection Engine.
    Filters and exposes relevant tools based on query intent to reduce token overhead
    and optimize LLM generation speed.
    """

    INTENT_KEYWORDS = {
        "inventory": ["stock", "inventory", "reorder", "fulfill", "ship", "shortage", "warehouse", "items", "units", "godown", "available", "out of stock"],
        "orders": ["order", "orders", "ship", "shipped", "dispatch", "customer", "delivered", "pending"],
        "finance": ["balance", "invoice", "invoices", "due", "overdue", "owe", "credit", "payment", "sales", "revenue", "gst", "debtor", "debtors"],
        "suppliers": ["supplier", "suppliers", "vendor", "vendors", "price", "cheapest", "lead time", "quote", "purchase order", "po", "buy", "spend", "biggest", "ranking"],
        "google_sheets": ["sheet", "sheets", "spreadsheet", "google sheet", "google sheets", "row", "worksheet", "tab", "sync"],
    }

    @classmethod
    def select_relevant_tools(
        cls,
        user_query: str,
        registry: ToolRegistry,
        max_tools: int = 8,
    ) -> List[DiscoveredTool]:
        q_lower = user_query.lower()
        active_domains = set()

        for domain, keywords in cls.INTENT_KEYWORDS.items():
            if any(k in q_lower for k in keywords):
                active_domains.add(domain)

        # Cross-domain dependencies:
        # e.g., "fulfill" or "ship" requires both orders and inventory
        if any(w in q_lower for w in ["ship", "why haven't", "why have not", "why hasn't", "dispatch"]):
            active_domains.update(["orders", "inventory", "finance"])
        if any(w in q_lower for w in ["fulfill", "can we fulfill"]):
            active_domains.update(["orders", "inventory"])
        if any(w in q_lower for w in ["reorder", "buy", "purchase order", "supplier", "who should we order"]):
            active_domains.update(["inventory", "suppliers"])
        if any(w in q_lower for w in ["sheet", "spreadsheet"]):
            active_domains.update(["google_sheets", "inventory"])

        if not active_domains:
            # If no specific intent identified, return all available tools
            return list(registry.tools.values())

        selected = []
        for namespaced_name, tool in registry.tools.items():
            if tool.metadata.domain in active_domains:
                selected.append(tool)

        # Always return at least some tools
        return selected if selected else list(registry.tools.values())

