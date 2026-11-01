import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from mcp.server.fastmcp import FastMCP
from services.order_service import OrderDomainService

mcp = FastMCP("Order MCP Server")
service = OrderDomainService()


@mcp.prompt("investigate_unshipped_order")
def investigate_unshipped_order_prompt(order_id: str) -> str:
    """Generate prompt instructions to investigate why an order has not shipped."""
    return (
        f"Perform a root-cause shipment blocker investigation for order '{order_id}'. "
        f"1. Retrieve order line items via orders.get_order. "
        f"2. Check available warehouse inventory for each product line via inventory.check_inventory. "
        f"3. Retrieve customer financial status and overdue invoices via finance.customer_balance. "
        f"4. Synthesize operational blockers and provide next resolution steps."
    )


@mcp.tool()
def get_order(order_id: str) -> dict:
    """Retrieve full order details including line items, customer details, and status.

    Metadata: {"operation_type": "read", "risk_level": "none", "requires_confirmation": false, "required_role": "Operator", "idempotent": true, "domain": "orders"}
    """
    return service.get_order_details(order_id)


@mcp.tool()
def pending_orders(
    start_date: str = "",
    end_date: str = "",
    month: str = "",
) -> dict:
    """Retrieve active orders currently in Pending or Processing status, optionally filtered by date range or month (YYYY-MM).

    Metadata: {"operation_type": "read", "risk_level": "none", "requires_confirmation": false, "required_role": "Operator", "idempotent": true, "domain": "orders"}
    """
    s_date = start_date if start_date else None
    e_date = end_date if end_date else None
    m_str = month if month else None
    return service.get_pending_orders(start_date=s_date, end_date=e_date, month=m_str)


@mcp.tool()
def customer_orders(customer_id_or_name: str) -> dict:
    """Retrieve order history for a specific customer by customer ID or customer name.

    Metadata: {"operation_type": "read", "risk_level": "none", "requires_confirmation": false, "required_role": "Operator", "idempotent": true, "domain": "orders"}
    """
    return service.get_customer_orders(customer_id_or_name)


if __name__ == "__main__":
    mcp.run(transport="stdio")
