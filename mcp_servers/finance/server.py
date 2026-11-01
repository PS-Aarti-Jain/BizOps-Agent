import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from mcp.server.fastmcp import FastMCP
from services.finance_service import FinanceDomainService

mcp = FastMCP("Finance MCP Server")
service = FinanceDomainService()


@mcp.resource("finance://credit-policy")
def get_credit_policy() -> str:
    """Returns official commercial credit-hold policy, delinquency thresholds, and terms."""
    return (
        "Finance Credit Policy: Accounts with any unpaid invoice overdue by > 30 calendar days "
        "or with total outstanding balance exceeding established credit limit are automatically placed on CREDIT_HOLD. "
        "No pending warehouse orders may be dispatched for delivery until past-due balances are cleared "
        "or a temporary credit extension is authorized in writing by the Finance Director."
    )


@mcp.tool()
def customer_balance(customer_id_or_name: str) -> dict:
    """Retrieve financial status for a customer including outstanding balance, credit limit, and overdue invoices.

    Metadata: {"operation_type": "read", "risk_level": "none", "requires_confirmation": false, "required_role": "Operator", "idempotent": true, "domain": "finance"}
    """
    return service.get_customer_balance(customer_id_or_name)


@mcp.tool()
def get_payment_status(status_filter: str = "all", overdue_only: bool = False) -> dict:
    """Retrieve customer payment status, accounts receivable, and unpaid invoices, with optional status filter ('all', 'overdue', 'pending').

    Metadata: {"operation_type": "read", "risk_level": "none", "requires_confirmation": false, "required_role": "Operator", "idempotent": true, "domain": "finance"}
    """
    return service.get_payment_status(status_filter=status_filter, overdue_only=overdue_only)


@mcp.tool()
def financial_summary(start_date: str = "", end_date: str = "", month: str = "") -> dict:
    """Retrieve financial performance summary (total invoiced revenue, collected payments, and outstanding receivables) for a date range (YYYY-MM-DD) or relative expression ('this month', 'last month', 'last two months').

    Metadata: {"operation_type": "read", "risk_level": "none", "requires_confirmation": false, "required_role": "Operator", "idempotent": true, "domain": "finance"}
    """
    s_date = start_date if start_date else None
    e_date = end_date if end_date else None
    m_val = month if month else None
    return service.get_financial_summary(start_date=s_date, end_date=e_date, month=m_val)


@mcp.tool()
def invoice_status(invoice_id: str) -> dict:
    """Retrieve detailed invoice status including total amount, paid amount, and payment status.

    Metadata: {"operation_type": "read", "risk_level": "none", "requires_confirmation": false, "required_role": "Operator", "idempotent": true, "domain": "finance"}
    """
    return service.get_invoice_status(invoice_id)


if __name__ == "__main__":
    mcp.run(transport="stdio")
