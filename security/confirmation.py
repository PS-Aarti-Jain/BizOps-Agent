from decimal import Decimal
from typing import Any, Dict, Optional
from database.models import PendingAction


class ConfirmationCardFormatter:
    """Formats human-readable confirmation cards for high-risk write operations."""

    @staticmethod
    def format_po_card(pa: PendingAction) -> str:
        cost_str = f"₹{pa.total_cost:,.2f}" if pa.total_cost else "₹0.00"
        unit_str = f"₹{pa.unit_price:,.2f}" if pa.unit_price else "₹0.00"
        lines = [
            "----------------------------------------------------------------",
            f" [CONFIRMATION REQUIRED] Purchase Order Proposal ({pa.action_id})",
            "----------------------------------------------------------------",
            f" Supplier  : {pa.supplier_name or pa.supplier_id} ({pa.supplier_id})",
            f" Product   : {pa.product_name or pa.product_id} ({pa.product_id})",
            f" Quantity  : {pa.quantity} units",
            f" Unit Price: {unit_str}",
            f" Total Cost: {cost_str}",
            f" Lead Time : {pa.lead_time_days or 'N/A'} days",
        ]
        if pa.trade_off_note:
            lines.append(f" Note      : {pa.trade_off_note}")
        if pa.reason:
            lines.append(f" Reason    : {pa.reason}")
        lines.append("----------------------------------------------------------------")
        lines.append(" Reply 'YES' / approve to execute, or 'NO' to cancel.")
        lines.append("----------------------------------------------------------------")
        return "\n".join(lines)

    @staticmethod
    def format_sheets_card(pa: PendingAction) -> str:
        lines = [
            "----------------------------------------------------------------",
            f" [CONFIRMATION REQUIRED] Google Sheets Modification ({pa.action_id})",
            "----------------------------------------------------------------",
            f" Operation   : {pa.action_type.replace('_', ' ').title()}",
            f" Spreadsheet : {pa.spreadsheet_id or 'Configured Google Spreadsheet'}",
            f" Worksheet   : {pa.worksheet_name or 'Inventory'}",
        ]
        if pa.target_row:
            lines.append(f" Target Row  : Row {pa.target_row}")
        if pa.fields_changed:
            lines.append(" Fields to Change:")
            for k, v in pa.fields_changed.items():
                lines.append(f"   • {k}: {v}")
        if pa.proposed_values:
            lines.append(" Proposed New Values:")
            for k, v in pa.proposed_values.items():
                lines.append(f"   • {k}: {v}")
        lines.append("----------------------------------------------------------------")
        lines.append(" Reply 'YES' / approve to write to Google Sheets, or 'NO' to cancel.")
        lines.append("----------------------------------------------------------------")
        return "\n".join(lines)

    @staticmethod
    def format_receive_goods_card(pa: PendingAction) -> str:
        lines = [
            "----------------------------------------------------------------",
            f" [CONFIRMATION REQUIRED] Receive Goods & Restock ({pa.action_id})",
            "----------------------------------------------------------------",
            f" PO Reference: {pa.po_id}",
            f" Received By : {pa.received_by or pa.user_id}",
            " Action      : Update warehouse stock and mark PO fulfilled",
            "----------------------------------------------------------------",
            " Reply 'YES' / approve to confirm receipt, or 'NO' to cancel.",
            "----------------------------------------------------------------",
        ]
        return "\n".join(lines)

