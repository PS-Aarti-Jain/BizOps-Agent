import sys
import uuid
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from mcp.server.fastmcp import FastMCP
from database.repositories.repository import BizOpsRepository
from services.supplier_service import SupplierDomainService

mcp = FastMCP("Supplier MCP Server")
service = SupplierDomainService()
repo = BizOpsRepository()


@mcp.tool()
def find_supplier(product_id_or_name: str) -> dict:
    """Find all suppliers offering a specific product by product ID or product name.

    Metadata: {"operation_type": "read", "risk_level": "none", "requires_confirmation": false, "required_role": "Operator", "idempotent": true, "domain": "suppliers"}
    """
    return service.find_suppliers_for_product(product_id_or_name)


@mcp.tool()
def compare_suppliers(product_id_or_name: str, required_quantity: int = 1) -> dict:
    """Compare and deterministically rank all available suppliers for a product by total cost, lead time, and stock availability.

    Metadata: {"operation_type": "read", "risk_level": "none", "requires_confirmation": false, "required_role": "Operator", "idempotent": true, "domain": "suppliers"}
    """
    return service.compare_and_rank_suppliers(product_id_or_name, required_quantity)


@mcp.tool()
def supplier_performance(start_date: str = "", end_date: str = "", period: str = "") -> dict:
    """Analyze supplier performance metrics and rank suppliers by total purchase spend and order frequency from actual purchase order history.

    Metadata: {"operation_type": "read", "risk_level": "none", "requires_confirmation": false, "required_role": "Operator", "idempotent": true, "domain": "suppliers"}
    """
    s_date = start_date if start_date else None
    e_date = end_date if end_date else None
    p_val = period if period else None
    return service.get_supplier_performance(start_date=s_date, end_date=e_date, period=p_val)


@mcp.tool()
def create_purchase_order(
    supplier_id: str,
    product_id: str,
    quantity: int,
    unit_price: float,
    reason: str = "Restock procurement",
    idempotency_key: str = "",
    created_by: str = "Manager",
) -> dict:
    """Create a new official purchase order with a supplier for restock. REQUIRES MANAGER/ADMIN CONFIRMATION.

    Metadata: {"operation_type": "write", "risk_level": "medium", "requires_confirmation": true, "required_role": "Manager", "idempotent": true, "domain": "suppliers"}
    """
    if not idempotency_key:
        idempotency_key = f"IDEM-PO-{uuid.uuid4().hex[:12].upper()}"

    # 1. Check idempotency key first
    existing = repo.check_idempotency_key(idempotency_key)
    if existing:
        return {
            "status": "SUCCESS",
            "message": "Existing purchase order returned (idempotent duplicate request).",
            "data": existing,
            "is_duplicate": True,
        }

    # 2. Compute total amount
    total_amount = round(quantity * unit_price, 2)
    po_id = f"PO-{uuid.uuid4().hex[:8].upper()}"

    # 3. Create PO record in database
    po_record = repo.create_purchase_order_record(
        po_id=po_id,
        supplier_id=supplier_id,
        product_id=product_id,
        quantity=quantity,
        unit_price=unit_price,
        total_amount=total_amount,
        created_by=created_by,
        idempotency_key=idempotency_key,
    )

    return {
        "status": "SUCCESS",
        "message": f"Successfully created Purchase Order {po_id}",
        "data": po_record,
        "is_duplicate": False,
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
