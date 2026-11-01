import logging
import os
from typing import Any, Dict, List, Optional
from app.config import (
    GOOGLE_SHEETS_DEFAULT_WORKSHEET,
    GOOGLE_SHEETS_SPREADSHEET_ID,
)
from integrations.google_sheets.auth import get_google_sheets_credentials

logger = logging.getLogger(__name__)


# In-memory realistic sample spreadsheet store for local fallback/testing
_MOCK_SPREADSHEETS: Dict[str, Dict[str, List[List[Any]]]] = {
    "default": {
        "Inventory": [
            ["Product ID", "Product Name", "SKU", "Category", "Available Qty", "Reorder Level", "Stock Status", "Location"],
            ["P1001", "Parle-style Glucose Biscuits 800g", "FMCG-PAR-GLU", "FMCG", 120, 50, "In Stock", "Mumbai WH-A"],
            ["P1002", "Refined Sunflower Cooking Oil 1L", "FMCG-OIL-SUN", "FMCG", 25, 40, "Low Stock", "Mumbai WH-A"],
            ["P1003", "Premium CTC Tea Powder 500g", "FMCG-TEA-PRE", "FMCG", 15, 30, "Low Stock", "Pune WH-B"],
            ["P1004", "A4 Copier Paper 75 GSM (Ream)", "OFF-PPR-A4", "Office Supplies", 8, 25, "Low Stock", "Pune WH-B"],
            ["P1005", "Blue Ballpoint Pens (Pack of 20)", "OFF-PEN-BLU", "Office Supplies", 60, 20, "In Stock", "Nashik WH-C"],
            ["P1006", "Wireless Optical Mouse (USB)", "ELE-MOU-WRL", "Electronics", 12, 20, "Low Stock", "Mumbai WH-A"],
            ["P1007", "Heavy Duty Corrugated Box (Pack 25)", "PKG-BOX-COR", "Packaging", 150, 50, "In Stock", "Thane WH-D"],
        ],
        "Customers": [
            ["Customer ID", "Business Name", "City", "State", "Credit Limit", "Outstanding Balance", "Status"],
            ["CUST-501", "Om Sai Enterprises", "Mumbai", "Maharashtra", 75000, 32000, "Active"],
            ["CUST-502", "Shree Krishna General Stores", "Pune", "Maharashtra", 120000, 145000, "Overdue"],
            ["CUST-503", "Maharashtra Office Solutions", "Nashik", "Maharashtra", 50000, 12000, "Active"],
            ["CUST-504", "Western India Distributors", "Thane", "Maharashtra", 200000, 45000, "Active"],
        ],
        "Orders": [
            ["Order ID", "Customer Name", "Product Name", "Quantity", "Total Amount", "Order Date", "Status"],
            ["ORD-1042", "Apex Technologies", "Wireless Optical Mouse (USB)", 50, 85000, "2026-10-25", "Pending"],
            ["ORD-1043", "Blue Horizon Ltd", "Mechanical Gaming Keyboard", 10, 4200, "2026-10-26", "Pending"],
            ["ORD-1044", "Zenith Retail", "A4 Copier Paper 75 GSM (Ream)", 100, 14500, "2026-10-27", "Pending"],
            ["ORD-1045", "Prime Distribution", "Refined Sunflower Cooking Oil 1L", 20, 18500, "2026-10-28", "Shipped"],
            ["ORD-1046", "Metro Hypermarket", "Brown Packing Tape 65m Roll", 40, 12450, "2026-10-29", "Pending"],
        ]
    }
}


class GoogleSheetsClient:
    """
    Client for interacting with Google Sheets API v4.
    Supports authenticated live Google Cloud API calls, with seamless
    fallback to an in-memory Indian business demonstration sheet when credentials
    are not configured in the local development environment.
    """

    def __init__(
        self,
        spreadsheet_id: Optional[str] = None,
        default_worksheet: str = GOOGLE_SHEETS_DEFAULT_WORKSHEET,
        force_mock: bool = False,
    ):
        self.default_spreadsheet_id = (
            spreadsheet_id or os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID") or GOOGLE_SHEETS_SPREADSHEET_ID or ""
        ).strip()
        self.default_worksheet = default_worksheet or "Inventory"
        self.force_mock = force_mock

        self.credentials, self.auth_status = get_google_sheets_credentials()
        self.is_authenticated = self.auth_status.get("authenticated", False) and not force_mock
        self._service: Any = None
        self._executed_idempotency_keys: set = set()

        if self.is_authenticated and self.credentials:
            try:
                from googleapiclient.discovery import build
                self._service = build("sheets", "v4", credentials=self.credentials, cache_discovery=False)
                logger.info("Google Sheets API service initialized with live credentials.")
            except Exception as e:
                logger.warning(f"Failed to build Google Sheets service: {e}. Falling back to demo store.")
                self.is_authenticated = False

    def _resolve_spreadsheet_id(self, spreadsheet_id: Optional[str]) -> str:
        target = (spreadsheet_id or self.default_spreadsheet_id or "default").strip()
        return target

    def read_data(
        self,
        spreadsheet_id: Optional[str] = None,
        worksheet_name: Optional[str] = None,
        range_name: str = "A1:Z100",
    ) -> Dict[str, Any]:
        """
        Reads structured rows from the selected worksheet.
        """
        sid = self._resolve_spreadsheet_id(spreadsheet_id)
        sheet_tab = worksheet_name or self.default_worksheet

        # Live Google Sheets API path
        if self.is_authenticated and self._service and sid and sid != "default":
            try:
                full_range = f"'{sheet_tab}'!{range_name}"
                sheet = self._service.spreadsheets()
                result = sheet.values().get(spreadsheetId=sid, range=full_range).execute()
                rows = result.get("values", [])
                if not rows:
                    return {
                        "status": "EMPTY_RESULT",
                        "message": f"Sheet '{sheet_tab}' range '{range_name}' contains no rows.",
                        "spreadsheet_id": sid,
                        "worksheet": sheet_tab,
                        "headers": [],
                        "records": [],
                        "row_count": 0,
                    }
                headers = rows[0]
                records = []
                for idx, row in enumerate(rows[1:], start=2):
                    rec = {"_row_number": idx}
                    for h_idx, h in enumerate(headers):
                        rec[h] = row[h_idx] if h_idx < len(row) else ""
                    records.append(rec)
                return {
                    "status": "SUCCESS",
                    "source": "google_sheets_api",
                    "mode": "live",
                    "spreadsheet_id": sid,
                    "worksheet": sheet_tab,
                    "headers": headers,
                    "records": records,
                    "row_count": len(records),
                }
            except Exception as e:
                err_msg = str(e)
                logger.error(f"Google Sheets read API error: {err_msg}")
                return {
                    "status": "API_ERROR",
                    "source": "google_sheets_api",
                    "mode": "live",
                    "message": f"Google Sheets API read failed: {err_msg}",
                    "spreadsheet_id": sid,
                    "worksheet": sheet_tab,
                }

        # Mock / Local Demo Sheet Path
        default_store = _MOCK_SPREADSHEETS.get("default", {})
        if sheet_tab not in default_store:
            return {
                "status": "NOT_FOUND",
                "source": "demo_sheets_store",
                "mode": "mock",
                "message": f"Worksheet '{sheet_tab}' not found in spreadsheet.",
                "spreadsheet_id": sid,
                "worksheet": sheet_tab,
                "records": [],
                "row_count": 0,
            }

        sheet_data = default_store.get(sheet_tab, [])
        if len(sheet_data) <= 1:
            return {
                "status": "NO_DATA",
                "source": "demo_sheets_store",
                "mode": "mock",
                "message": f"Worksheet '{sheet_tab}' contains no data rows.",
                "spreadsheet_id": sid,
                "worksheet": sheet_tab,
                "records": [],
                "row_count": 0,
            }

        headers = sheet_data[0]
        records = []
        for idx, row in enumerate(sheet_data[1:], start=2):
            rec = {"_row_number": idx}
            for h_idx, h in enumerate(headers):
                rec[h] = row[h_idx] if h_idx < len(row) else ""
            records.append(rec)

        return {
            "status": "SUCCESS",
            "source": "demo_sheets_store" if not self.is_authenticated else "google_sheets_api",
            "mode": "mock" if not self.is_authenticated else "live",
            "spreadsheet_id": sid,
            "worksheet": sheet_tab,
            "headers": headers,
            "records": records,
            "row_count": len(records),
        }

    def search_records(
        self,
        query: str,
        spreadsheet_id: Optional[str] = None,
        worksheet_name: Optional[str] = None,
        column_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Searches spreadsheet records matching a term across all columns or a specific column.
        """
        read_res = self.read_data(spreadsheet_id, worksheet_name)
        if read_res["status"] != "SUCCESS":
            return read_res

        records = read_res.get("records", [])
        q_norm = query.strip().lower()
        matched = []

        for rec in records:
            if column_name:
                val = str(rec.get(column_name, "")).lower()
                if q_norm in val:
                    matched.append(rec)
            else:
                found = any(q_norm in str(v).lower() for k, v in rec.items() if not k.startswith("_"))
                if found:
                    matched.append(rec)

        return {
            "status": "SUCCESS",
            "source": read_res.get("source", "demo_sheets_store"),
            "mode": read_res.get("mode", "mock"),
            "spreadsheet_id": read_res.get("spreadsheet_id"),
            "worksheet": read_res.get("worksheet"),
            "query": query,
            "column_searched": column_name or "ALL",
            "matches_found": len(matched),
            "records": matched,
        }

    def update_record(
        self,
        row_index: int,
        updates: Dict[str, Any],
        spreadsheet_id: Optional[str] = None,
        worksheet_name: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Updates an existing record at row_index (1-based, row 1 = headers).
        """
        sid = self._resolve_spreadsheet_id(spreadsheet_id)
        sheet_tab = worksheet_name or self.default_worksheet

        if idempotency_key and idempotency_key in self._executed_idempotency_keys:
            return {
                "status": "ALREADY_EXISTS",
                "message": f"Operation with idempotency key '{idempotency_key}' already completed.",
                "spreadsheet_id": sid,
                "worksheet": sheet_tab,
            }

        if row_index < 2:
            return {
                "status": "INVALID_ROW",
                "message": f"Row index must be 2 or greater (row 1 is reserved for headers).",
            }

        # Live Google Sheets API path
        if self.is_authenticated and self._service and sid and sid != "default":
            try:
                # Read header to map column names to letters
                h_res = self._service.spreadsheets().values().get(
                    spreadsheetId=sid, range=f"'{sheet_tab}'!1:1"
                ).execute()
                headers = h_res.get("values", [[]])[0]
                if not headers:
                    return {"status": "ERROR", "message": f"No headers found in '{sheet_tab}'."}

                col_map = {h: idx for idx, h in enumerate(headers)}
                applied = {}
                for col_name, new_val in updates.items():
                    if col_name in col_map:
                        col_idx = col_map[col_name]
                        col_letter = chr(ord('A') + col_idx)
                        target_cell = f"'{sheet_tab}'!{col_letter}{row_index}"
                        self._service.spreadsheets().values().update(
                            spreadsheetId=sid,
                            range=target_cell,
                            valueInputOption="USER_ENTERED",
                            body={"values": [[new_val]]},
                        ).execute()
                        applied[col_name] = new_val

                return {
                    "status": "SUCCESS",
                    "source": "google_sheets_api",
                    "mode": "live",
                    "spreadsheet_id": sid,
                    "worksheet": sheet_tab,
                    "row_index": row_index,
                    "updated_fields": applied,
                    "message": f"Successfully updated row {row_index} in sheet '{sheet_tab}'.",
                }
            except Exception as e:
                return {
                    "status": "API_ERROR",
                    "source": "google_sheets_api",
                    "mode": "live",
                    "message": f"Failed to update Google Sheet: {str(e)}",
                    "spreadsheet_id": sid,
                    "worksheet": sheet_tab,
                }

        # Local Demo Store Path
        sheet_data = _MOCK_SPREADSHEETS.get("default", {}).get(sheet_tab)
        if not sheet_data or row_index > len(sheet_data):
            return {
                "status": "ROW_NOT_FOUND",
                "message": f"Row {row_index} does not exist in worksheet '{sheet_tab}'.",
                "spreadsheet_id": sid,
                "worksheet": sheet_tab,
            }

        headers = sheet_data[0]
        col_map = {h: idx for idx, h in enumerate(headers)}
        target_row = sheet_data[row_index - 1]

        applied = {}
        for col_name, new_val in updates.items():
            if col_name in col_map:
                col_idx = col_map[col_name]
                while len(target_row) <= col_idx:
                    target_row.append("")
                target_row[col_idx] = new_val
                applied[col_name] = new_val

        if idempotency_key:
            self._executed_idempotency_keys.add(idempotency_key)

        return {
            "status": "SUCCESS",
            "source": "demo_sheets_store" if not self.is_authenticated else "google_sheets_api",
            "mode": "mock" if not self.is_authenticated else "live",
            "spreadsheet_id": sid,
            "worksheet": sheet_tab,
            "row_index": row_index,
            "updated_fields": applied,
            "message": f"Successfully updated row {row_index} in sheet '{sheet_tab}'.",
        }

    def append_record(
        self,
        record: Dict[str, Any],
        spreadsheet_id: Optional[str] = None,
        worksheet_name: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Appends a new record to the worksheet.
        """
        sid = self._resolve_spreadsheet_id(spreadsheet_id)
        sheet_tab = worksheet_name or self.default_worksheet

        if idempotency_key and idempotency_key in self._executed_idempotency_keys:
            return {
                "status": "ALREADY_EXISTS",
                "message": f"Operation with idempotency key '{idempotency_key}' already completed.",
                "spreadsheet_id": sid,
                "worksheet": sheet_tab,
            }

        # Live Google Sheets API path
        if self.is_authenticated and self._service and sid and sid != "default":
            try:
                h_res = self._service.spreadsheets().values().get(
                    spreadsheetId=sid, range=f"'{sheet_tab}'!1:1"
                ).execute()
                headers = h_res.get("values", [[]])[0]
                row_vals = [record.get(h, "") for h in headers]
                res = self._service.spreadsheets().values().append(
                    spreadsheetId=sid,
                    range=f"'{sheet_tab}'!A1",
                    valueInputOption="USER_ENTERED",
                    insertDataOption="INSERT_ROWS",
                    body={"values": [row_vals]},
                ).execute()
                updated_range = res.get("updates", {}).get("updatedRange", "")
                return {
                    "status": "SUCCESS",
                    "source": "google_sheets_api",
                    "mode": "live",
                    "spreadsheet_id": sid,
                    "worksheet": sheet_tab,
                    "updated_range": updated_range,
                    "appended_record": record,
                    "message": f"Appended new record to sheet '{sheet_tab}'.",
                }
            except Exception as e:
                return {
                    "status": "API_ERROR",
                    "source": "google_sheets_api",
                    "mode": "live",
                    "message": f"Failed to append to Google Sheet: {str(e)}",
                }

        # Local Demo Store Path
        sheet_data = _MOCK_SPREADSHEETS.get("default", {}).get(sheet_tab)
        if not sheet_data:
            return {
                "status": "NO_DATA",
                "source": "demo_sheets_store",
                "mode": "mock",
                "message": f"Worksheet '{sheet_tab}' does not exist.",
            }

        headers = sheet_data[0]
        new_row = [record.get(h, "") for h in headers]
        sheet_data.append(new_row)
        new_row_num = len(sheet_data)

        if idempotency_key:
            self._executed_idempotency_keys.add(idempotency_key)

        return {
            "status": "SUCCESS",
            "source": "demo_sheets_store" if not self.is_authenticated else "google_sheets_api",
            "mode": "mock" if not self.is_authenticated else "live",
            "spreadsheet_id": sid,
            "worksheet": sheet_tab,
            "row_index": new_row_num,
            "appended_record": record,
            "message": f"Appended new row #{new_row_num} to sheet '{sheet_tab}'.",
        }

