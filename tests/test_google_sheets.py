import pytest
from integrations.google_sheets.client import GoogleSheetsClient, _MOCK_SPREADSHEETS


@pytest.fixture
def fresh_client():
    return GoogleSheetsClient(force_mock=True)


def test_read_data_success(fresh_client):
    """Verifies reading data headers and structured records from the Inventory sheet."""
    result = fresh_client.read_data(worksheet_name="Inventory")
    assert result["status"] == "SUCCESS"
    assert "headers" in result
    assert "records" in result
    assert "Product ID" in result["headers"]
    assert len(result["records"]) >= 7
    first_record = result["records"][0]
    assert first_record["Product ID"] == "P1001"


def test_search_records_all_columns(fresh_client):
    """Verifies searching records without column constraint."""
    result = fresh_client.search_records(query="Cooking Oil", worksheet_name="Inventory")
    assert result["status"] == "SUCCESS"
    assert result["matches_found"] >= 1
    matched = result["records"][0]
    assert matched["Product ID"] == "P1002"
    assert "Sunflower" in matched["Product Name"]


def test_search_records_specific_column(fresh_client):
    """Verifies searching records restricted to a specific column."""
    result = fresh_client.search_records(
        query="P1006",
        worksheet_name="Inventory",
        column_name="Product ID",
    )
    assert result["status"] == "SUCCESS"
    assert result["matches_found"] == 1
    assert result["records"][0]["Product Name"] == "Wireless Optical Mouse (USB)"


def test_update_record(fresh_client):
    """Verifies updating an existing record and reflecting changes."""
    # Row 2 in sheet (1-based index: Row 1 is header, Row 2 is P1001)
    update_res = fresh_client.update_record(
        row_index=2,
        updates={"Available Qty": 135, "Stock Status": "In Stock"},
        worksheet_name="Inventory",
    )
    assert update_res["status"] == "SUCCESS"
    assert update_res["updated_fields"]["Available Qty"] == 135

    # Verify update persisted in sheet
    read_res = fresh_client.read_data(worksheet_name="Inventory")
    p1001 = next(r for r in read_res["records"] if r["Product ID"] == "P1001")
    assert p1001["Available Qty"] == 135


def test_append_record(fresh_client):
    """Verifies appending a new row to the worksheet."""
    initial_read = fresh_client.read_data(worksheet_name="Inventory")
    initial_count = initial_read["row_count"]

    new_product = {
        "Product ID": "P1099",
        "Product Name": "Test Thermal Paper Roll 80mm",
        "SKU": "OFF-ROL-80M",
        "Category": "Office Supplies",
        "Available Qty": 100,
        "Reorder Level": 30,
        "Stock Status": "In Stock",
        "Location": "Pune WH-B",
    }
    append_res = fresh_client.append_record(record=new_product, worksheet_name="Inventory")
    assert append_res["status"] == "SUCCESS"

    post_read = fresh_client.read_data(worksheet_name="Inventory")
    assert post_read["row_count"] == initial_count + 1
    assert any(r["Product ID"] == "P1099" for r in post_read["records"])


def test_missing_worksheet_and_invalid_row(fresh_client):
    """Verifies error handling for non-existent worksheets and invalid row indices."""
    missing_sheet = fresh_client.read_data(worksheet_name="NonExistentSheet")
    assert missing_sheet["status"] == "NOT_FOUND"

    invalid_row = fresh_client.update_record(
        row_index=999,
        updates={"Available Qty": 10},
        worksheet_name="Inventory",
    )
    assert invalid_row["status"] == "ROW_NOT_FOUND"


@pytest.mark.live_sheets
def test_live_google_sheets_connection():
    """Live Google Sheets API verification. Skipped when live credentials are not present."""
    client = GoogleSheetsClient(force_mock=False)
    if not client.is_authenticated or not client.default_spreadsheet_id or client.default_spreadsheet_id == "default":
        pytest.skip("Live Google Sheets credentials or spreadsheet ID not configured.")
    res = client.read_data(worksheet_name=client.default_worksheet)
    assert res["status"] in ("SUCCESS", "EMPTY_RESULT")


@pytest.mark.live_sheets
def test_live_google_sheets_search():
    """Live Google Sheets API search verification. Skipped when live credentials are not present."""
    client = GoogleSheetsClient(force_mock=False)
    if not client.is_authenticated or not client.default_spreadsheet_id or client.default_spreadsheet_id == "default":
        pytest.skip("Live Google Sheets credentials or spreadsheet ID not configured.")
    res = client.search_records(query="P1001", worksheet_name=client.default_worksheet)
    assert res["status"] in ("SUCCESS", "NO_DATA", "EMPTY_RESULT")
