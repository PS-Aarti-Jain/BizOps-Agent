from typing import Any, Dict, List, Optional
from database.repositories.repository import BizOpsRepository


class OrderDomainService:
    def __init__(self, repo: Optional[BizOpsRepository] = None):
        self.repo = repo or BizOpsRepository()

    def get_order_details(self, order_id: str) -> Dict[str, Any]:
        order = self.repo.get_order(order_id)
        if not order:
            return {
                "status": "NO_DATA",
                "message": f"Order ID '{order_id}' was not found in the database.",
            }
        return {"status": "SUCCESS", "data": order}

    def get_pending_orders(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        month: Optional[str] = None,
    ) -> Dict[str, Any]:
        conn = self.repo.get_customer_orders  # ensure connection
        from database.connection import get_db_connection
        conn = get_db_connection(self.repo.db_path)
        try:
            cur = conn.cursor()
            query = "SELECT * FROM orders WHERE status IN ('Pending', 'Processing')"
            params: List[Any] = []
            if month:
                query += " AND order_date LIKE ?"
                params.append(f"{month}%")
            elif start_date and end_date:
                query += " AND order_date >= ? AND order_date <= ?"
                params.extend([start_date, end_date])
            elif start_date:
                query += " AND order_date >= ?"
                params.append(start_date)
            elif end_date:
                query += " AND order_date <= ?"
                params.append(end_date)
            query += " ORDER BY order_date DESC;"
            cur.execute(query, tuple(params))
            pending = cur.fetchall()
            for o in pending:
                cur.execute("SELECT oi.*, p.name as product_name FROM order_items oi JOIN products p ON oi.product_id = p.product_id WHERE oi.order_id = ?;", (o["order_id"],))
                o["items"] = cur.fetchall()
            period_label = f" for period {month or f'{start_date} to {end_date}'}" if (month or start_date or end_date) else ""
            pending_orders = [o for o in pending if o.get("status") == "Pending"]
            processing_orders = [o for o in pending if o.get("status") == "Processing"]
            total_active_amt = sum(float(o.get("total_amount", 0.0)) for o in pending)
            pending_amt = sum(float(o.get("total_amount", 0.0)) for o in pending_orders)
            processing_amt = sum(float(o.get("total_amount", 0.0)) for o in processing_orders)

            if not pending:
                return {
                    "status": "EMPTY_RESULT",
                    "message": f"There are currently zero pending or processing orders{period_label}.",
                    "count": 0,
                    "total_active_amount": 0.0,
                    "pending_count": 0,
                    "pending_amount": 0.0,
                    "processing_count": 0,
                    "processing_amount": 0.0,
                    "orders": [],
                    "start_date": start_date,
                    "end_date": end_date,
                    "month": month,
                }
            return {
                "status": "SUCCESS",
                "count": len(pending),
                "total_active_amount": total_active_amt,
                "pending_count": len(pending_orders),
                "pending_amount": pending_amt,
                "processing_count": len(processing_orders),
                "processing_amount": processing_amt,
                "orders": pending,
                "start_date": start_date,
                "end_date": end_date,
                "month": month,
            }
        finally:
            conn.close()

    def get_customer_orders(self, customer_id_or_name: str) -> Dict[str, Any]:
        customer = self.repo.get_customer(customer_id_or_name)
        cust_id = customer["customer_id"] if customer else customer_id_or_name
        cust_name = customer["name"] if customer else customer_id_or_name
        orders = self.repo.get_customer_orders(cust_id)

        if not orders:
            return {
                "status": "EMPTY_RESULT",
                "message": f"No orders found for customer '{cust_name}'.",
                "customer_name": cust_name,
                "count": 0,
                "orders": [],
            }
        return {
            "status": "SUCCESS",
            "customer_name": cust_name,
            "count": len(orders),
            "orders": orders,
        }

