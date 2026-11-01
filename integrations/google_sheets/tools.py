from typing import Any, Callable, Dict, List
from database.models import DiscoveredTool, ToolMetadata
from integrations.google_sheets.client import GoogleSheetsClient

_sheets_client = GoogleSheetsClient()


def get_sheets_client() -> GoogleSheetsClient:
    return _sheets_client


def read_data(
    spreadsheet_id: str = None,
    worksheet_name: str = "Inventory",
    range_name: str = "A1:Z100",
) -> Dict[str, Any]:
    """Read structured records from a Google spreadsheet worksheet."""
    return get_sheets_client().read_data(
        spreadsheet_id=spreadsheet_id,
        worksheet_name=worksheet_name,
        range_name=range_name,
    )


def search_records(
    query: str,
    spreadsheet_id: str = None,
    worksheet_name: str = "Inventory",
    column_name: str = None,
) -> Dict[str, Any]:
    """Search Google spreadsheet records using relevant columns and query terms."""
    return get_sheets_client().search_records(
        query=query,
        spreadsheet_id=spreadsheet_id,
        worksheet_name=worksheet_name,
        column_name=column_name,
    )


def update_record(
    row_index: int,
    updates: Dict[str, Any],
    spreadsheet_id: str = None,
    worksheet_name: str = "Inventory",
    idempotency_key: str = None,
) -> Dict[str, Any]:
    """Update an existing record in Google Sheets at the specified row index."""
    return get_sheets_client().update_record(
        row_index=row_index,
        updates=updates,
        spreadsheet_id=spreadsheet_id,
        worksheet_name=worksheet_name,
        idempotency_key=idempotency_key,
    )


def append_record(
    record: Dict[str, Any],
    spreadsheet_id: str = None,
    worksheet_name: str = "Inventory",
    idempotency_key: str = None,
) -> Dict[str, Any]:
    """Add a new row to an approved worksheet in Google Sheets."""
    return get_sheets_client().append_record(
        record=record,
        spreadsheet_id=spreadsheet_id,
        worksheet_name=worksheet_name,
        idempotency_key=idempotency_key,
    )


# Metadata and Tool Definitions
GOOGLE_SHEETS_TOOLS: List[DiscoveredTool] = [
    DiscoveredTool(
        name="read_data",
        description="Read structured business records from a Google spreadsheet worksheet (e.g. 'Inventory', 'Customers').",
        input_schema={
            "type": "object",
            "properties": {
                "worksheet_name": {"type": "string", "description": "Tab name in spreadsheet, e.g. 'Inventory'"},
                "range_name": {"type": "string", "description": "Range to read, default A1:Z100"},
                "spreadsheet_id": {"type": "string", "description": "Optional spreadsheet ID"},
            },
        },
        owning_server="google_sheets",
        metadata=ToolMetadata(
            operation_type="read",
            risk_level="none",
            requires_confirmation=False,
            required_role="Operator",
            idempotent=True,
            domain="google_sheets",
        ),
    ),
    DiscoveredTool(
        name="search_records",
        description="Search Google spreadsheet records using relevant columns and search terms (e.g. product name, customer name, order ID).",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Text or ID to search for"},
                "worksheet_name": {"type": "string", "description": "Tab name in spreadsheet, default 'Inventory'"},
                "column_name": {"type": "string", "description": "Optional column to restrict search to"},
                "spreadsheet_id": {"type": "string", "description": "Optional spreadsheet ID"},
            },
            "required": ["query"],
        },
        owning_server="google_sheets",
        metadata=ToolMetadata(
            operation_type="read",
            risk_level="none",
            requires_confirmation=False,
            required_role="Operator",
            idempotent=True,
            domain="google_sheets",
        ),
    ),
    DiscoveredTool(
        name="update_record",
        description="Update an existing record in Google Sheets. Requires confirmation showing proposed updates before execution.",
        input_schema={
            "type": "object",
            "properties": {
                "row_index": {"type": "integer", "description": "Row number to update (2 or greater)"},
                "updates": {"type": "object", "description": "Key-value mapping of column names to updated values"},
                "worksheet_name": {"type": "string", "description": "Tab name, default 'Inventory'"},
                "spreadsheet_id": {"type": "string", "description": "Optional spreadsheet ID"},
            },
            "required": ["row_index", "updates"],
        },
        owning_server="google_sheets",
        metadata=ToolMetadata(
            operation_type="write",
            risk_level="medium",
            requires_confirmation=True,
            required_role="Manager",
            idempotent=True,
            domain="google_sheets",
        ),
    ),
    DiscoveredTool(
        name="append_record",
        description="Add a new row to an approved worksheet in Google Sheets. Requires confirmation before execution.",
        input_schema={
            "type": "object",
            "properties": {
                "record": {"type": "object", "description": "Dictionary of fields and values to append as new row"},
                "worksheet_name": {"type": "string", "description": "Tab name, default 'Inventory'"},
                "spreadsheet_id": {"type": "string", "description": "Optional spreadsheet ID"},
            },
            "required": ["record"],
        },
        owning_server="google_sheets",
        metadata=ToolMetadata(
            operation_type="write",
            risk_level="medium",
            requires_confirmation=True,
            required_role="Manager",
            idempotent=True,
            domain="google_sheets",
        ),
    ),
]

TOOL_EXECUTORS: Dict[str, Callable] = {
    "google_sheets.read_data": read_data,
    "google_sheets.search_records": search_records,
    "google_sheets.update_record": update_record,
    "google_sheets.append_record": append_record,
}

