from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ReadDataInput(BaseModel):
    spreadsheet_id: Optional[str] = Field(None, description="Target spreadsheet ID (defaults to GOOGLE_SHEETS_SPREADSHEET_ID)")
    worksheet_name: Optional[str] = Field("Inventory", description="Worksheet tab name, e.g. 'Inventory', 'Customers'")
    range_name: Optional[str] = Field("A1:Z100", description="Cell range to read, defaults to A1:Z100")


class SearchRecordsInput(BaseModel):
    spreadsheet_id: Optional[str] = Field(None, description="Target spreadsheet ID")
    worksheet_name: Optional[str] = Field("Inventory", description="Worksheet tab name")
    query: str = Field(..., description="Value to search for (e.g. product name, customer name, ID)")
    column_name: Optional[str] = Field(None, description="Optional column to restrict search to (e.g. 'Product ID', 'Name')")


class UpdateRecordInput(BaseModel):
    spreadsheet_id: Optional[str] = Field(None, description="Target spreadsheet ID")
    worksheet_name: Optional[str] = Field("Inventory", description="Worksheet tab name")
    row_index: int = Field(..., description="Row number in spreadsheet to update (1-based, e.g. 2 for first data row)")
    updates: Dict[str, Any] = Field(..., description="Mapping of column names or letters to new values, e.g. {'Stock Status': 'Low Stock', 'Available': 15}")


class AppendRecordInput(BaseModel):
    spreadsheet_id: Optional[str] = Field(None, description="Target spreadsheet ID")
    worksheet_name: Optional[str] = Field("Inventory", description="Worksheet tab name")
    record: Dict[str, Any] = Field(..., description="Record dictionary or column mapping to append as a new row")

