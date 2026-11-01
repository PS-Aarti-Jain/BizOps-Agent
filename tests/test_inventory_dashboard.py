import pytest
from httpx import ASGITransport, AsyncClient

from database.repositories.repository import BizOpsRepository
from database.seed import seed_database
from services.inventory_service import (
    InventoryDomainService,
    calculate_available_percentage,
    classify_inventory_level,
)
from ui.app import app


@pytest.fixture
def repo(tmp_path):
    db_file = tmp_path / "test_inventory.db"
    seed_database(str(db_file), force=True)
    return BizOpsRepository(db_path=str(db_file))


@pytest.fixture
def inv_service(repo):
    return InventoryDomainService(repo=repo)


def test_zero_stock_classified_as_out_of_stock(inv_service):
    """
    1. Zero stock (available_stock == 0):
    Must be classified as OUT_OF_STOCK, not Low.
    """
    res = inv_service.get_stock_info("P1001")
    assert res["status"] == "SUCCESS"
    item = res["data"]
    assert item["product_id"] == "P1001"
    assert item["on_hand_qty"] == 0
    assert item["available_qty"] == 0
    assert item["classification"] == "OUT_OF_STOCK"
    assert item["is_out_of_stock"] is True
    assert item["is_low_stock"] is False


def test_stock_below_reorder_level_classified_as_low_stock(inv_service):
    """
    2. Stock below reorder level (0 < available_stock < reorder_level):
    Must be classified as LOW_STOCK.
    """
    res = inv_service.get_stock_info("P1002")
    assert res["status"] == "SUCCESS"
    item = res["data"]
    assert item["available_qty"] == 5
    assert item["reorder_threshold"] == 15
    assert item["classification"] == "LOW_STOCK"
    assert item["is_low_stock"] is True
    assert item["is_out_of_stock"] is False


def test_stock_equal_to_reorder_level_classified_as_in_stock():
    """
    3. Stock equal to reorder level (available_stock == reorder_level):
    Must be classified as IN_STOCK per rule: available_stock >= reorder_level.
    """
    status = classify_inventory_level(available_qty=20, reorder_level=20)
    assert status == "IN_STOCK"

    status_low = classify_inventory_level(available_qty=19, reorder_level=20)
    assert status_low == "LOW_STOCK"


def test_stock_above_reorder_level_classified_as_in_stock(inv_service):
    """
    4. Stock above reorder level (available_stock >= reorder_level):
    Must be classified as IN_STOCK.
    """
    res = inv_service.get_stock_info("P1005")
    assert res["status"] == "SUCCESS"
    item = res["data"]
    assert item["available_qty"] == 30
    assert item["reorder_threshold"] == 20
    assert item["classification"] == "IN_STOCK"
    assert item["is_low_stock"] is False
    assert item["is_out_of_stock"] is False


def test_reserved_stock_calculation(inv_service):
    """
    5. Reserved stock calculation:
    reserved_stock = on_hand_stock - available_stock.
    """
    res = inv_service.get_stock_info("P1005")
    assert res["status"] == "SUCCESS"
    item = res["data"]
    # P1005: On hand: 35, Reserved: 5, Allocated: 0, Available: 30
    assert item["on_hand_qty"] == 35
    assert item["available_qty"] == 30
    assert item["total_reserved_qty"] == 5
    assert item["on_hand_qty"] - item["available_qty"] == item["total_reserved_qty"]


def test_zero_on_hand_quantity_available_percentage():
    """
    6. Zero on-hand quantity:
    Available % must be safely computed as 0.0% without division-by-zero errors.
    """
    pct = calculate_available_percentage(available_qty=0, on_hand_qty=0)
    assert pct == 0.0


def test_missing_reorder_level_explicit_handling():
    """
    7. Missing reorder level (reorder_level is None):
    Handled explicitly without inventing thresholds.
    """
    # Available stock > 0 with missing threshold -> IN_STOCK
    status_with_stock = classify_inventory_level(available_qty=15, reorder_level=None)
    assert status_with_stock == "IN_STOCK"

    # Zero available stock with missing threshold -> OUT_OF_STOCK
    status_zero_stock = classify_inventory_level(available_qty=0, reorder_level=None)
    assert status_zero_stock == "OUT_OF_STOCK"


def test_missing_target_stock_level_handling(inv_service):
    """
    8. Missing target stock level:
    Does not invent target values; computes Availability Percentage against on-hand quantity.
    """
    all_res = inv_service.get_all_inventory_items()
    assert all_res["status"] == "SUCCESS"
    for item in all_res["items"]:
        # Verify available_pct is a valid float between 0.0 and 100.0
        assert 0.0 <= item["available_pct"] <= 100.0
        if item["on_hand_qty"] > 0:
            expected_pct = round((item["available_qty"] / item["on_hand_qty"]) * 100.0, 1)
            assert item["available_pct"] == expected_pct
        else:
            assert item["available_pct"] == 0.0


def test_invalid_or_negative_quantities():
    """
    9. Invalid or negative quantities:
    Negative available quantities are treated as OUT_OF_STOCK and clamped to 0.0%.
    """
    assert classify_inventory_level(available_qty=-5, reorder_level=10) == "OUT_OF_STOCK"
    assert calculate_available_percentage(available_qty=-5, on_hand_qty=10) == 0.0
    assert calculate_available_percentage(available_qty=10, on_hand_qty=-5) == 0.0


@pytest.mark.asyncio
async def test_consistency_between_api_mcp_and_dashboard(inv_service):
    """
    10. Consistency between API (/api/data/inventory), MCP tool, and domain service:
    Validates identical record counts, classifications, and percentages.
    """
    # Domain service result
    domain_res = inv_service.get_all_inventory_items()
    domain_items = domain_res["items"]

    # FastAPI endpoint result
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/data/inventory")
        assert res.status_code == 200
        api_data = res.json()["data"]

    assert len(domain_items) == len(api_data) == 15

    # Check Wireless Optical Mouse (P1001) in both
    p1001_domain = next(it for it in domain_items if it["product_id"] == "P1001")
    p1001_api = next(it for it in api_data if it["product_id"] == "P1001")

    assert p1001_domain["classification"] == "OUT_OF_STOCK"
    assert p1001_api["classification"] == "OUT_OF_STOCK"
    assert p1001_domain["available_qty"] == 0
    assert p1001_api["available_qty"] == 0
    assert p1001_domain["is_out_of_stock"] is True
    assert p1001_api["is_out_of_stock"] is True
    assert p1001_domain["is_low_stock"] is False
    assert p1001_api["is_low_stock"] is False

    # Check classification counts match exactly
    api_classifications = [it["classification"] for it in api_data]
    assert api_classifications.count("OUT_OF_STOCK") == 1
    assert api_classifications.count("LOW_STOCK") == 8
    assert api_classifications.count("IN_STOCK") == 6

