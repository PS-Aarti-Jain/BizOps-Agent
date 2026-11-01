"""
Automated regression tests locking in exact business numbers and verifying data consistency across:
- Database records (authoritative truth)
- Domain services (OrderDomainService, FinanceDomainService, InventoryDomainService)
- REST API endpoints (/api/data/orders, /api/data/finance, /api/data/inventory)
- ResponseFormatter outputs
"""
import pytest
from httpx import AsyncClient, ASGITransport
from services.order_service import OrderDomainService
from services.finance_service import FinanceDomainService
from services.inventory_service import InventoryDomainService, classify_inventory_level
from agent.response_formatter import ResponseFormatter
from ui.app import app


@pytest.fixture
def order_service():
    return OrderDomainService()


@pytest.fixture
def finance_service():
    return FinanceDomainService()


@pytest.fixture
def inventory_service():
    return InventoryDomainService()


def test_orders_status_and_totals(order_service):
    """
    Validates exact order totals and status breakdowns:
    - 4 strictly Pending orders: ORD-1048 (₹5,500), ORD-1046 (₹8,400), ORD-1044 (₹7,700), ORD-1042 (₹38,000) = ₹59,600.00
    - 1 Processing order: ORD-1043 (₹34,500) = ₹34,500.00
    - 5 Active unfulfilled orders (Pending + Processing) = ₹94,100.00
    - 2 Shipped orders: ORD-1045 (₹6,800), ORD-1047 (₹7,350) = ₹14,150.00
    - Total all orders (7) = ₹1,08,250.00
    """
    res = order_service.get_pending_orders()
    assert res["status"] == "SUCCESS"
    assert res["count"] == 5

    # Check precomputed domain metrics
    assert res["pending_count"] == 4
    assert res["pending_amount"] == 59600.00
    assert res["processing_count"] == 1
    assert res["processing_amount"] == 34500.00
    assert res["total_active_amount"] == 94100.00

    # Verify individual order amounts
    order_map = {o["order_id"]: o for o in res["orders"]}
    assert order_map["ORD-1048"]["total_amount"] == 5500.00
    assert order_map["ORD-1048"]["status"] == "Pending"

    assert order_map["ORD-1046"]["total_amount"] == 8400.00
    assert order_map["ORD-1046"]["status"] == "Pending"

    assert order_map["ORD-1044"]["total_amount"] == 7700.00
    assert order_map["ORD-1044"]["status"] == "Pending"

    assert order_map["ORD-1043"]["total_amount"] == 34500.00
    assert order_map["ORD-1043"]["status"] == "Processing"

    assert order_map["ORD-1042"]["total_amount"] == 38000.00
    assert order_map["ORD-1042"]["status"] == "Pending"


def test_september_2026_revenue_consistency(finance_service):
    """
    Validates September 2026 financials:
    - 2 invoices: INV-2001 (₹32,000.00 Overdue), INV-2006 (₹45,000.00 Overdue)
    - Total Invoiced: ₹77,000.00
    - Total Collected: ₹0.00
    - Total Outstanding: ₹77,000.00
    """
    sep = finance_service.get_financial_summary(month="2026-09")
    assert sep["status"] == "SUCCESS"
    assert sep["invoice_count"] == 2
    assert sep["total_invoiced"] == 77000.00
    assert sep["total_collected"] == 0.00
    assert sep["total_outstanding"] == 77000.00


def test_october_2026_revenue_consistency(finance_service):
    """
    Validates October 2026 financials:
    - 4 invoices: INV-2005 (₹7,350 billed, ₹2,350 paid), INV-2002 (₹34,500 paid),
      INV-2003 (₹7,700 unpaid), INV-2004 (₹6,800 paid)
    - Total Invoiced: ₹56,350.00
    - Total Collected: ₹43,650.00
    - Total Outstanding: ₹12,700.00
    """
    oct_data = finance_service.get_financial_summary(month="2026-10")
    assert oct_data["status"] == "SUCCESS"
    assert oct_data["invoice_count"] == 4
    assert oct_data["total_invoiced"] == 56350.00
    assert oct_data["total_collected"] == 43650.00
    assert oct_data["total_outstanding"] == 12700.00


def test_all_time_financial_ledger_integrity(finance_service):
    """
    Validates all-time financial ledger integrity across both months:
    - September Invoiced (₹77,000) + October Invoiced (₹56,350) = ₹1,33,350.00
    - September Collected (₹0) + October Collected (₹43,650) = ₹43,650.00
    - Total Outstanding = ₹89,700.00 (₹77,000 + ₹12,700)
    - Total Overdue = ₹77,000.00 (both September invoices)
    """
    payments = finance_service.get_pending_payments()
    assert payments["status"] == "SUCCESS"
    assert payments["debtor_count"] == 4
    assert payments["unpaid_invoice_count"] == 4
    assert payments["total_outstanding_amount"] == 89700.00
    assert payments["total_overdue_amount"] == 77000.00


def test_inventory_consistency_and_classification(inventory_service):
    """
    Validates inventory counts and mutual classification consistency:
    - Total products: 15
    - Available (positive stock > 0): exactly 14 items
    - Out of stock (stock == 0): exactly 1 item (P1001)
    - Low stock (positive stock <= reorder_threshold): exactly 8 items
    - Well-stocked / In Stock (> reorder_threshold): exactly 6 items
    - Total sum: 1 (OOS) + 8 (Low) + 6 (In Stock) = 15 total items
    """
    all_res = inventory_service.get_all_inventory_items()
    assert all_res["total_count"] == 15

    avail_res = inventory_service.get_available_items()
    assert avail_res["total_count"] == 14

    oos_res = inventory_service.get_out_of_stock_items()
    assert oos_res["total_count"] == 1
    assert oos_res["items"][0]["product_id"] == "P1001"
    assert oos_res["items"][0]["available_qty"] == 0

    low_res = inventory_service.get_low_stock_items()
    assert low_res["total_count"] == 8
    low_ids = {it["product_id"] for it in low_res["items"]}
    assert low_ids == {"P1002", "P1003", "P1004", "P1007", "P1008", "P1010", "P1014", "P1015"}

    # Classification breakdown
    classifications = [it["classification"] for it in all_res["items"]]
    assert classifications.count("OUT_OF_STOCK") == 1
    assert classifications.count("LOW_STOCK") == 8
    assert classifications.count("IN_STOCK") == 6


@pytest.mark.asyncio
async def test_api_endpoints_data_alignment():
    """
    Verifies that FastAPI REST endpoints return data strictly aligned with domain services.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Orders endpoint
        res_orders = await client.get("/api/data/orders")
        assert res_orders.status_code == 200
        ord_body = res_orders.json()
        assert ord_body["status"] == "ok"
        assert len(ord_body["data"]) == 7
        summary = ord_body.get("summary", {})
        assert summary.get("pending_count") == 4
        assert summary.get("pending_amount") == 59600.00
        assert summary.get("processing_count") == 1
        assert summary.get("processing_amount") == 34500.00
        assert summary.get("active_amount") == 94100.00

        # 2. Finance endpoint
        res_fin = await client.get("/api/data/finance")
        assert res_fin.status_code == 200
        fin_body = res_fin.json()
        assert fin_body["status"] == "ok"
        assert len(fin_body["data"]) == 6
        fin_summary = fin_body.get("summary", {})
        assert fin_summary["october_2026"]["total_invoiced"] == 56350.00
        assert fin_summary["october_2026"]["total_collected"] == 43650.00
        assert fin_summary["september_2026"]["total_invoiced"] == 77000.00
        assert fin_summary["total_outstanding"] == 89700.00
        assert fin_summary["total_overdue"] == 77000.00

        # 3. Inventory endpoint
        res_inv = await client.get("/api/data/inventory")
        assert res_inv.status_code == 200
        inv_body = res_inv.json()
        assert inv_body["status"] == "ok"
        assert len(inv_body["data"]) == 15
        p1001 = next(r for r in inv_body["data"] if r["product_id"] == "P1001")
        assert p1001["available_qty"] == 0
        assert p1001["classification"] == "OUT_OF_STOCK"


def test_response_formatter_pending_orders_grounding():
    """
    Verifies that ResponseFormatter produces grounded responses with exact database values.
    """
    obs = {
        "orders.pending_orders": {
            "status": "SUCCESS",
            "count": 5,
            "total_active_amount": 94100.00,
            "pending_count": 4,
            "pending_amount": 59600.00,
            "processing_count": 1,
            "processing_amount": 34500.00,
            "orders": [
                {"order_id": "ORD-1048", "order_date": "2026-10-30", "total_amount": 5500.00, "status": "Pending", "customer_id": "CUST-509"},
                {"order_id": "ORD-1046", "order_date": "2026-10-29", "total_amount": 8400.00, "status": "Pending", "customer_id": "CUST-506"},
                {"order_id": "ORD-1044", "order_date": "2026-10-27", "total_amount": 7700.00, "status": "Pending", "customer_id": "CUST-503"},
                {"order_id": "ORD-1043", "order_date": "2026-10-26", "total_amount": 34500.00, "status": "Processing", "customer_id": "CUST-502"},
                {"order_id": "ORD-1042", "order_date": "2026-10-25", "total_amount": 38000.00, "status": "Pending", "customer_id": "CUST-501"},
            ],
        }
    }
    ans = ResponseFormatter.format_business_response("pending orders", obs)
    assert "94,100.00" in ans
    assert "59,600.00" in ans
    assert "34,500.00" in ans
    assert "ORD-1048" in ans
    assert "ORD-1042" in ans
    assert "SUCCESS" in ans

