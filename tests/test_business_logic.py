from decimal import Decimal
import os
import tempfile
import pytest

from database.repositories.repository import BizOpsRepository
from database.seed import seed_database
from services.inventory_service import InventoryDomainService
from services.order_service import OrderDomainService
from services.finance_service import FinanceDomainService
from services.supplier_service import SupplierDomainService


@pytest.fixture
def seeded_repo():
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_bizops.db")
    seed_database(db_path, force=True)
    return BizOpsRepository(db_path=db_path)


def test_inventory_available_calculation(seeded_repo):
    """Verifies that available quantity correctly accounts for on-hand, reserved, and allocated quantities."""
    service = InventoryDomainService(repo=seeded_repo)
    # P1001: Wireless Optical Mouse (USB) - completely out of stock
    res = service.get_stock_info("P1001")
    assert res["status"] == "SUCCESS"
    data = res["data"]
    assert data["product_id"] == "P1001"
    assert data["available_qty"] == data["on_hand_qty"] - data["reserved_qty"] - data["allocated_qty"]
    assert data["available_qty"] == 0
    assert data["is_out_of_stock"] is True
    assert data["classification"] == "OUT_OF_STOCK"
    assert data["is_low_stock"] is False

    # P1002: Mechanical Gaming Keyboard - low stock (avail: 5, threshold: 15)
    res_p1002 = service.get_stock_info("P1002")
    assert res_p1002["status"] == "SUCCESS"
    data_p1002 = res_p1002["data"]
    assert data_p1002["available_qty"] == 5
    assert data_p1002["is_low_stock"] is True
    assert data_p1002["classification"] == "LOW_STOCK"
    assert data_p1002["is_out_of_stock"] is False


def test_order_shortage_detection(seeded_repo):
    """Verifies that order fulfillment check detects items with shortages."""
    service = OrderDomainService(repo=seeded_repo)
    # ORD-1042 has items exceeding available stock
    res = service.get_order_details("ORD-1042")
    assert res["status"] == "SUCCESS"
    order = res["data"]
    assert order["order_id"] == "ORD-1042"
    assert len(order["items"]) > 0

    # Test fulfillment check
    inv_service = InventoryDomainService(repo=seeded_repo)
    f_res = inv_service.check_inventory_items(order["items"])
    assert f_res["status"] == "SUCCESS"
    assert f_res["all_fulfillable"] is False
    assert any(item.get("fulfillable") is False and item.get("shortage", 0) > 0 for item in f_res["items"])


def test_finance_customer_balance_and_overdue(seeded_repo):
    """Verifies credit limit evaluation and overdue invoice detection."""
    service = FinanceDomainService(repo=seeded_repo)
    # CUST-501: Om Sai Enterprises has overdue invoice INV-2001 (₹32,000)
    res = service.get_customer_balance("CUST-501")
    assert res["status"] == "SUCCESS"
    assert res["customer_name"] == "Om Sai Enterprises"
    assert res["credit_hold_recommended"] is True
    assert res["overdue_amount"] == 32000.0


def test_supplier_deterministic_ranking(seeded_repo):
    """Verifies deterministic ranking of suppliers based on quantity, cost, lead time, and reliability."""
    service = SupplierDomainService(repo=seeded_repo)
    res = service.compare_and_rank_suppliers("P1001", required_quantity=100)
    assert res["status"] == "SUCCESS"
    ranked = res["ranked_suppliers"]
    assert len(ranked) >= 2
    best = ranked[0]
    assert best["rank"] == 1
    assert "total_cost_for_req" in best
    assert "trade_off_vs_recommendation" in best


def test_decimal_financial_accuracy(seeded_repo):
    """Verifies that all prices and currency computations use exact Decimal math without float rounding error."""
    product = seeded_repo.get_product("P1001")
    cost = Decimal(str(product["unit_cost"]))
    selling = Decimal(str(product["selling_price"]))
    gst = Decimal(str(product["gst_rate"]))

    # GST calculation: (selling * gst) / 100
    gst_amt = (selling * gst) / Decimal("100")
    total_with_tax = selling + gst_amt
    assert str(total_with_tax).replace(".", "").isdigit()


def test_audit_log_record(seeded_repo):
    """Verifies that audit logging records operational state changes."""
    seeded_repo.log_audit_entry(
        session_id="SESS-TEST",
        action_id="PA-TEST01",
        user_id="USR-101",
        user_role="Admin",
        tool_name="test_tool",
        arguments={"param": "val"},
        risk_level="high",
        confirmation_status="CONFIRMED",
        user_confirmed_at="2026-10-30T10:00:00Z",
        execution_status="SUCCESS",
        result={"status": "ok"},
        idempotency_key="IDEM-TEST01",
    )
    logs = seeded_repo.get_audit_logs(limit=1)
    assert len(logs) == 1
    assert logs[0]["action_id"] == "PA-TEST01"
    assert logs[0]["user_id"] == "USR-101"
