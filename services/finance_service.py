from decimal import Decimal
from typing import Any, Dict, List, Optional
from database.connection import get_db_connection
from database.repositories.repository import BizOpsRepository
from services.date_resolver import RelativeDateResolver, get_current_date


class FinanceDomainService:
    def __init__(self, repo: Optional[BizOpsRepository] = None):
        self.repo = repo or BizOpsRepository()

    def get_customer_balance(self, customer_id_or_name: str) -> Dict[str, Any]:
        customer = self.repo.get_customer(customer_id_or_name)
        if not customer:
            return {
                "status": "NO_DATA",
                "message": f"Customer '{customer_id_or_name}' not found.",
            }

        bal_info = self.repo.get_customer_balance(customer["customer_id"])
        if not bal_info:
            return {
                "status": "NO_DATA",
                "message": f"Could not calculate financial balance for '{customer['name']}'.",
            }

        overdue_amt = bal_info["overdue_amount"]
        has_overdue = overdue_amt > 0
        exceeds_credit = bal_info["total_outstanding"] > bal_info["credit_limit"]
        credit_hold = has_overdue or exceeds_credit

        reason = (
            f"Has overdue unpaid invoices totaling ₹{overdue_amt:,.2f}."
            if has_overdue
            else (
                f"Outstanding balance (₹{bal_info['total_outstanding']:,.2f}) exceeds credit limit (₹{bal_info['credit_limit']:,.2f})."
                if exceeds_credit
                else "Account in good standing with positive credit available."
            )
        )

        return {
            "status": "SUCCESS",
            "customer_id": customer["customer_id"],
            "customer_name": customer["name"],
            "credit_limit": bal_info["credit_limit"],
            "outstanding_balance": bal_info["total_outstanding"],
            "overdue_amount": overdue_amt,
            "available_credit": bal_info["available_credit"],
            "customer_status": customer["status"],
            "credit_hold_recommended": credit_hold,
            "reason": reason,
            "unpaid_invoices": bal_info.get("unpaid_invoices", []),
            "unpaid_invoice_count": len(bal_info.get("unpaid_invoices", [])),
        }

    def get_invoice_status(self, invoice_id: str) -> Dict[str, Any]:
        conn = get_db_connection(self.repo.db_path)
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT inv.*, c.name as customer_name
                FROM invoices inv
                LEFT JOIN customers c ON inv.customer_id = c.customer_id
                WHERE inv.invoice_id = ?;
            """, (invoice_id.strip(),))
            inv = cur.fetchone()
            if not inv:
                return {
                    "status": "NO_DATA",
                    "message": f"Invoice '{invoice_id}' not found in records.",
                }

            tot = Decimal(str(inv["total_amount"]))
            paid = Decimal(str(inv["paid_amount"]))
            balance = float(tot - paid)

            return {
                "status": "SUCCESS",
                "invoice_id": inv["invoice_id"],
                "order_id": inv["order_id"],
                "customer_id": inv["customer_id"],
                "customer_name": inv["customer_name"],
                "invoice_date": inv["invoice_date"],
                "due_date": inv["due_date"],
                "total_amount": float(tot),
                "paid_amount": float(paid),
                "remaining_balance": balance,
                "invoice_status": inv["status"],
            }
        finally:
            conn.close()

    def get_payment_status(self, status_filter: str = "all", overdue_only: bool = False) -> Dict[str, Any]:
        """
        Retrieves customer debtor status and unpaid invoices with optional status filter:
        - "all": all unpaid invoices and customer balances
        - "overdue": only overdue invoices and customers with overdue balances
        - "pending": pending / current unpaid invoices
        """
        is_overdue = overdue_only or (status_filter and status_filter.strip().lower() in ("overdue", "overdue_only"))
        return self.get_pending_payments(overdue_only=is_overdue)

    def get_pending_payments(self, overdue_only: bool = False) -> Dict[str, Any]:
        """
        Calculates customer outstanding balances strictly from invoices and payments.
        Distinguishes unpaid invoices from pending orders.
        """
        conn = get_db_connection(self.repo.db_path)
        try:
            cur = conn.cursor()
            query = """
                SELECT inv.*, c.name as customer_name, c.phone, c.credit_limit
                FROM invoices inv
                JOIN customers c ON inv.customer_id = c.customer_id
                WHERE inv.total_amount > inv.paid_amount
            """
            params = []
            if overdue_only:
                query += " AND inv.status = 'Overdue'"
            query += " ORDER BY inv.due_date ASC;"

            cur.execute(query, params)
            unpaid_invoices = cur.fetchall()

            customers_map: Dict[str, Dict[str, Any]] = {}
            total_outstanding = Decimal("0.00")
            total_overdue = Decimal("0.00")

            invoice_records = []
            for inv in unpaid_invoices:
                tot = Decimal(str(inv["total_amount"]))
                paid = Decimal(str(inv["paid_amount"]))
                rem = tot - paid
                total_outstanding += rem

                is_overdue = (inv["status"] == "Overdue")
                if is_overdue:
                    total_overdue += rem

                c_id = inv["customer_id"]
                if c_id not in customers_map:
                    customers_map[c_id] = {
                        "customer_id": c_id,
                        "customer_name": inv["customer_name"],
                        "phone": inv["phone"],
                        "credit_limit": float(Decimal(str(inv["credit_limit"]))),
                        "total_outstanding": Decimal("0.00"),
                        "overdue_amount": Decimal("0.00"),
                        "unpaid_invoices": [],
                    }
                customers_map[c_id]["total_outstanding"] += rem
                if is_overdue:
                    customers_map[c_id]["overdue_amount"] += rem

                inv_dict = {
                    "invoice_id": inv["invoice_id"],
                    "order_id": inv["order_id"],
                    "customer_id": c_id,
                    "customer_name": inv["customer_name"],
                    "invoice_date": inv["invoice_date"],
                    "due_date": inv["due_date"],
                    "total_amount": float(tot),
                    "paid_amount": float(paid),
                    "remaining_balance": float(rem),
                    "status": inv["status"],
                }
                customers_map[c_id]["unpaid_invoices"].append(inv_dict)
                invoice_records.append(inv_dict)

            # Format customer debtor summary
            debtor_list = []
            for c_id, c_data in customers_map.items():
                debtor_list.append({
                    "customer_id": c_data["customer_id"],
                    "customer_name": c_data["customer_name"],
                    "phone": c_data["phone"],
                    "credit_limit": c_data["credit_limit"],
                    "total_outstanding": float(c_data["total_outstanding"]),
                    "overdue_amount": float(c_data["overdue_amount"]),
                    "unpaid_invoice_count": len(c_data["unpaid_invoices"]),
                    "invoices": c_data["unpaid_invoices"],
                })

            return {
                "status": "SUCCESS",
                "debtor_count": len(debtor_list),
                "unpaid_invoice_count": len(invoice_records),
                "total_outstanding_amount": float(total_outstanding),
                "total_overdue_amount": float(total_overdue),
                "debtors": debtor_list,
                "invoices": invoice_records,
                "is_complete": True,
                "source": "invoices_and_payments_ledger",
            }
        finally:
            conn.close()

    def get_financial_summary(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        month: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Calculates invoiced revenue, collected payments, and outstanding receivables for a date range.
        Uses exact Decimal arithmetic. Does NOT count pending or cancelled orders as revenue.
        """
        # Resolve date range
        if month and not (start_date and end_date):
            dr = RelativeDateResolver.resolve_date_expression(month)
            if dr:
                start_date = dr.start_date
                end_date = dr.end_date
            else:
                start_date = f"{month}-01"
                end_date = f"{month}-31"

        if not start_date or not end_date:
            # Default to current month in Asia/Kolkata
            dr = RelativeDateResolver.resolve_date_expression("this month")
            start_date = dr.start_date
            end_date = dr.end_date

        conn = get_db_connection(self.repo.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM invoices
                WHERE invoice_date >= ? AND invoice_date <= ?
                ORDER BY invoice_date ASC;
                """,
                (start_date, end_date),
            )
            invoices = cursor.fetchall()
            tot_inv = Decimal("0.00")
            tot_coll = Decimal("0.00")
            for inv in invoices:
                tot_inv += Decimal(str(inv["total_amount"]))
                tot_coll += Decimal(str(inv["paid_amount"]))
            tot_out = tot_inv - tot_coll

            return {
                "status": "SUCCESS",
                "start_date": start_date,
                "end_date": end_date,
                "period": f"{start_date} to {end_date}",
                "invoice_count": len(invoices),
                "record_count": len(invoices),
                "total_invoiced": float(tot_inv),
                "total_collected": float(tot_coll),
                "total_outstanding": float(tot_out),
                "is_complete": True,
                "is_empty": (len(invoices) == 0),
            }
        finally:
            conn.close()

    def get_monthly_financial_summary(self, month: str) -> Dict[str, Any]:
        """Backward compatibility wrapper delegating to get_financial_summary."""
        dr = RelativeDateResolver.resolve_date_expression(month)
        if dr:
            return self.get_financial_summary(start_date=dr.start_date, end_date=dr.end_date)
        return self.get_financial_summary(month=month)
