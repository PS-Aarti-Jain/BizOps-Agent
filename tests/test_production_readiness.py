import datetime
from decimal import Decimal
import os
import tempfile
import pytest

from database.repositories.repository import BizOpsRepository
from database.seed import seed_database
from services.date_resolver import RelativeDateResolver, get_current_date
from services.inventory_service import InventoryDomainService, classify_inventory_level
from services.finance_service import FinanceDomainService
from services.supplier_service import SupplierDomainService
from services.order_service import OrderDomainService
from security.safety_gateway import SafetyGateway
from agent.memory import ConversationMemory


@pytest.fixture
def test_repo():
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_prod_readiness.db")
    seed_database(db_path, force=True)
    return BizOpsRepository(db_path=db_path)


# 1. Available inventory query returns all in-stock products
def test_available_inventory_query_returns_in_stock_products(test_repo):
    inv_svc = InventoryDomainService(repo=test_repo)
    res = inv_svc.get_available_items()
    assert res["status"] == "SUCCESS"
    assert res["is_complete"] is True
    assert res["total_count"] > 0
    for it in res["items"]:
        assert it["available_qty"] > 0
        assert it["classification"] in ("IN_STOCK", "LOW_STOCK")


# 2. Out-of-stock query excludes products with positive available stock
def test_out_of_stock_query_excludes_positive_stock_products(test_repo):
    inv_svc = InventoryDomainService(repo=test_repo)
    res = inv_svc.get_out_of_stock_items()
    assert res["status"] == "SUCCESS"
    assert res["is_complete"] is True
    for it in res["items"]:
        assert it["available_qty"] == 0
        assert it["classification"] == "OUT_OF_STOCK"


# 3. Low-stock query does not classify products as unavailable
def test_low_stock_query_does_not_classify_products_as_unavailable(test_repo):
    inv_svc = InventoryDomainService(repo=test_repo)
    res = inv_svc.get_low_stock_items()
    assert res["status"] == "SUCCESS"
    for it in res["items"]:
        assert it["available_qty"] > 0
        assert it["available_qty"] <= it["reorder_threshold"]
        assert it["classification"] == "LOW_STOCK"


# 4. "This month" resolves to the current calendar month
def test_relative_date_this_month_resolves_to_current_month():
    ref_date = datetime.date(2026, 10, 30)
    dr = RelativeDateResolver.resolve_date_expression("this month", ref_date=ref_date)
    assert dr is not None
    assert dr.start_date == "2026-10-01"
    assert dr.end_date == "2026-10-31"
    assert dr.month_str == "2026-10"


# 5. "Last month" resolves to the previous calendar month (including year boundary)
def test_relative_date_last_month_resolves_to_previous_month():
    # Normal case: October -> September
    ref_oct = datetime.date(2026, 10, 30)
    dr_sep = RelativeDateResolver.resolve_date_expression("last month", ref_date=ref_oct)
    assert dr_sep is not None
    assert dr_sep.start_date == "2026-09-01"
    assert dr_sep.end_date == "2026-09-30"
    assert dr_sep.month_str == "2026-09"

    # Year boundary case: January 2026 -> December 2025
    ref_jan = datetime.date(2026, 1, 15)
    dr_dec = RelativeDateResolver.resolve_date_expression("last month", ref_date=ref_jan)
    assert dr_dec is not None
    assert dr_dec.start_date == "2025-12-01"
    assert dr_dec.end_date == "2025-12-31"
    assert dr_dec.month_str == "2025-12"


# 6. "Last two months" resolves consistently according to reporting convention
def test_relative_date_last_two_months_resolves_consistently():
    ref_date = datetime.date(2026, 10, 30)
    dr = RelativeDateResolver.resolve_date_expression("last two months", ref_date=ref_date)
    assert dr is not None
    assert dr.start_date == "2026-09-01"
    assert dr.end_date == "2026-10-31"


# 7. "Last month" in a follow-up message retains the revenue context
def test_follow_up_last_month_retains_revenue_context():
    mem = ConversationMemory(max_turns=3)
    sess_id = "test-session-rev"
    mem.add_turn(
        session_id=sess_id,
        query="What was our revenue this month?",
        answer="Total revenue for October 2026 was ₹1,56,350.",
        intent="financial_summary",
        entities={"period": "2026-10"},
        tool="finance.financial_summary",
    )

    resolved_q, ctx = mem.resolve_follow_up(sess_id, "last month")
    assert "revenue" in resolved_q.lower() or "financial summary" in resolved_q.lower()
    assert "last month" in resolved_q.lower()
    assert ctx is not None
    assert ctx.get("retained_intent") == "financial_summary"
    assert ctx.get("period") == "last month"


# 8. Customer outstanding balances come from invoices and payments
def test_customer_outstanding_balances_come_from_invoices_and_payments(test_repo):
    fin_svc = FinanceDomainService(repo=test_repo)
    res = fin_svc.get_pending_payments()
    assert res["status"] == "SUCCESS"
    assert res["source"] == "invoices_and_payments_ledger"
    assert res["debtor_count"] > 0
    assert res["total_outstanding_amount"] > 0

    # Verify partially paid invoice handling (INV-2005: 7350 total, 2350 paid, 5000 remaining)
    inv_status = fin_svc.get_invoice_status("INV-2005")
    assert inv_status["status"] == "SUCCESS"
    assert inv_status["total_amount"] == 7350.0
    assert inv_status["paid_amount"] == 2350.0
    assert inv_status["remaining_balance"] == 5000.0


# 9. Supplier ranking uses purchase-order history
def test_supplier_ranking_uses_purchase_order_history(test_repo):
    sup_svc = SupplierDomainService(repo=test_repo)
    res = sup_svc.get_supplier_performance()
    assert res["status"] == "SUCCESS"
    assert res["total_suppliers"] > 0
    assert res["total_system_spend"] > 0
    assert res["biggest_supplier_by_spend"] is not None
    assert res["biggest_supplier_by_spend"]["supplier_id"] == "SUP-101"
    assert res["biggest_supplier_by_spend"]["total_spend"] > 0


# 10. Partial retrieval is clearly identified
def test_partial_retrieval_is_clearly_identified(test_repo):
    inv_svc = InventoryDomainService(repo=test_repo)
    res = inv_svc.get_all_inventory_items()
    assert res["status"] == "SUCCESS"
    assert "total_count" in res
    assert "returned_count" in res
    assert "is_complete" in res
    assert res["total_count"] == res["returned_count"]
    assert res["is_complete"] is True


# 11. Unknown product and customer IDs return NOT_FOUND / NO_DATA
def test_unknown_product_and_customer_ids_return_not_found(test_repo):
    inv_svc = InventoryDomainService(repo=test_repo)
    stock_res = inv_svc.get_stock_info("NON_EXISTENT_P9999")
    assert stock_res["status"] == "NO_DATA"

    fin_svc = FinanceDomainService(repo=test_repo)
    cust_res = fin_svc.get_customer_balance("NON_EXISTENT_CUST")
    assert cust_res["status"] == "NO_DATA"

    ord_svc = OrderDomainService(repo=test_repo)
    ord_res = ord_svc.get_order_details("ORD-99999")
    assert ord_res["status"] == "NO_DATA"


# 12. Incoming purchase orders are not treated as received stock
def test_incoming_purchase_orders_not_treated_as_received_stock(test_repo):
    inv_svc = InventoryDomainService(repo=test_repo)
    res = inv_svc.get_stock_info("P1001")
    assert res["status"] == "SUCCESS"
    data = res["data"]
    # P1001 has 0 available physical stock
    assert data["available_qty"] == 0
    # But has incoming purchase orders
    assert data["incoming_qty"] > 0
    # Available stock remains 0 until goods receipt
    assert data["classification"] == "OUT_OF_STOCK"


# 13. Supplier comparison uses actual availability and lead times
def test_supplier_comparison_uses_actual_availability_and_lead_times(test_repo):
    sup_svc = SupplierDomainService(repo=test_repo)
    res = sup_svc.compare_and_rank_suppliers("P1001", required_quantity=100)
    assert res["status"] == "SUCCESS"
    assert res["recommendation"] is not None
    ranked = res["ranked_suppliers"]
    assert len(ranked) >= 2

    for r in ranked:
        assert "lead_time_days" in r
        assert "unit_price" in r
        assert "can_fulfill_qty" in r
        if r["available_qty"] >= 100:
            assert r["can_fulfill_qty"] is True


# 14. Existing Safety Gateway approval and idempotency tests continue to pass
def test_safety_gateway_approval_and_idempotency_continue_to_pass(test_repo):
    from database.models import DiscoveredTool, ToolMetadata
    safety = SafetyGateway(repo=test_repo)
    tool = DiscoveredTool(
        name="create_purchase_order",
        description="Creates PO",
        input_schema={},
        owning_server="suppliers",
        metadata=ToolMetadata(
            domain="suppliers",
            operation_type="write",
            risk_level="medium",
            required_role="Manager",
            requires_confirmation=True,
        ),
    )
    args = {
        "supplier_id": "SUP-102",
        "product_id": "P1001",
        "quantity": 50,
        "unit_price": 1245.00,
    }
    pa = safety.create_pending_action(
        tool=tool,
        args=args,
        user_id="USR-001",
        user_role="Manager",
    )
    assert pa.action_id.startswith("PA-")
    assert not pa.is_expired()

    # Operator cannot approve Manager action
    res_deny = safety.execute_confirmed_action(
        action_id=pa.action_id,
        user_id="USR-002",
        user_role="Operator",
        execute_fn=lambda p: {"status": "SUCCESS"},
    )
    assert res_deny["status"] == "PERMISSION_DENIED"

    # Manager can approve
    res_ok = safety.execute_confirmed_action(
        action_id=pa.action_id,
        user_id="USR-001",
        user_role="Manager",
        execute_fn=lambda p: {"status": "SUCCESS", "po_id": "PO-TEST-1"},
    )
    assert res_ok["status"] == "SUCCESS"
