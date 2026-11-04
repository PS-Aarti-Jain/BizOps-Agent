import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from mcp.server.fastmcp import FastMCP
from services.inventory_service import InventoryDomainService

mcp = FastMCP("Inventory MCP Server")
service = InventoryDomainService()


@mcp.resource("inventory://schema")
def get_inventory_schema() -> str:
    """Returns inventory database schema documentation and availability calculation rules."""
    return (
        "Inventory Schema: inventory(inventory_id, product_id, on_hand_qty, reserved_qty, allocated_qty, location). "
        "Available stock = max(0, on_hand_qty - reserved_qty - allocated_qty). "
        "Low stock trigger occurs when available_qty <= reorder_threshold."
    )


@mcp.prompt("restock_review")
def restock_review_prompt(product_id: str) -> str:
    """Generate prompt instructions to review restock status and supplier options for a product."""
    return (
        f"Perform a comprehensive restock review for product ID '{product_id}'. "
        f"1. Query current available and incoming inventory via inventory.get_stock. "
        f"2. Check reorder threshold and calculate needed shortage units. "
        f"3. Compare all active suppliers via suppliers.compare_suppliers to recommend best vendor by cost and lead time."
    )


@mcp.tool()
def get_stock(product_id: str = "", product_id_or_name: str = "") -> dict:
    """Get product stock levels including on-hand, reserved, allocated, and available stock.

    Metadata: {"operation_type": "read", "risk_level": "none", "requires_confirmation": false, "required_role": "Operator", "idempotent": true, "domain": "inventory"}
    """
    pid = product_id or product_id_or_name
    return service.get_stock_info(pid)


@mcp.tool()
def check_inventory(items: list) -> dict:
    """Check if a list of product items with requested quantities can be fulfilled from available inventory.

    Metadata: {"operation_type": "read", "risk_level": "none", "requires_confirmation": false, "required_role": "Operator", "idempotent": true, "domain": "inventory"}
    """
    return service.check_inventory_items(items)


@mcp.tool()
def list_inventory(status_filter: str = "all", category: str = "") -> dict:
    """Retrieve warehouse inventory catalog with optional status filtering ('all', 'low_stock', 'out_of_stock', 'available') and category.

    Metadata: {"operation_type": "read", "risk_level": "none", "requires_confirmation": false, "required_role": "Operator", "idempotent": true, "domain": "inventory"}
    """
    return service.list_inventory(status_filter=status_filter, category=category if category else None)


@mcp.tool()
def receive_goods(po_id: str, received_by: str = "Warehouse Manager") -> dict:
    """Record verified receipt of goods from an approved purchase order and update available inventory.

    Metadata: {"operation_type": "write", "risk_level": "medium", "requires_confirmation": true, "required_role": "Manager", "idempotent": true, "domain": "inventory"}
    """
    return service.receive_goods(po_id, received_by)


if __name__ == "__main__":
    mcp.run(transport="stdio")
