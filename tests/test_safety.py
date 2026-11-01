from decimal import Decimal
import os
import tempfile
import pytest

from database.models import DiscoveredTool, ToolMetadata
from database.repositories.repository import BizOpsRepository
from database.seed import seed_database
from security.redaction import SecuritySanitizer
from security.safety_gateway import SafetyGateway


@pytest.fixture
def seeded_safety_gateway():
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_safety.db")
    seed_database(db_path, force=True)
    repo = BizOpsRepository(db_path=db_path)
    return SafetyGateway(repo=repo)


def test_rbac_authorization(seeded_safety_gateway):
    """Verifies that Safety Gateway enforces Role-Based Access Control."""
    tool = DiscoveredTool(
        name="create_purchase_order",
        description="Creates a PO",
        input_schema={},
        owning_server="orders",
        metadata=ToolMetadata(
            domain="orders",
            operation_type="write",
            risk_level="high",
            required_role="Manager",
            requires_confirmation=True,
        ),
    )

    # Operator rank is lower than Manager
    allowed, reason = seeded_safety_gateway.evaluate_authorization(tool, user_role="Operator")
    assert allowed is False
    assert "PERMISSION_DENIED" in reason

    # Manager rank satisfies required role
    allowed, reason = seeded_safety_gateway.evaluate_authorization(tool, user_role="Manager")
    assert allowed is True
    assert reason == "AUTHORIZED"

    # Admin rank satisfies required role
    allowed, reason = seeded_safety_gateway.evaluate_authorization(tool, user_role="Admin")
    assert allowed is True


def test_create_pending_action_for_po(seeded_safety_gateway):
    """Verifies PendingAction generation for Purchase Orders with idempotency key and INR total cost."""
    tool = DiscoveredTool(
        name="create_purchase_order",
        description="Creates PO",
        input_schema={},
        owning_server="orders",
        metadata=ToolMetadata(
            domain="orders",
            operation_type="write",
            risk_level="high",
            requires_confirmation=True,
            required_role="Manager",
        ),
    )
    args = {
        "supplier_id": "SUP-102",
        "product_id": "P1001",
        "quantity": 100,
        "unit_price": 245.0,
    }
    pa = seeded_safety_gateway.create_pending_action(
        tool=tool,
        args=args,
        user_id="USR-001",
        user_role="Manager",
        supplier_name="Pune Electronics Ltd",
        product_name="Wireless Optical Mouse (USB)",
    )
    assert pa.action_id.startswith("PA-")
    assert pa.action_type == "create_purchase_order"
    assert pa.total_cost == Decimal("24500.00")
    assert pa.idempotency_key.startswith("IDEM-")
    assert pa.is_expired() is False

    # Check confirmation card
    card = seeded_safety_gateway.format_confirmation_card(pa)
    assert "Pune Electronics Ltd" in card
    assert "₹24,500.00" in card
    assert pa.action_id in card


def test_create_pending_action_for_google_sheets(seeded_safety_gateway):
    """Verifies PendingAction generation for Google Sheets write operations."""
    tool = DiscoveredTool(
        name="update_record",
        description="Update sheet row",
        input_schema={},
        owning_server="google_sheets",
        metadata=ToolMetadata(
            domain="google_sheets",
            operation_type="write",
            risk_level="medium",
            requires_confirmation=True,
            required_role="Manager",
        ),
    )
    args = {
        "worksheet_name": "Inventory",
        "row_index": 7,
        "updates": {"Available Qty": 120, "Stock Status": "In Stock"},
    }
    pa = seeded_safety_gateway.create_pending_action(
        tool=tool,
        args=args,
        user_id="USR-001",
        user_role="Manager",
        reason="Sync updated physical count to Google Sheets",
    )
    assert pa.action_type == "google_sheets_update"
    assert pa.worksheet_name == "Inventory"
    assert pa.target_row == 7
    assert pa.fields_changed["Available Qty"] == 120

    card = seeded_safety_gateway.format_confirmation_card(pa)
    assert "Google Sheets Modification" in card
    assert "Inventory" in card
    assert "Row 7" in card
    assert "Available Qty" in card


def test_tamper_detection(seeded_safety_gateway):
    """Verifies that tampering with pending action parameters causes execution rejection."""
    tool = DiscoveredTool(
        name="create_purchase_order",
        description="Creates PO",
        input_schema={},
        owning_server="orders",
        metadata=ToolMetadata(
            domain="orders",
            operation_type="write",
            risk_level="high",
            requires_confirmation=True,
            required_role="Manager",
        ),
    )
    args = {
        "supplier_id": "SUP-102",
        "product_id": "P1001",
        "quantity": 100,
        "unit_price": 245.0,
    }
    pa = seeded_safety_gateway.create_pending_action(
        tool=tool,
        args=args,
        user_id="USR-001",
        user_role="Manager",
    )

    # Tamper with the unit price behind the scenes
    pa.unit_price = Decimal("399.00")

    def mock_exec(pa):
        return {"status": "SUCCESS"}

    result = seeded_safety_gateway.execute_confirmed_action(
        action_id=pa.action_id,
        user_id="USR-001",
        user_role="Manager",
        execute_fn=mock_exec,
    )
    assert result["status"] == "SECURITY_ERROR"
    assert "tampered" in result["message"].lower()


def test_secret_masking_and_sanitization():
    """Verifies that API keys and secrets are masked in error and log strings."""
    secret = "gsk_abcd1234efgh5678ijkl9012"
    raw_text = f"Connection failed with key {secret} for endpoint https://api.groq.com"
    masked = SecuritySanitizer.mask_secrets(raw_text, secret)
    assert secret not in masked
    assert "REDACTED" in masked


def test_idempotency_prevents_duplicate_submission(seeded_safety_gateway):
    """Verifies that identical parameters produce a stable idempotency key."""
    tool = DiscoveredTool(
        name="create_purchase_order",
        description="Creates PO",
        input_schema={},
        owning_server="orders",
        metadata=ToolMetadata(
            domain="orders",
            operation_type="write",
            risk_level="high",
            requires_confirmation=True,
            required_role="Manager",
        ),
    )
    args = {"supplier_id": "SUP-101", "product_id": "P1001", "quantity": 50, "unit_price": 20.0}
    pa1 = seeded_safety_gateway.create_pending_action(tool=tool, args=args, user_id="U1", user_role="Manager")
    pa2 = seeded_safety_gateway.create_pending_action(tool=tool, args=args, user_id="U1", user_role="Manager")
    assert pa1.idempotency_key == pa2.idempotency_key
