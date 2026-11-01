import datetime
from decimal import Decimal
import pytest

from database.models import DiscoveredTool, ToolMetadata
from database.repositories.repository import BizOpsRepository
from security.safety_gateway import SafetyGateway


@pytest.fixture
def repo(tmp_path):
    db_file = tmp_path / "test_bizops.db"
    from database.seed import seed_database
    seed_database(str(db_file), force=True)
    return BizOpsRepository(str(db_file))


@pytest.fixture
def safety_gateway(repo):
    return SafetyGateway(repo=repo)


def test_tamper_detection_purchase_order(safety_gateway):
    """Verifies that tampering with PO price or quantity is blocked."""
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
        "quantity": 50,
        "unit_price": 240.0,
    }
    pa = safety_gateway.create_pending_action(
        tool=tool,
        args=args,
        user_id="USR-MGR-1",
        user_role="Manager",
    )

    # Tamper with quantity
    pa.quantity = 500

    res = safety_gateway.execute_confirmed_action(
        action_id=pa.action_id,
        user_id="USR-MGR-1",
        user_role="Manager",
        execute_fn=lambda p: {"status": "SUCCESS"},
    )
    assert res["status"] == "SECURITY_ERROR"
    assert "tampered" in res["message"].lower()


def test_tamper_detection_receive_goods(safety_gateway):
    """Verifies that tampering with receive_goods PO ID is blocked."""
    tool = DiscoveredTool(
        name="receive_goods",
        description="Receives goods for a PO",
        input_schema={},
        owning_server="inventory",
        metadata=ToolMetadata(
            domain="inventory",
            operation_type="write",
            risk_level="high",
            requires_confirmation=True,
            required_role="Manager",
        ),
    )
    args = {"po_id": "PO-1001", "received_by": "Warehouse Manager"}
    pa = safety_gateway.create_pending_action(
        tool=tool,
        args=args,
        user_id="USR-MGR-1",
        user_role="Manager",
    )

    # Tamper with PO ID
    pa.po_id = "PO-9999"

    res = safety_gateway.execute_confirmed_action(
        action_id=pa.action_id,
        user_id="USR-MGR-1",
        user_role="Manager",
        execute_fn=lambda p: {"status": "SUCCESS"},
    )
    assert res["status"] == "SECURITY_ERROR"
    assert "tampered" in res["message"].lower()


def test_tamper_detection_sheets_update(safety_gateway):
    """Verifies that tampering with Google Sheets update target row or values is blocked."""
    tool = DiscoveredTool(
        name="update_record",
        description="Updates sheets record",
        input_schema={},
        owning_server="google_sheets",
        metadata=ToolMetadata(
            domain="google_sheets",
            operation_type="write",
            risk_level="high",
            requires_confirmation=True,
            required_role="Manager",
        ),
    )
    args = {
        "spreadsheet_id": "test-sheet",
        "worksheet_name": "Inventory",
        "row_index": 5,
        "updates": {"Stock Level": 100},
    }
    pa = safety_gateway.create_pending_action(
        tool=tool,
        args=args,
        user_id="USR-MGR-1",
        user_role="Manager",
    )

    # Tamper with target row
    pa.target_row = 15

    res = safety_gateway.execute_confirmed_action(
        action_id=pa.action_id,
        user_id="USR-MGR-1",
        user_role="Manager",
        execute_fn=lambda p: {"status": "SUCCESS"},
    )
    assert res["status"] == "SECURITY_ERROR"
    assert "tampered" in res["message"].lower()


def test_tamper_detection_sheets_append(safety_gateway):
    """Verifies that tampering with Google Sheets append record values is blocked."""
    tool = DiscoveredTool(
        name="append_record",
        description="Appends sheets record",
        input_schema={},
        owning_server="google_sheets",
        metadata=ToolMetadata(
            domain="google_sheets",
            operation_type="write",
            risk_level="high",
            requires_confirmation=True,
            required_role="Manager",
        ),
    )
    args = {
        "spreadsheet_id": "test-sheet",
        "worksheet_name": "Inventory",
        "record": {"Product ID": "P1001", "Qty": 10},
    }
    pa = safety_gateway.create_pending_action(
        tool=tool,
        args=args,
        user_id="USR-MGR-1",
        user_role="Manager",
    )

    # Tamper with params
    pa.params["record"] = {"Product ID": "P9999", "Qty": 9999}

    res = safety_gateway.execute_confirmed_action(
        action_id=pa.action_id,
        user_id="USR-MGR-1",
        user_role="Manager",
        execute_fn=lambda p: {"status": "SUCCESS"},
    )
    assert res["status"] == "SECURITY_ERROR"
    assert "tampered" in res["message"].lower()


def test_expired_action_rejection(safety_gateway):
    """Verifies that an expired pending action is rejected and audited."""
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
    args = {"supplier_id": "SUP-102", "product_id": "P1001", "quantity": 10, "unit_price": 200.0}
    pa = safety_gateway.create_pending_action(
        tool=tool,
        args=args,
        user_id="USR-MGR-1",
        user_role="Manager",
    )

    # Force expiration
    pa.expires_at = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=10)).isoformat()

    res = safety_gateway.execute_confirmed_action(
        action_id=pa.action_id,
        user_id="USR-MGR-1",
        user_role="Manager",
        execute_fn=lambda p: {"status": "SUCCESS"},
    )
    assert res["status"] == "EXPIRED"
    assert "expired" in res["message"].lower()


def test_unauthorized_user_rejection(safety_gateway):
    """Verifies that an Operator cannot approve a high-risk write action."""
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
    args = {"supplier_id": "SUP-102", "product_id": "P1001", "quantity": 10, "unit_price": 200.0}
    pa = safety_gateway.create_pending_action(
        tool=tool,
        args=args,
        user_id="USR-OP-1",
        user_role="Operator",
    )

    # Operator tries to approve
    res = safety_gateway.execute_confirmed_action(
        action_id=pa.action_id,
        user_id="USR-OP-1",
        user_role="Operator",
        execute_fn=lambda p: {"status": "SUCCESS"},
    )
    assert res["status"] == "PERMISSION_DENIED"
    assert "cannot approve" in res["message"].lower() or "requires" in res["message"].lower()


def test_repeated_approval_blocked(safety_gateway):
    """Verifies that executing approval twice for the same action_id is blocked."""
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
    args = {"supplier_id": "SUP-102", "product_id": "P1001", "quantity": 10, "unit_price": 200.0}
    pa = safety_gateway.create_pending_action(
        tool=tool,
        args=args,
        user_id="USR-MGR-1",
        user_role="Manager",
    )

    # First approval: Success
    res1 = safety_gateway.execute_confirmed_action(
        action_id=pa.action_id,
        user_id="USR-MGR-1",
        user_role="Manager",
        execute_fn=lambda p: {"status": "SUCCESS", "po_id": "PO-TEST-1"},
    )
    assert res1["status"] == "SUCCESS"

    # Second approval attempt: Must be rejected as ALREADY_EXECUTED
    res2 = safety_gateway.execute_confirmed_action(
        action_id=pa.action_id,
        user_id="USR-MGR-1",
        user_role="Manager",
        execute_fn=lambda p: {"status": "SUCCESS", "po_id": "PO-TEST-1"},
    )
    assert res2["status"] == "ALREADY_EXECUTED"
    assert "already been executed" in res2["message"].lower()


def test_duplicate_sheets_write_idempotency(safety_gateway):
    """Verifies that duplicate execution with same idempotency key is blocked."""
    tool = DiscoveredTool(
        name="append_record",
        description="Appends sheets record",
        input_schema={},
        owning_server="google_sheets",
        metadata=ToolMetadata(
            domain="google_sheets",
            operation_type="write",
            risk_level="high",
            requires_confirmation=True,
            required_role="Manager",
        ),
    )
    args = {
        "spreadsheet_id": "test-sheet",
        "worksheet_name": "Inventory",
        "record": {"Product ID": "P1001", "Qty": 10},
    }
    pa1 = safety_gateway.create_pending_action(
        tool=tool,
        args=args,
        user_id="USR-MGR-1",
        user_role="Manager",
    )
    res1 = safety_gateway.execute_confirmed_action(
        action_id=pa1.action_id,
        user_id="USR-MGR-1",
        user_role="Manager",
        execute_fn=lambda p: {"status": "SUCCESS"},
    )
    assert res1["status"] == "SUCCESS"

    # Now create another action with identical parameters/idempotency key
    pa2 = safety_gateway.create_pending_action(
        tool=tool,
        args=args,
        user_id="USR-MGR-1",
        user_role="Manager",
    )
    assert pa2.idempotency_key == pa1.idempotency_key

    res2 = safety_gateway.execute_confirmed_action(
        action_id=pa2.action_id,
        user_id="USR-MGR-1",
        user_role="Manager",
        execute_fn=lambda p: {"status": "SUCCESS"},
    )
    assert res2["status"] == "ALREADY_EXECUTED"


def test_cancellation_audited(safety_gateway, repo):
    """Verifies that action cancellation is properly audited."""
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
    args = {"supplier_id": "SUP-102", "product_id": "P1001", "quantity": 10, "unit_price": 200.0}
    pa = safety_gateway.create_pending_action(
        tool=tool,
        args=args,
        user_id="USR-MGR-1",
        user_role="Manager",
    )

    cancel_res = safety_gateway.cancel_pending_action(
        action_id=pa.action_id,
        user_id="USR-MGR-1",
        user_role="Manager",
        session_id="SESS-TEST",
    )
    assert cancel_res["status"] == "CANCELLED"
    assert pa.action_id not in safety_gateway.pending_actions

    # Check audit log in repository
    history = repo.get_audit_logs(limit=5)
    assert len(history) > 0
    latest = history[0]
    assert latest["action_id"] == pa.action_id
    assert latest["confirmation_status"] == "CANCELLED"
    assert latest["execution_status"] == "CANCELLED_BY_USER"


def test_duplicate_goods_receipt_prevention(repo):
    """Verifies that receive_goods prevents fulfilling the same purchase order twice."""
    po_res = repo.create_purchase_order(
        supplier_id="SUP-101",
        items=[{"product_id": "P1001", "quantity": 15, "unit_price": 240.0}],
        created_by="Test Manager",
        idempotency_key="IDEM-TEST-RECV-1",
    )
    po_id = po_res["po_id"]

    # Initial receipt: Success
    recv1 = repo.receive_goods(po_id=po_id, received_by="Warehouse Manager")
    assert recv1["status"] == "SUCCESS"

    # Second receipt attempt: Blocked with ALREADY_FULFILLED
    recv2 = repo.receive_goods(po_id=po_id, received_by="Warehouse Manager")
    assert recv2["status"] == "ALREADY_FULFILLED"
