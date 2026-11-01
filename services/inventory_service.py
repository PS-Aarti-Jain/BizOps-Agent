from typing import Any, Dict, List, Optional
from database.models import StockInfo
from database.repositories.repository import BizOpsRepository


def classify_inventory_level(available_qty: int, reorder_level: Optional[int]) -> str:
    """
    Explicit inventory classifications:
    - OUT_OF_STOCK: available_stock == 0 (or negative)
    - LOW_STOCK: available_stock > 0 and available_stock < reorder_level (when reorder_level is present)
    - IN_STOCK: available_stock >= reorder_level (or available_stock > 0 when reorder_level is missing)
    """
    if available_qty <= 0:
        return "OUT_OF_STOCK"
    if reorder_level is not None:
        if available_qty < reorder_level:
            return "LOW_STOCK"
        return "IN_STOCK"
    return "IN_STOCK"


def calculate_available_percentage(available_qty: int, on_hand_qty: int) -> float:
    """
    Calculates the availability ratio: (available_stock / on_hand_stock) * 100.
    Handles zero, missing, and invalid quantities safely bounded between 0.0% and 100.0%.
    """
    if on_hand_qty <= 0 or available_qty <= 0:
        return 0.0
    pct = (available_qty / on_hand_qty) * 100.0
    return round(max(0.0, min(100.0, pct)), 1)


class InventoryDomainService:
    def __init__(self, repo: Optional[BizOpsRepository] = None):
        self.repo = repo or BizOpsRepository()

    def get_stock_info(self, product_id_or_name: str) -> Dict[str, Any]:
        product = self.repo.get_product(product_id_or_name)
        if not product:
            return {
                "status": "NO_DATA",
                "message": f"No product found matching '{product_id_or_name}'",
            }

        product_id = product["product_id"]
        inv = self.repo.get_inventory(product_id)
        if not inv:
            return {
                "status": "NO_DATA",
                "message": f"No inventory record found for product ID {product_id}",
            }

        on_hand = inv["on_hand_qty"]
        reserved = inv["reserved_qty"]
        allocated = inv["allocated_qty"]
        available = max(0, on_hand - reserved - allocated)
        reorder_thresh = product.get("reorder_threshold")
        classification = classify_inventory_level(available, reorder_thresh)
        is_low = (classification == "LOW_STOCK")
        is_out = (classification == "OUT_OF_STOCK")
        available_pct = calculate_available_percentage(available, on_hand)

        # Check for open incoming purchase orders
        open_pos = self.repo.get_open_purchase_orders_for_product(product_id)
        incoming_qty = sum(po["quantity"] for po in open_pos)
        po_ids = [po["po_id"] for po in open_pos]

        stock = StockInfo(
            product_id=product_id,
            product_name=product["name"],
            sku=product["sku"],
            on_hand_qty=on_hand,
            reserved_qty=reserved,
            allocated_qty=allocated,
            available_qty=available,
            incoming_qty=incoming_qty,
            open_purchase_orders=po_ids,
            reorder_threshold=reorder_thresh,
            is_low_stock=is_low,
            is_out_of_stock=is_out,
            classification=classification,
            available_pct=available_pct,
            location=inv["location"],
        )
        data = stock.model_dump()
        data["total_reserved_qty"] = max(0, on_hand - available)
        data["availability_percentage"] = available_pct
        return {"status": "SUCCESS", "data": data}

    def check_inventory_items(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        results = []
        all_fulfillable = True

        for item in items:
            p_id = item.get("product_id") or item.get("item_id")
            req_qty = item.get("quantity", 0)

            if req_qty <= 0:
                all_fulfillable = False
                results.append({
                    "product_id": p_id,
                    "requested_qty": req_qty,
                    "available_qty": 0,
                    "fulfillable": False,
                    "shortage": 0,
                    "classification": "INVALID_REQUEST",
                    "reason": "INVALID_REQUEST: Quantity must be a positive integer greater than zero.",
                })
                continue

            stock_res = self.get_stock_info(p_id)
            if stock_res["status"] != "SUCCESS":
                all_fulfillable = False
                results.append({
                    "product_id": p_id,
                    "requested_qty": req_qty,
                    "available_qty": 0,
                    "fulfillable": False,
                    "shortage": req_qty,
                    "classification": "NOT_FOUND",
                    "reason": stock_res["message"],
                })
                continue

            st = stock_res["data"]
            avail = st["available_qty"]
            fulfillable = avail >= req_qty
            shortage = max(0, req_qty - avail)
            if not fulfillable:
                all_fulfillable = False
                classification = "INSUFFICIENT_FOR_REQUEST"
            else:
                classification = st.get("classification", "IN_STOCK")

            results.append({
                "product_id": st["product_id"],
                "product_name": st["product_name"],
                "requested_qty": req_qty,
                "available_qty": avail,
                "incoming_qty": st.get("incoming_qty", 0),
                "open_purchase_orders": st.get("open_purchase_orders", []),
                "fulfillable": fulfillable,
                "shortage": shortage,
                "classification": classification,
            })

        return {
            "status": "SUCCESS",
            "all_fulfillable": all_fulfillable,
            "total_count": len(results),
            "returned_count": len(results),
            "is_complete": True,
            "items": results,
        }

    def get_all_inventory_items(self) -> Dict[str, Any]:
        """Returns all inventory items with full quantities, threshold, and explicit classification."""
        all_inv = self.repo.get_all_inventory()
        items = []

        for inv in all_inv:
            on_hand = inv["on_hand_qty"]
            reserved = inv.get("reserved_qty", 0)
            allocated = inv.get("allocated_qty", 0)
            available = max(0, on_hand - reserved - allocated)
            threshold = inv.get("reorder_threshold")
            classification = classify_inventory_level(available, threshold)
            is_low = (classification == "LOW_STOCK")
            is_out = (classification == "OUT_OF_STOCK")
            avail_pct = calculate_available_percentage(available, on_hand)

            open_pos = self.repo.get_open_purchase_orders_for_product(inv["product_id"])
            incoming_qty = sum(po["quantity"] for po in open_pos)

            items.append({
                "product_id": inv["product_id"],
                "product_name": inv["product_name"],
                "sku": inv.get("sku", ""),
                "category": inv.get("category", "General"),
                "unit_cost": float(inv.get("unit_cost", 0.0)),
                "selling_price": float(inv.get("selling_price", 0.0)),
                "on_hand_qty": on_hand,
                "reserved_qty": reserved,
                "allocated_qty": allocated,
                "total_reserved_qty": max(0, on_hand - available),
                "available_qty": available,
                "incoming_qty": incoming_qty,
                "reorder_threshold": threshold,
                "classification": classification,
                "is_low_stock": is_low,
                "is_out_of_stock": is_out,
                "available_pct": avail_pct,
                "availability_percentage": avail_pct,
                "location": inv.get("location", "Warehouse"),
            })

        return {
            "status": "SUCCESS",
            "total_count": len(items),
            "returned_count": len(items),
            "is_complete": True,
            "items": items,
        }

    def list_inventory(self, status_filter: str = "all", category: Optional[str] = None) -> Dict[str, Any]:
        """
        Consolidated inventory catalog tool with optional classification filtering:
        - "all": full warehouse inventory catalog
        - "low_stock": products at or below reorder threshold
        - "out_of_stock": products with zero available stock
        - "available" / "in_stock": products with positive available stock
        """
        sf = (status_filter or "all").strip().lower()
        if sf in ("low_stock", "low", "low-stock"):
            res = self.get_low_stock_items()
        elif sf in ("out_of_stock", "out", "oos", "out-of-stock"):
            res = self.get_out_of_stock_items()
        elif sf in ("available", "in_stock", "in-stock"):
            res = self.get_available_items()
        else:
            res = self.get_all_inventory_items()

        if category and res.get("status") == "SUCCESS":
            cat_norm = category.strip().lower()
            filtered = [it for it in res.get("items", []) if cat_norm in it.get("category", "").lower()]
            return {
                "status": "SUCCESS",
                "total_count": len(filtered),
                "returned_count": len(filtered),
                "is_complete": True,
                "items": filtered,
            }
        return res

    def get_available_items(self) -> Dict[str, Any]:
        """Returns only products that currently have positive available stock (available > 0)."""
        all_res = self.get_all_inventory_items()
        avail_items = [it for it in all_res["items"] if it["available_qty"] > 0]
        return {
            "status": "SUCCESS",
            "total_count": len(avail_items),
            "returned_count": len(avail_items),
            "is_complete": True,
            "items": avail_items,
        }

    def get_out_of_stock_items(self) -> Dict[str, Any]:
        """Returns only products that are completely out of stock (available == 0)."""
        all_res = self.get_all_inventory_items()
        oos_items = [it for it in all_res["items"] if it["classification"] == "OUT_OF_STOCK"]
        return {
            "status": "SUCCESS",
            "total_count": len(oos_items),
            "returned_count": len(oos_items),
            "is_complete": True,
            "items": oos_items,
        }

    def get_low_stock_items(self) -> Dict[str, Any]:
        """Returns products whose available stock is positive but below reorder threshold."""
        all_res = self.get_all_inventory_items()
        low_items = [
            it for it in all_res["items"]
            if it["classification"] == "LOW_STOCK"
        ]
        return {
            "status": "SUCCESS",
            "total_count": len(low_items),
            "returned_count": len(low_items),
            "is_complete": True,
            "items": low_items,
        }

    def receive_goods(self, po_id: str, received_by: str = "Warehouse Manager") -> Dict[str, Any]:
        return self.repo.receive_goods(po_id=po_id, received_by=received_by)
