import json
import logging
import sqlite3
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional
from database.connection import get_db_connection

logger = logging.getLogger(__name__)


class BizOpsRepository:
    def __init__(self, db_path=None):
        self.db_path = db_path

    # --- Product & Inventory ---
    def get_product(self, product_id_or_name: str) -> Optional[Dict[str, Any]]:
        conn = get_db_connection(self.db_path)
        try:
            cursor = conn.cursor()
            query_str = product_id_or_name.strip()
            norm_str = query_str.lower().replace("mice", "mouse")
            cursor.execute(
                """
                SELECT * FROM products
                WHERE product_id = ? OR LOWER(name) LIKE ? OR LOWER(sku) LIKE ?
                   OR LOWER(name) LIKE ? OR LOWER(sku) LIKE ?;
            """,
                (
                    query_str,
                    f"%{query_str.lower()}%",
                    f"%{query_str.lower()}%",
                    f"%{norm_str}%",
                    f"%{norm_str}%",
                ),
            )
            res = cursor.fetchone()
            if res:
                return res

            # Token-based search for partial matches (e.g. "wireless mice", "a4 paper", "oil")
            tokens = [t for t in norm_str.split() if len(t) > 2]
            if tokens:
                cursor.execute("SELECT * FROM products;")
                all_prods = cursor.fetchall()
                for p in all_prods:
                    p_name = p["name"].lower()
                    if all(t in p_name for t in tokens):
                        return p
                best_match = None
                best_score = 0
                for p in all_prods:
                    p_name = p["name"].lower()
                    score = sum(1 for t in tokens if t in p_name)
                    if score > best_score:
                        best_score = score
                        best_match = p
                if best_score > 0:
                    return best_match
            return None
        finally:
            conn.close()

    def get_inventory(self, product_id: str) -> Optional[Dict[str, Any]]:
        conn = get_db_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT i.*, p.name as product_name, p.sku, p.reorder_threshold
                FROM inventory i
                JOIN products p ON i.product_id = p.product_id
                WHERE i.product_id = ?;
            """,
                (product_id,),
            )
            return cursor.fetchone()
        finally:
            conn.close()

    def get_low_stock_items(self) -> List[Dict[str, Any]]:
        all_inv = self.get_all_inventory()
        return [
            i
            for i in all_inv
            if max(0, i["on_hand_qty"] - i["reserved_qty"] - i["allocated_qty"])
            <= i["reorder_threshold"]
        ]

    def get_all_inventory(self) -> List[Dict[str, Any]]:
        conn = get_db_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT i.*, p.name as product_name, p.sku, p.category, p.unit_cost, p.selling_price, p.reorder_threshold
                FROM inventory i
                JOIN products p ON i.product_id = p.product_id
                ORDER BY p.product_id ASC;
            """)
            return cursor.fetchall()
        finally:
            conn.close()

    # --- Orders ---
    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        conn = get_db_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM orders WHERE order_id = ?;", (order_id.strip(),))
            order = cursor.fetchone()
            if not order:
                return None
            cursor.execute("SELECT * FROM order_items WHERE order_id = ?;", (order_id.strip(),))
            order["items"] = cursor.fetchall()
            return order
        finally:
            conn.close()

    def get_order_items(self, order_id: str) -> List[Dict[str, Any]]:
        conn = get_db_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT oi.*, p.name as product_name, p.sku
                FROM order_items oi
                JOIN products p ON oi.product_id = p.product_id
                WHERE oi.order_id = ?;
            """,
                (order_id.strip(),),
            )
            return cursor.fetchall()
        finally:
            conn.close()

    def get_customer_orders(self, customer_id: str) -> List[Dict[str, Any]]:
        conn = get_db_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM orders
                WHERE customer_id = ?
                ORDER BY order_date DESC;
            """,
                (customer_id.strip(),),
            )
            orders = cursor.fetchall()
            for o in orders:
                cursor.execute("SELECT * FROM order_items WHERE order_id = ?;", (o["order_id"],))
                o["items"] = cursor.fetchall()
            return orders
        finally:
            conn.close()

    # --- Customers & Finance ---
    def get_customer(self, customer_id_or_name: str) -> Optional[Dict[str, Any]]:
        conn = get_db_connection(self.db_path)
        try:
            cursor = conn.cursor()
            query_str = customer_id_or_name.strip()
            cursor.execute(
                """
                SELECT * FROM customers
                WHERE customer_id = ? OR LOWER(name) = ? OR LOWER(name) LIKE ?;
            """,
                (query_str, query_str.lower(), f"%{query_str.lower()}%"),
            )
            return cursor.fetchone()
        finally:
            conn.close()

    def get_customer_balance(self, customer_id: str) -> Optional[Dict[str, Any]]:
        conn = get_db_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM customers WHERE customer_id = ?;", (customer_id.strip(),))
            customer = cursor.fetchone()
            if not customer:
                return None

            cursor.execute(
                """
                SELECT * FROM invoices
                WHERE customer_id = ?;
            """,
                (customer_id.strip(),),
            )
            invoices = cursor.fetchall()

            total_outstanding = Decimal("0.00")
            overdue_amount = Decimal("0.00")
            unpaid_invoices = []

            for inv in invoices:
                tot = Decimal(str(inv["total_amount"]))
                paid = Decimal(str(inv["paid_amount"]))
                rem = tot - paid
                if rem > 0:
                    total_outstanding += rem
                    inv_dict = dict(inv)
                    inv_dict["remaining_balance"] = float(rem)
                    unpaid_invoices.append(inv_dict)
                    if inv["status"] == "Overdue":
                        overdue_amount += rem

            credit_limit = Decimal(str(customer["credit_limit"]))
            available_credit = max(Decimal("0.00"), credit_limit - total_outstanding)
            credit_hold = (
                customer["status"] == "Overdue"
                or total_outstanding > credit_limit
                or overdue_amount > 0
            )

            return {
                "customer_id": customer["customer_id"],
                "name": customer["name"],
                "status": customer["status"],
                "credit_limit": float(credit_limit),
                "total_outstanding": float(total_outstanding),
                "overdue_amount": float(overdue_amount),
                "available_credit": float(available_credit),
                "credit_hold": credit_hold,
                "unpaid_invoices": unpaid_invoices,
            }
        finally:
            conn.close()

    def get_all_invoices(self) -> List[Dict[str, Any]]:
        conn = get_db_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT inv.*, c.name as customer_name
                FROM invoices inv
                LEFT JOIN customers c ON inv.customer_id = c.customer_id
                ORDER BY inv.due_date DESC;
            """)
            return cursor.fetchall()
        finally:
            conn.close()

    # --- Suppliers ---
    def get_supplier(self, supplier_id_or_name: str) -> Optional[Dict[str, Any]]:
        conn = get_db_connection(self.db_path)
        try:
            cursor = conn.cursor()
            q = supplier_id_or_name.strip()
            cursor.execute(
                """
                SELECT * FROM suppliers
                WHERE supplier_id = ? OR LOWER(name) = ? OR LOWER(name) LIKE ?;
            """,
                (q, q.lower(), f"%{q.lower()}%"),
            )
            return cursor.fetchone()
        finally:
            conn.close()

    def get_supplier_products(self, product_id: str) -> List[Dict[str, Any]]:
        conn = get_db_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT sp.*, s.name as supplier_name, s.contact_email, s.rating, s.reliability_score
                FROM supplier_products sp
                JOIN suppliers s ON sp.supplier_id = s.supplier_id
                WHERE sp.product_id = ?;
            """,
                (product_id.strip(),),
            )
            return cursor.fetchall()
        finally:
            conn.close()

    def get_supplier_product(self, supplier_id: str, product_id: str) -> Optional[Dict[str, Any]]:
        conn = get_db_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT sp.*, s.name as supplier_name, s.contact_email, s.rating, s.reliability_score
                FROM supplier_products sp
                JOIN suppliers s ON sp.supplier_id = s.supplier_id
                WHERE sp.supplier_id = ? AND sp.product_id = ?;
            """,
                (supplier_id.strip(), product_id.strip()),
            )
            return cursor.fetchone()
        finally:
            conn.close()

    def get_all_suppliers(self) -> List[Dict[str, Any]]:
        conn = get_db_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM suppliers ORDER BY rating DESC;")
            return cursor.fetchall()
        finally:
            conn.close()

    # --- Purchase Orders & Goods Receipt ---
    def get_open_purchase_orders_for_product(self, product_id: str) -> List[Dict[str, Any]]:
        conn = get_db_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT po.po_id, po.status, poi.quantity, po.created_at
                FROM purchase_orders po
                JOIN purchase_order_items poi ON po.po_id = poi.po_id
                WHERE poi.product_id = ? AND po.status IN ('Created', 'Approved');
            """,
                (product_id.strip(),),
            )
            return cursor.fetchall()
        finally:
            conn.close()

    def create_purchase_order(
        self,
        supplier_id: str,
        items: List[Dict[str, Any]],
        created_by: str,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        conn = get_db_connection(self.db_path)
        try:
            cursor = conn.cursor()

            # Check idempotency
            cursor.execute(
                "SELECT * FROM purchase_orders WHERE idempotency_key = ?;",
                (idempotency_key,),
            )
            existing = cursor.fetchone()
            if existing:
                return {
                    "status": "ALREADY_EXISTS",
                    "po_id": existing["po_id"],
                    "message": f"Purchase order with idempotency key '{idempotency_key}' already exists.",
                    "total_amount": existing["total_amount"],
                    "created_at": existing["created_at"],
                }

            # Generate PO ID
            import uuid
            po_id = f"PO-{uuid.uuid4().hex[:6].upper()}"

            total_amount = Decimal("0.00")
            validated_items = []
            for it in items:
                qty = int(it["quantity"])
                price = Decimal(str(it["unit_price"]))
                subtotal = (Decimal(qty) * price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                total_amount += subtotal
                validated_items.append({
                    "item_id": f"POI-{uuid.uuid4().hex[:6].upper()}",
                    "product_id": it["product_id"],
                    "quantity": qty,
                    "unit_price": float(price),
                    "subtotal": float(subtotal),
                })

            cursor.execute(
                """
                INSERT INTO purchase_orders (po_id, supplier_id, status, total_amount, created_by, idempotency_key)
                VALUES (?, ?, 'Approved', ?, ?, ?);
            """,
                (po_id, supplier_id, float(total_amount), created_by, idempotency_key),
            )

            for vi in validated_items:
                cursor.execute(
                    """
                    INSERT INTO purchase_order_items (item_id, po_id, product_id, quantity, unit_price, subtotal)
                    VALUES (?, ?, ?, ?, ?, ?);
                """,
                    (
                        vi["item_id"],
                        po_id,
                        vi["product_id"],
                        vi["quantity"],
                        vi["unit_price"],
                        vi["subtotal"],
                    ),
                )

            conn.commit()
            return {
                "status": "SUCCESS",
                "po_id": po_id,
                "supplier_id": supplier_id,
                "total_amount": float(total_amount),
                "items": validated_items,
            }
        finally:
            conn.close()

    def check_idempotency_key(self, idempotency_key: str) -> Optional[Dict[str, Any]]:
        conn = get_db_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM purchase_orders WHERE idempotency_key = ?;", (idempotency_key,))
            return cursor.fetchone()
        finally:
            conn.close()

    def create_purchase_order_record(
        self,
        po_id: str,
        supplier_id: str,
        product_id: str,
        quantity: int,
        unit_price: float,
        total_amount: float,
        created_by: str,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        conn = get_db_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO purchase_orders (po_id, supplier_id, status, total_amount, created_by, idempotency_key)
                VALUES (?, ?, 'Approved', ?, ?, ?);
            """,
                (po_id, supplier_id, float(total_amount), created_by, idempotency_key),
            )
            import uuid
            item_id = f"POI-{uuid.uuid4().hex[:6].upper()}"
            cursor.execute(
                """
                INSERT INTO purchase_order_items (item_id, po_id, product_id, quantity, unit_price, subtotal)
                VALUES (?, ?, ?, ?, ?, ?);
            """,
                (item_id, po_id, product_id, quantity, unit_price, total_amount),
            )
            conn.commit()
            return {
                "po_id": po_id,
                "supplier_id": supplier_id,
                "product_id": product_id,
                "quantity": quantity,
                "unit_price": unit_price,
                "total_amount": total_amount,
                "status": "Approved",
                "idempotency_key": idempotency_key,
            }
        finally:
            conn.close()

    def get_supplier_purchase_history(
        self, start_date: Optional[str] = None, end_date: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        conn = get_db_connection(self.db_path)
        try:
            cursor = conn.cursor()
            query = """
                SELECT 
                    s.supplier_id,
                    s.name as supplier_name,
                    s.city,
                    s.state,
                    s.rating,
                    s.reliability_score,
                    po.po_id,
                    po.status as po_status,
                    po.total_amount,
                    po.created_at,
                    COALESCE(SUM(poi.quantity), 0) as total_qty
                FROM suppliers s
                JOIN purchase_orders po ON s.supplier_id = po.supplier_id
                LEFT JOIN purchase_order_items poi ON po.po_id = poi.po_id
            """
            params = []
            if start_date and end_date:
                query += " WHERE po.created_at >= ? AND po.created_at <= ?"
                params.extend([f"{start_date} 00:00:00", f"{end_date} 23:59:59"])
            query += " GROUP BY po.po_id, s.supplier_id ORDER BY po.created_at DESC;"
            cursor.execute(query, params)
            return cursor.fetchall()
        finally:
            conn.close()

    def receive_goods(self, po_id: str, received_by: str = "Warehouse Manager") -> Dict[str, Any]:
        conn = get_db_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM purchase_orders WHERE po_id = ?;", (po_id.strip(),))
            po = cursor.fetchone()
            if not po:
                return {"status": "NOT_FOUND", "message": f"Purchase order '{po_id}' not found."}

            if po["status"] == "Fulfilled":
                return {
                    "status": "ALREADY_FULFILLED",
                    "po_id": po_id,
                    "message": f"Purchase order '{po_id}' has already been fulfilled.",
                }

            if po["status"] == "Cancelled":
                return {
                    "status": "ERROR",
                    "po_id": po_id,
                    "message": f"Purchase order '{po_id}' has been cancelled and cannot be received.",
                }

            cursor.execute("SELECT * FROM purchase_order_items WHERE po_id = ?;", (po_id.strip(),))
            items = cursor.fetchall()

            for it in items:
                cursor.execute(
                    """
                    UPDATE inventory
                    SET on_hand_qty = on_hand_qty + ?, updated_at = datetime('now')
                    WHERE product_id = ?;
                """,
                    (it["quantity"], it["product_id"]),
                )

            cursor.execute(
                "UPDATE purchase_orders SET status = 'Fulfilled' WHERE po_id = ?;",
                (po_id.strip(),),
            )
            conn.commit()

            return {
                "status": "SUCCESS",
                "po_id": po_id,
                "received_by": received_by,
                "items_received": [
                    {"product_id": it["product_id"], "quantity": it["quantity"]} for it in items
                ],
            }
        finally:
            conn.close()

    # --- Audit Log ---
    def log_audit_entry(
        self,
        session_id: str,
        action_id: str,
        user_id: str,
        user_role: str,
        tool_name: str,
        arguments: Dict[str, Any],
        risk_level: str,
        confirmation_status: str,
        user_confirmed_at: Optional[str],
        execution_status: str,
        result: Dict[str, Any],
        idempotency_key: str,
    ) -> None:
        conn = get_db_connection(self.db_path)
        try:
            import uuid
            audit_id = f"AUDIT-{uuid.uuid4().hex[:8].upper()}"
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO audit_log (
                    audit_id, session_id, action_id, user_id, user_role, tool_name,
                    arguments_json, risk_level, confirmation_status, user_confirmed_at,
                    execution_status, result_json, idempotency_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
                (
                    audit_id,
                    session_id,
                    action_id,
                    user_id,
                    user_role,
                    tool_name,
                    json.dumps(arguments),
                    risk_level,
                    confirmation_status,
                    user_confirmed_at,
                    execution_status,
                    json.dumps(result),
                    idempotency_key,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def get_audit_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        conn = get_db_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?;",
                (limit,),
            )
            return cursor.fetchall()
        finally:
            conn.close()

