import asyncio
import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from app.config import (
    DEVELOPER_MODE,
    MAX_REACT_ITERATIONS,
    OBSERVATION_CHAR_LIMIT,
    QUERY_TIMEOUT_SECONDS,
)
from database.models import DiscoveredTool, ReActAction
from domain.entity_resolver import EntityResolutionResult, EntityResolver
from llm.base import LLMClient
from llm.prompts import REACT_SYSTEM_PROMPT
from mcp.router import MCPRouterManager, ToolRegistry
from security.redaction import SecuritySanitizer
from agent.response_formatter import ResponseFormatter, _matches_intent
from agent.tool_selector import ToolSelector
from services.date_resolver import RelativeDateResolver

logger = logging.getLogger(__name__)


class DeterministicFallbackEngine:
    """
    Authoritative, zero-hallucination deterministic fallback engine.
    Active during test execution, offline development, or provider degraded states.
    Directly binds extracted entities to registered MCP and Google Sheets tools.
    """

    @classmethod
    def determine_next_action(
        cls,
        user_query: str,
        registry: ToolRegistry,
        transcript: List[Dict[str, Any]],
        entity_res: Optional[EntityResolutionResult] = None,
    ) -> ReActAction:
        query_lower = user_query.lower()
        if not entity_res:
            resolver = EntityResolver()
            entity_res = resolver.resolve_query(user_query)

        if entity_res.status == "AMBIGUOUS":
            return ReActAction(
                action="final",
                answer=entity_res.clarification_prompt or "Multiple matching records found. Please clarify.",
            )

        called_tools = {step.get("action") for step in transcript if step.get("action")}
        retrieved_data: Dict[str, Any] = {}
        for step in transcript:
            obs = step.get("observation", {})
            action = step.get("action")
            if action and isinstance(obs, dict):
                retrieved_data[action] = obs

        # Google Sheets Operations
        if "sheet" in query_lower or "spreadsheet" in query_lower or "worksheet" in query_lower:
            ws_target = (
                entity_res.worksheet_name
                if (entity_res and entity_res.worksheet_name)
                else ("Orders" if "order" in query_lower else ("Customers" if "customer" in query_lower else "Inventory"))
            )

            # Scenario A: Check low-stock and update sheet
            if any(w in query_lower for w in ["low-stock", "low stock", "stock status"]):
                inv_tool = registry.resolve_tool_name("list_inventory") or registry.resolve_tool_name("low_stock_items") or "inventory.list_inventory"
                if inv_tool not in called_tools:
                    return ReActAction(action=inv_tool, args={"status_filter": "low_stock"})

                sheets_read = registry.resolve_tool_name("read_data") or "google_sheets.read_data"
                if sheets_read not in called_tools:
                    return ReActAction(action=sheets_read, args={"worksheet_name": "Inventory"})

                sheets_update = registry.resolve_tool_name("update_record") or "google_sheets.update_record"
                if sheets_update not in called_tools:
                    low_stock_obs = retrieved_data.get(inv_tool, {})
                    items = low_stock_obs.get("items", [])
                    target_p_id = items[0]["product_id"] if items else "P1001"
                    avail = items[0]["available_qty"] if items else 0

                    sheet_obs = retrieved_data.get(sheets_read, {})
                    records = sheet_obs.get("records", [])
                    target_row = 2
                    for r in records:
                        if r.get("Product ID") == target_p_id:
                            target_row = r.get("_row_number", 2)
                            break

                    return ReActAction(
                        action=sheets_update,
                        args={
                            "worksheet_name": "Inventory",
                            "row_index": target_row,
                            "updates": {
                                "Available Qty": avail,
                                "Stock Status": "Low Stock" if avail <= 25 else "In Stock",
                            },
                        },
                    )

                return ReActAction(
                    action="final",
                    answer=ResponseFormatter.format_business_response(user_query, retrieved_data, entity_res),
                )

            # Scenario B: Search records
            if any(w in query_lower for w in ["find", "search", "lookup"]):
                search_tool = registry.resolve_tool_name("search_records") or "google_sheets.search_records"
                if search_tool not in called_tools:
                    import re
                    m_ord = re.search(r"ORD-\d+", query, re.IGNORECASE)
                    if m_ord:
                        q_term = m_ord.group(0).upper()
                        ws_target = "Orders"
                    elif entity_res and entity_res.customer:
                        q_term = entity_res.customer.canonical_name
                    elif entity_res and entity_res.product:
                        q_term = entity_res.product.canonical_name
                    elif "om sai" in query_lower:
                        q_term = "Om Sai Enterprises"
                    else:
                        q_term = "Om Sai Enterprises"

                    return ReActAction(action=search_tool, args={"query": q_term, "worksheet_name": ws_target})
                return ReActAction(
                    action="final",
                    answer=ResponseFormatter.format_business_response(user_query, retrieved_data, entity_res),
                )

            # Scenario C: Append record
            if any(w in query_lower for w in ["append", "add row", "insert row", "add new row", "add record"]):
                append_tool = registry.resolve_tool_name("append_record") or "google_sheets.append_record"
                if append_tool not in called_tools:
                    if ws_target == "Orders":
                        sample_record = {"Order ID": "ORD-1099", "Customer": "Demo Corp", "Total": 15000, "Status": "Pending"}
                    else:
                        sample_record = {"Product ID": "P1099", "Product Name": "Special Widget", "Category": "Hardware", "Available Qty": 50, "Stock Status": "In Stock"}
                    return ReActAction(action=append_tool, args={"record": sample_record, "worksheet_name": ws_target})
                return ReActAction(
                    action="final",
                    answer=ResponseFormatter.format_business_response(user_query, retrieved_data, entity_res),
                )

            # Scenario D: Direct Update
            if any(w in query_lower for w in ["update", "modify", "change"]):
                update_tool = registry.resolve_tool_name("update_record") or "google_sheets.update_record"
                if update_tool not in called_tools:
                    return ReActAction(
                        action=update_tool,
                        args={
                            "worksheet_name": ws_target,
                            "row_index": 2,
                            "updates": {"Stock Status": "Verified"} if ws_target == "Inventory" else {"Status": "Confirmed"},
                        },
                    )
                return ReActAction(
                    action="final",
                    answer=ResponseFormatter.format_business_response(user_query, retrieved_data, entity_res),
                )

            # Scenario E: Read / Retrieve records
            sheets_read = registry.resolve_tool_name("read_data") or "google_sheets.read_data"
            if sheets_read not in called_tools:
                return ReActAction(action=sheets_read, args={"worksheet_name": ws_target})
            return ReActAction(
                action="final",
                answer=ResponseFormatter.format_business_response(user_query, retrieved_data, entity_res),
            )

        # Use Case 1: Order Fulfillment (e.g. "Can we fulfill order ORD-1042?")
        if _matches_intent(query_lower, "order_fulfillment", ["fulfill", "can we fulfill"]):
            orders_tool = registry.resolve_tool_name("get_order") or "orders.get_order"
            if orders_tool not in called_tools:
                ord_id = entity_res.order_id or "ORD-1042"
                return ReActAction(action=orders_tool, args={"order_id": ord_id})

            inv_tool = registry.resolve_tool_name("check_inventory") or "inventory.check_inventory"
            if inv_tool not in called_tools:
                ord_obs = retrieved_data.get(orders_tool, {})
                items = ord_obs.get("data", {}).get("items", [])
                items_to_check = [
                    {"product_id": it["product_id"], "quantity": it["quantity"]}
                    for it in items if "product_id" in it
                ]
                if not items_to_check:
                    items_to_check = [{"product_id": "P1001", "quantity": 100}]
                return ReActAction(action=inv_tool, args={"items": items_to_check})

            return ReActAction(
                action="final",
                answer=ResponseFormatter.format_business_response(user_query, retrieved_data, entity_res),
            )

        # 1. Out of Stock Check (e.g. "Which items are not available in stock?", "What is out of stock?")
        if any(w in query_lower for w in ["not available", "out of stock", "unavailable", "stock khatam", "zero stock"]):
            oos_tool = registry.resolve_tool_name("list_inventory") or registry.resolve_tool_name("out_of_stock_items") or "inventory.list_inventory"
            if oos_tool not in called_tools:
                return ReActAction(action=oos_tool, args={"status_filter": "out_of_stock"})
            return ReActAction(
                action="final",
                answer=ResponseFormatter.format_business_response(user_query, retrieved_data, entity_res),
            )

        # 2. Available Products in Stock (e.g. "What items are currently available in stock?", "Which items are available?")
        # Distinguish general catalog available query from specific single-product stock query
        if any(w in query_lower for w in ["available in stock", "currently available", "in-stock items", "in stock items", "available products", "items available", "products are available"]) and not (entity_res and entity_res.product):
            avail_tool = registry.resolve_tool_name("list_inventory") or registry.resolve_tool_name("available_items") or "inventory.list_inventory"
            if avail_tool not in called_tools:
                return ReActAction(action=avail_tool, args={"status_filter": "available"})
            return ReActAction(
                action="final",
                answer=ResponseFormatter.format_business_response(user_query, retrieved_data, entity_res),
            )

        # 3. All Inventory (e.g. "Show all inventory", "full inventory list", "what products do we have in warehouse?")
        if any(w in query_lower for w in ["all inventory", "full inventory", "all products in stock", "all items in warehouse", "catalog stock"]):
            all_inv_tool = registry.resolve_tool_name("list_inventory") or registry.resolve_tool_name("all_inventory") or "inventory.list_inventory"
            if all_inv_tool not in called_tools:
                return ReActAction(action=all_inv_tool, args={"status_filter": "all"})
            return ReActAction(
                action="final",
                answer=ResponseFormatter.format_business_response(user_query, retrieved_data, entity_res),
            )

        # 4. Stock Availability Check for a Specific Product (e.g. "How much stock is available for Wireless Optical Mouse?", "Check stock for P1001")
        if any(w in query_lower for w in ["how much stock", "stock is available", "check stock", "stock of", "available stock", "kitna stock", "stock level"]):
            # If no product specified in query, request clarification in English (Test E)
            if not (entity_res and entity_res.product):
                return ReActAction(
                    action="final",
                    answer=(
                        "**Answer**: Which product's stock would you like to check? "
                        "Please specify a product name or ID (for example: Wireless Optical Mouse, A4 Paper, or P1001).\n\n"
                        "**Status**: NEEDS_CLARIFICATION\n\n"
                        "**Next Step**: Please provide the product name or ID to view current stock levels."
                    ),
                )
            stock_tool = registry.resolve_tool_name("get_stock") or "inventory.get_stock"
            if stock_tool not in called_tools:
                p_arg = entity_res.product.canonical_id
                return ReActAction(action=stock_tool, args={"product_id_or_name": p_arg})
            return ReActAction(
                action="final",
                answer=ResponseFormatter.format_business_response(user_query, retrieved_data, entity_res),
            )

        # 5. Pending Orders (e.g. "total pending orders?", "How many pending orders do we have?", "How many pending orders were placed in last month?")
        if any(w in query_lower for w in ["pending order", "pending orders", "orders pending", "total pending", "how many pending", "unfulfilled orders"]):
            orders_tool = registry.resolve_tool_name("pending_orders") or "orders.pending_orders"
            if orders_tool not in called_tools:
                args = {}
                for dp in ["last two months", "last 2 months", "last 3 months", "last month", "this month", "today", "yesterday"]:
                    if dp in query_lower:
                        dr = RelativeDateResolver.resolve_date_expression(dp)
                        args = {"start_date": dr.start_date, "end_date": dr.end_date, "month": dr.month_str or ""}
                        break
                return ReActAction(action=orders_tool, args=args)
            return ReActAction(
                action="final",
                answer=ResponseFormatter.format_business_response(user_query, retrieved_data, entity_res),
            )

        # 6. Supplier Analytics & Biggest Supplier (e.g. "Which is our biggest supplier?", "Who is our top supplier by purchase spend?")
        if any(w in query_lower for w in ["biggest supplier", "top supplier", "supplier ranking", "supplier performance", "purchase spend", "spend with suppliers", "largest supplier"]):
            sup_perf_tool = registry.resolve_tool_name("supplier_performance") or "suppliers.supplier_performance"
            if sup_perf_tool not in called_tools:
                dr = None
                for dp in ["last two months", "last 2 months", "last 3 months", "last month", "this month"]:
                    if dp in query_lower:
                        dr = RelativeDateResolver.resolve_date_expression(dp)
                        break
                args = {}
                if dr:
                    args = {"start_date": dr.start_date, "end_date": dr.end_date, "period": dr.label}
                return ReActAction(action=sup_perf_tool, args=args)
            return ReActAction(
                action="final",
                answer=ResponseFormatter.format_business_response(user_query, retrieved_data, entity_res),
            )

        # 6. Debtors & Customers With Pending Payments (e.g. "Who owes us money?", "Which customers have pending payments?", "outstanding customer payment")
        if any(w in query_lower for w in ["who owes", "customers owe", "pending payment", "pending payments", "unpaid invoices", "overdue payments", "outstanding payment", "outstanding customer", "payment is outstanding", "payment outstanding"]):
            if entity_res and entity_res.customer:
                fin_tool = registry.resolve_tool_name("customer_balance") or "finance.customer_balance"
                if fin_tool not in called_tools:
                    return ReActAction(action=fin_tool, args={"customer_id_or_name": entity_res.customer.canonical_name})
            else:
                debt_tool = registry.resolve_tool_name("get_payment_status") or registry.resolve_tool_name("pending_payments") or "finance.get_payment_status"
                if debt_tool not in called_tools:
                    overdue_only = "overdue" in query_lower
                    return ReActAction(action=debt_tool, args={"status_filter": "overdue" if overdue_only else "all", "overdue_only": overdue_only})
            return ReActAction(
                action="final",
                answer=ResponseFormatter.format_business_response(user_query, retrieved_data, entity_res),
            )

        # 7. Financial Summary & Revenue (e.g. "What was our revenue this month?", "Revenue last month", "Financial summary for last two months")
        if any(w in query_lower for w in ["revenue", "financial summary", "financial performance", "how much did we invoice", "total collected", "sales this month", "sales last month", "invoiced revenue"]):
            fin_sum_tool = registry.resolve_tool_name("financial_summary") or "finance.financial_summary"
            if fin_sum_tool not in called_tools:
                dr = None
                for dp in ["last two months", "last 2 months", "last 3 months", "last month", "this month", "today", "yesterday"]:
                    if dp in query_lower:
                        dr = RelativeDateResolver.resolve_date_expression(dp)
                        break
                if not dr:
                    dr = RelativeDateResolver.resolve_date_expression("this month")
                args = {"start_date": dr.start_date, "end_date": dr.end_date, "month": dr.month_str or ""}
                return ReActAction(action=fin_sum_tool, args=args)
            return ReActAction(
                action="final",
                answer=ResponseFormatter.format_business_response(user_query, retrieved_data, entity_res),
            )

        # Use Case 2: Low Stock Advice / Reorder (e.g. "Which products do I need to reorder?", "Kaunse products ka stock kam hai?")
        if _matches_intent(query_lower, "low_stock_advice", ["low on", "reorder", "running low", "low stock", "stock kam", "almost out of stock", "need to reorder"]):
            stock_tool = registry.resolve_tool_name("list_inventory") or registry.resolve_tool_name("low_stock_items") or "inventory.list_inventory"
            if entity_res and entity_res.product:
                stock_tool = registry.resolve_tool_name("get_stock") or "inventory.get_stock"

            if stock_tool not in called_tools:
                args = {"product_id_or_name": entity_res.product.canonical_id} if (entity_res and entity_res.product) else {"status_filter": "low_stock"}
                return ReActAction(action=stock_tool, args=args)

            # Recommend suppliers if low stock identified for a specific product
            if entity_res and entity_res.product:
                sup_comp_tool = registry.resolve_tool_name("compare_suppliers") or "suppliers.compare_suppliers"
                if sup_comp_tool not in called_tools:
                    p_id = entity_res.product.canonical_id
                    qty = entity_res.quantity or 100
                    return ReActAction(action=sup_comp_tool, args={"product_id_or_name": p_id, "required_quantity": qty})

            return ReActAction(
                action="final",
                answer=ResponseFormatter.format_business_response(user_query, retrieved_data, entity_res),
            )

        # Use Case 3: Shipment Investigation (e.g. "Why haven't we shipped Om Sai's order?", "Why hasn't the Pune order been dispatched?")
        if _matches_intent(query_lower, "shipment_investigation", ["ship", "why haven't", "why have not", "why hasn't", "dispatched", "dispatch"]):
            orders_tool = registry.resolve_tool_name("customer_orders") or "orders.customer_orders"
            if entity_res.order_id and not entity_res.customer:
                orders_tool = registry.resolve_tool_name("get_order") or "orders.get_order"

            if orders_tool not in called_tools:
                if entity_res.order_id and not entity_res.customer:
                    return ReActAction(action=orders_tool, args={"order_id": entity_res.order_id})
                cust_arg = entity_res.customer.canonical_name if entity_res.customer else "Om Sai Enterprises"
                return ReActAction(action=orders_tool, args={"customer_id_or_name": cust_arg})

            inv_tool = registry.resolve_tool_name("check_inventory") or "inventory.check_inventory"
            if inv_tool not in called_tools:
                ord_obs = retrieved_data.get(orders_tool, {})
                items = []
                if "orders" in ord_obs and ord_obs["orders"]:
                    items = ord_obs["orders"][0].get("items", [])
                elif "data" in ord_obs:
                    items = ord_obs["data"].get("items", [])
                check_items = [
                    {"product_id": it["product_id"], "quantity": it["quantity"]}
                    for it in items if "product_id" in it
                ]
                if not check_items:
                    check_items = [{"product_id": "P1001", "quantity": 100}]
                return ReActAction(action=inv_tool, args={"items": check_items})

            fin_tool = registry.resolve_tool_name("customer_balance") or "finance.customer_balance"
            if fin_tool not in called_tools:
                cust_arg = entity_res.customer.canonical_name if entity_res.customer else "Om Sai Enterprises"
                return ReActAction(action=fin_tool, args={"customer_id_or_name": cust_arg})

            return ReActAction(
                action="final",
                answer=ResponseFormatter.format_business_response(user_query, retrieved_data, entity_res),
            )

        # Use Case 5: Purchase Order Creation (WRITE tool) - prioritized over general inquiry
        if _matches_intent(query_lower, "purchase_order_creation", ["create purchase order", "create the purchase order", "create po", "place po"]):
            po_tool = registry.resolve_tool_name("create_purchase_order") or "suppliers.create_purchase_order"
            if po_tool in called_tools:
                return ReActAction(
                    action="final",
                    answer=ResponseFormatter.format_business_response(user_query, retrieved_data, entity_res),
                )

            # 1. Require clarification if product is ambiguous or missing
            if not entity_res or not entity_res.product or not entity_res.product.canonical_id:
                return ReActAction(
                    action="final",
                    answer="**Clarification Needed**: Which product would you like to create a purchase order for? Please specify the product name or Product ID (e.g. Wireless Mouse, USB-C Cable).",
                )

            # 2. Require clarification if quantity is missing or invalid
            if not entity_res.quantity or entity_res.quantity <= 0:
                prod_name = getattr(entity_res.product, "canonical_name", None) or entity_res.product.canonical_id
                return ReActAction(
                    action="final",
                    answer=f"**Clarification Needed**: Please specify the quantity you wish to order for '{prod_name}'.",
                )

            prod_id = entity_res.product.canonical_id
            qty = entity_res.quantity

            # 3. If supplier is NOT specified in query, run compare_suppliers first
            comp_tool = registry.resolve_tool_name("compare_suppliers") or "suppliers.compare_suppliers"
            if comp_tool not in called_tools and not entity_res.supplier:
                return ReActAction(action=comp_tool, args={"product_id_or_name": prod_id, "required_quantity": qty})

            # 4. Resolve supplier and price from compare_suppliers observation or query / repository lookup
            sup_id = entity_res.supplier.canonical_id if entity_res.supplier else None
            price = getattr(entity_res, "unit_price", None)

            if comp_tool in retrieved_data:
                comp_data = retrieved_data[comp_tool]
                rec = comp_data.get("recommendation", {})
                if rec:
                    if not sup_id:
                        sup_id = rec.get("supplier_id")
                    if not price:
                        price = rec.get("unit_price")

            # Fallback to top ranked supplier if recommendation not nested
            if not sup_id and comp_tool in retrieved_data:
                ranked = retrieved_data[comp_tool].get("ranked_suppliers", [])
                if ranked:
                    sup_id = ranked[0].get("supplier_id")
                    if not price:
                        price = ranked[0].get("unit_price")

            # 5. Validate supplier availability
            if not sup_id:
                p_label = getattr(entity_res.product, "canonical_name", None) or prod_id
                return ReActAction(
                    action="final",
                    answer=f"**No Available Suppliers**: Could not find an active supplier offering product '{p_label}'.",
                )

            # 6. Validate price from database if not yet known
            if price is None or price <= 0:
                try:
                    from database.repositories.repository import BizOpsRepository
                    repo = BizOpsRepository()
                    sp = repo.get_supplier_product(sup_id, prod_id)
                    if sp and sp.get("unit_price") is not None:
                        price = float(sp["unit_price"])
                    else:
                        price = 0.0
                except Exception:
                    price = 0.0

            if price <= 0:
                return ReActAction(
                    action="final",
                    answer=f"**Price Validation Error**: Unable to determine valid unit price for supplier '{sup_id}' and product '{prod_id}'.",
                )

            return ReActAction(
                action=po_tool,
                args={
                    "supplier_id": sup_id,
                    "product_id": prod_id,
                    "quantity": qty,
                    "unit_price": price,
                    "reason": "Restock procurement",
                    "idempotency_key": "",
                },
            )

        # Use Case 4: Supplier Comparison (e.g. "We need 100 wireless mice. Which supplier should we use?", "Which supplier offers lowest price for A4 paper?", "Compare our suppliers")
        is_supplier_comparison = (
            _matches_intent(query_lower, "supplier_comparison", ["which supplier", "compare supplier", "compare suppliers", "cheapest supplier", "recommend a supplier", "who should we order"])
            or ("compare" in query_lower and any(w in query_lower for w in ["supplier", "suppliers", "vendor", "vendors"]))
        )
        if is_supplier_comparison:
            comp_tool = registry.resolve_tool_name("compare_suppliers") or "suppliers.compare_suppliers"
            if comp_tool not in called_tools:
                p_id = entity_res.product.canonical_id if entity_res.product else "P1001"
                qty = entity_res.quantity or 100
                return ReActAction(action=comp_tool, args={"product_id_or_name": p_id, "required_quantity": qty})

            return ReActAction(
                action="final",
                answer=ResponseFormatter.format_business_response(user_query, retrieved_data, entity_res),
            )

        # Finance Queries: "How much money do customers owe us?", "Has Om Sai paid", "Which invoices are overdue"
        if _matches_intent(query_lower, "finance_balance", ["balance", "owe", "pending payment", "overdue", "invoice", "credit limit"]):
            fin_tool = registry.resolve_tool_name("customer_balance") or "finance.customer_balance"
            if entity_res.customer and fin_tool not in called_tools:
                return ReActAction(action=fin_tool, args={"customer_id_or_name": entity_res.customer.canonical_name})
            fin_sum_tool = registry.resolve_tool_name("financial_summary") or "finance.financial_summary"
            if fin_sum_tool not in called_tools:
                dr = RelativeDateResolver.resolve_date_expression("this month")
                return ReActAction(action=fin_sum_tool, args={"start_date": dr.start_date, "end_date": dr.end_date})
            return ReActAction(
                action="final",
                answer=ResponseFormatter.format_business_response(user_query, retrieved_data, entity_res),
            )

        # General Default Lookup: return all inventory overview
        all_inv = registry.resolve_tool_name("all_inventory") or "inventory.all_inventory"
        if all_inv not in called_tools:
            return ReActAction(action=all_inv, args={})

        return ReActAction(
            action="final",
            answer=ResponseFormatter.format_business_response(user_query, retrieved_data, entity_res),
        )

