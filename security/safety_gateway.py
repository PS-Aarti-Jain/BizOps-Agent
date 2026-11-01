import datetime
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import inspect
import json
import logging
import uuid
from typing import Any, Dict, Optional, Tuple

from app.config import PA_EXPIRATION_SECONDS, PRICE_CHANGE_TOLERANCE_PCT, ROLE_HIERARCHY
from database.models import DiscoveredTool, PendingAction
from database.repositories.repository import BizOpsRepository
from security.confirmation import ConfirmationCardFormatter

logger = logging.getLogger(__name__)


class SafetyGateway:
    def __init__(self, repo: Optional[BizOpsRepository] = None):
        self.repo = repo or BizOpsRepository()
        self.pending_actions: Dict[str, PendingAction] = {}  # pa_id -> PendingAction
        self.executed_actions: Dict[str, Dict[str, Any]] = {}  # pa_id -> execution result metadata
        self.executed_idempotency_keys: Dict[str, Dict[str, Any]] = {}  # key -> result metadata

    def evaluate_authorization(
        self, tool: DiscoveredTool, user_role: str
    ) -> Tuple[bool, str]:
        """
        Role-Based Access Control (RBAC) Authority check.
        Roles: Operator < Manager < Admin
        """
        required_role = tool.metadata.required_role if tool and tool.metadata else "Operator"
        required_rank = ROLE_HIERARCHY.get(required_role, 1)
        user_rank = ROLE_HIERARCHY.get(user_role, 1)

        if user_rank < required_rank:
            return (
                False,
                f"PERMISSION_DENIED: User role '{user_role}' lacks authority. Required role is '{required_role}'.",
            )
        return True, "AUTHORIZED"

    def create_pending_action(
        self,
        tool: DiscoveredTool,
        args: Dict[str, Any],
        user_id: str,
        user_role: str,
        supplier_name: str = "Unknown Supplier",
        product_name: str = "Unknown Product",
        lead_time_days: Optional[int] = None,
        reason: Optional[str] = None,
        trade_off_note: Optional[str] = None,
    ) -> PendingAction:
        pa_id = f"PA-{uuid.uuid4().hex[:6].upper()}"
        tool_name = tool.name if tool else "unknown"
        owning_server = tool.owning_server if tool else "unknown"

        now = datetime.datetime.now(datetime.timezone.utc)
        created_at = now.isoformat()
        expires_at = (now + datetime.timedelta(seconds=PA_EXPIRATION_SECONDS)).isoformat()

        # Case 1: Google Sheets Write Operations
        if "google_sheets" in tool_name or tool_name in ("update_record", "append_record"):
            action_type = "google_sheets_update" if "update" in tool_name else "google_sheets_append"
            target_row = args.get("row_index") or args.get("row") or args.get("target_row")
            worksheet_name = args.get("worksheet_name") or args.get("sheet_name") or "Inventory"
            spreadsheet_id = args.get("spreadsheet_id") or ""
            fields_changed = args.get("updates") or args.get("record") or args.get("data") or {}
            proposed_values = dict(fields_changed) if isinstance(fields_changed, dict) else {"values": fields_changed}

            raw_key = f"GSHEETS:{user_id}:{worksheet_name}:{target_row}:{json.dumps(fields_changed, sort_keys=True)}"
            idempotency_key = f"IDEM-GS-{hashlib.sha256(raw_key.encode()).hexdigest()[:16]}"
            
            if action_type == "google_sheets_update":
                param_dict = {
                    "action": "google_sheets_update",
                    "spreadsheet_id": spreadsheet_id,
                    "worksheet_name": worksheet_name,
                    "row_index": int(target_row) if target_row is not None and str(target_row).isdigit() else None,
                    "updates": fields_changed if isinstance(fields_changed, dict) else {},
                }
            else:
                param_dict = {
                    "action": "google_sheets_append",
                    "spreadsheet_id": spreadsheet_id,
                    "worksheet_name": worksheet_name,
                    "record": fields_changed if isinstance(fields_changed, dict) else {},
                }
            param_hash = hashlib.sha256(json.dumps(param_dict, sort_keys=True).encode()).hexdigest()

            pa = PendingAction(
                action_id=pa_id,
                tool_name=tool_name,
                owning_server=owning_server,
                user_id=user_id,
                user_role=user_role,
                action_type=action_type,
                params=dict(args),
                spreadsheet_id=spreadsheet_id,
                worksheet_name=worksheet_name,
                target_row=int(target_row) if target_row is not None and str(target_row).isdigit() else None,
                fields_changed=fields_changed if isinstance(fields_changed, dict) else {},
                proposed_values=proposed_values,
                idempotency_key=idempotency_key,
                created_at=created_at,
                expires_at=expires_at,
                parameter_hash=param_hash,
                reason=reason or "Google Sheets record modification",
            )
            self.pending_actions[pa_id] = pa
            return pa

        # Case 2: receive_goods
        if "receive_goods" in tool_name:
            po_id = str(args.get("po_id", "")).strip()
            received_by = str(args.get("received_by") or f"User {user_id} ({user_role})")

            existing_key = args.get("idempotency_key")
            if existing_key and existing_key.startswith("IDEM-"):
                idempotency_key = existing_key
            else:
                raw_key = f"RECV:{user_id}:{po_id}"
                idempotency_key = f"IDEM-{hashlib.sha256(raw_key.encode()).hexdigest()[:16]}"

            param_dict = {"action": "receive_goods", "po_id": po_id, "received_by": received_by}
            param_hash = hashlib.sha256(json.dumps(param_dict, sort_keys=True).encode()).hexdigest()

            pa = PendingAction(
                action_id=pa_id,
                tool_name=tool_name,
                owning_server=owning_server,
                user_id=user_id,
                user_role=user_role,
                action_type="receive_goods",
                params=dict(args),
                po_id=po_id,
                received_by=received_by,
                idempotency_key=idempotency_key,
                created_at=created_at,
                expires_at=expires_at,
                parameter_hash=param_hash,
            )
            self.pending_actions[pa_id] = pa
            return pa

        # Case 3: create_purchase_order
        supplier_id = str(args.get("supplier_id", ""))
        product_id = str(args.get("product_id", ""))
        quantity = int(args.get("quantity", 0))
        unit_price = Decimal(str(args.get("unit_price", "0.00")))
        total_cost = (Decimal(quantity) * unit_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        lead_time = lead_time_days
        sp = self.repo.get_supplier_product(supplier_id, product_id)
        if sp and sp.get("lead_time_days") is not None:
            lead_time = int(sp["lead_time_days"])

        # Determine supplier name & product name if unknown
        sup_rec = self.repo.get_supplier(supplier_id)
        if sup_rec and supplier_name == "Unknown Supplier":
            supplier_name = sup_rec["name"]
        prod_rec = self.repo.get_product(product_id)
        if prod_rec and product_name == "Unknown Product":
            product_name = prod_rec["name"]

        existing_key = args.get("idempotency_key")
        if existing_key and existing_key.startswith("IDEM-"):
            idempotency_key = existing_key
        else:
            raw_key = f"{user_id}:{supplier_id}:{product_id}:{quantity}:{unit_price}"
            idempotency_key = f"IDEM-{hashlib.sha256(raw_key.encode()).hexdigest()[:16]}"

        param_dict = {
            "supplier_id": supplier_id,
            "product_id": product_id,
            "quantity": quantity,
            "unit_price": str(unit_price),
        }
        param_hash = hashlib.sha256(json.dumps(param_dict, sort_keys=True).encode()).hexdigest()

        pa = PendingAction(
            action_id=pa_id,
            tool_name=tool_name,
            owning_server=owning_server,
            user_id=user_id,
            user_role=user_role,
            action_type="create_purchase_order",
            params=dict(args),
            supplier_id=supplier_id,
            supplier_name=supplier_name,
            product_id=product_id,
            product_name=product_name,
            quantity=quantity,
            unit_price=unit_price,
            total_cost=total_cost,
            lead_time_days=lead_time,
            reason=reason or "Low stock procurement",
            trade_off_note=trade_off_note,
            idempotency_key=idempotency_key,
            created_at=created_at,
            expires_at=expires_at,
            parameter_hash=param_hash,
        )
        self.pending_actions[pa_id] = pa
        return pa

    def format_confirmation_card(self, pa: PendingAction) -> str:
        """Formats an actionable user confirmation card based on action type."""
        if pa.action_type in ("google_sheets_update", "google_sheets_append"):
            return ConfirmationCardFormatter.format_sheets_card(pa)
        elif pa.action_type == "receive_goods":
            return ConfirmationCardFormatter.format_receive_goods_card(pa)
        else:
            return ConfirmationCardFormatter.format_po_card(pa)

    def cancel_pending_action(
        self,
        action_id: str,
        user_id: str = "UNKNOWN",
        user_role: str = "UNKNOWN",
        session_id: str = "SESSION-UNKNOWN",
    ) -> Dict[str, Any]:
        """Cancels a pending action and records the cancellation in the audit log."""
        pa = self.pending_actions.pop(action_id, None)
        if not pa:
            return {"status": "NOT_FOUND", "message": f"Action '{action_id}' not found."}

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.repo.log_audit_entry(
            session_id=session_id,
            action_id=action_id,
            user_id=user_id or "UNKNOWN",
            user_role=user_role or "UNKNOWN",
            tool_name=pa.tool_name,
            arguments=pa.params,
            risk_level="medium",
            confirmation_status="CANCELLED",
            user_confirmed_at=now_iso,
            execution_status="CANCELLED_BY_USER",
            result={"status": "CANCELLED", "message": f"Action '{action_id}' cancelled by user."},
            idempotency_key=pa.idempotency_key,
        )
        return {"status": "CANCELLED", "message": f"Action '{action_id}' cancelled by user."}

    def _validate_and_authorize_action(
        self,
        action_id: str,
        user_id: str,
        user_role: str,
        session_id: str,
    ) -> Tuple[bool, Dict[str, Any], Optional[PendingAction]]:
        """
        Validates action existence, user identity, expiry, RBAC authority,
        and parameter tamper integrity. Logs violations directly to the audit log.
        Returns: (is_valid, response_dict, pending_action_obj)
        """
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # Check if already executed (repeated approval)
        if action_id in self.executed_actions:
            prev = self.executed_actions[action_id]
            self.repo.log_audit_entry(
                session_id=session_id,
                action_id=action_id,
                user_id=user_id or "UNKNOWN",
                user_role=user_role or "UNKNOWN",
                tool_name=prev.get("tool_name", "unknown"),
                arguments=prev.get("params", {}),
                risk_level="high",
                confirmation_status="REPEATED_APPROVAL_ATTEMPT",
                user_confirmed_at=now_iso,
                execution_status="ALREADY_EXECUTED",
                result={"error": f"Action '{action_id}' has already been executed."},
                idempotency_key=prev.get("idempotency_key", ""),
            )
            return False, {"status": "ALREADY_EXECUTED", "message": f"Action '{action_id}' has already been executed."}, None

        pa = self.pending_actions.get(action_id)
        if not pa:
            return False, {"status": "NOT_FOUND", "message": f"Action '{action_id}' not found."}, None

        # Validate user identity
        if not user_id or not user_role:
            self.repo.log_audit_entry(
                session_id=session_id,
                action_id=action_id,
                user_id="ANONYMOUS",
                user_role="NONE",
                tool_name=pa.tool_name,
                arguments=pa.params,
                risk_level="high",
                confirmation_status="REJECTED",
                user_confirmed_at=now_iso,
                execution_status="PERMISSION_DENIED",
                result={"error": "User identity and role are required to confirm write actions."},
                idempotency_key=pa.idempotency_key,
            )
            return False, {"status": "PERMISSION_DENIED", "message": "User identity and role are required."}, pa

        # Check expiry
        if pa.is_expired():
            self.pending_actions.pop(action_id, None)
            self.repo.log_audit_entry(
                session_id=session_id,
                action_id=action_id,
                user_id=user_id,
                user_role=user_role,
                tool_name=pa.tool_name,
                arguments=pa.params,
                risk_level="medium",
                confirmation_status="EXPIRED",
                user_confirmed_at=now_iso,
                execution_status="EXPIRED",
                result={"error": f"Action '{action_id}' has expired."},
                idempotency_key=pa.idempotency_key,
            )
            return False, {"status": "EXPIRED", "message": f"Action '{action_id}' has expired. Please re-request."}, pa

        # RBAC Check (Requires Manager or above)
        user_rank = ROLE_HIERARCHY.get(user_role, 1)
        req_role = "Manager"
        if user_rank < ROLE_HIERARCHY.get(req_role, 2):
            self.repo.log_audit_entry(
                session_id=session_id,
                action_id=action_id,
                user_id=user_id,
                user_role=user_role,
                tool_name=pa.tool_name,
                arguments=pa.params,
                risk_level="high",
                confirmation_status="REJECTED",
                user_confirmed_at=now_iso,
                execution_status="PERMISSION_DENIED",
                result={"error": f"User role '{user_role}' cannot approve '{action_id}'. Requires '{req_role}'."},
                idempotency_key=pa.idempotency_key,
            )
            return False, {
                "status": "PERMISSION_DENIED",
                "message": f"User role '{user_role}' cannot approve '{action_id}'. Requires '{req_role}'.",
            }, pa

        # Parameter tamper verification
        tampered = False
        if pa.action_type == "create_purchase_order":
            check_dict = {
                "supplier_id": str(pa.supplier_id or ""),
                "product_id": str(pa.product_id or ""),
                "quantity": int(pa.quantity or 0),
                "unit_price": str(pa.unit_price),
            }
            if pa.parameter_hash != hashlib.sha256(json.dumps(check_dict, sort_keys=True).encode()).hexdigest():
                tampered = True
            if (
                str(pa.params.get("supplier_id", "")) != str(pa.supplier_id or "")
                or str(pa.params.get("product_id", "")) != str(pa.product_id or "")
                or int(pa.params.get("quantity", 0)) != int(pa.quantity or 0)
                or str(pa.params.get("unit_price", "")) != str(pa.unit_price or "")
            ):
                tampered = True

        elif pa.action_type == "receive_goods":
            check_dict = {
                "action": "receive_goods",
                "po_id": str(pa.po_id or ""),
                "received_by": str(pa.received_by or f"User {pa.user_id} ({pa.user_role})"),
            }
            if pa.parameter_hash != hashlib.sha256(json.dumps(check_dict, sort_keys=True).encode()).hexdigest():
                tampered = True
            if str(pa.params.get("po_id", "")).strip() != str(pa.po_id or ""):
                tampered = True

        elif pa.action_type == "google_sheets_update":
            target_row = pa.params.get("row_index") or pa.params.get("row") or pa.params.get("target_row") or pa.target_row
            worksheet_name = pa.params.get("worksheet_name") or pa.params.get("sheet_name") or pa.worksheet_name or "Inventory"
            spreadsheet_id = pa.params.get("spreadsheet_id") or pa.spreadsheet_id or ""
            fields_changed = pa.params.get("updates") or pa.params.get("record") or pa.params.get("data") or pa.fields_changed or {}
            check_dict = {
                "action": "google_sheets_update",
                "spreadsheet_id": spreadsheet_id,
                "worksheet_name": worksheet_name,
                "row_index": int(target_row) if target_row is not None and str(target_row).isdigit() else None,
                "updates": fields_changed if isinstance(fields_changed, dict) else {},
            }
            dict_hash = hashlib.sha256(json.dumps(check_dict, sort_keys=True).encode()).hexdigest()
            raw_hash = hashlib.sha256(json.dumps(pa.params, sort_keys=True).encode()).hexdigest()
            if pa.parameter_hash not in (dict_hash, raw_hash):
                tampered = True
            if pa.target_row is not None and check_dict["row_index"] != pa.target_row:
                tampered = True

        elif pa.action_type == "google_sheets_append":
            worksheet_name = pa.params.get("worksheet_name") or pa.params.get("sheet_name") or pa.worksheet_name or "Inventory"
            spreadsheet_id = pa.params.get("spreadsheet_id") or pa.spreadsheet_id or ""
            fields_changed = pa.params.get("updates") or pa.params.get("record") or pa.params.get("data") or pa.fields_changed or {}
            check_dict = {
                "action": "google_sheets_append",
                "spreadsheet_id": spreadsheet_id,
                "worksheet_name": worksheet_name,
                "record": fields_changed if isinstance(fields_changed, dict) else {},
            }
            dict_hash = hashlib.sha256(json.dumps(check_dict, sort_keys=True).encode()).hexdigest()
            raw_hash = hashlib.sha256(json.dumps(pa.params, sort_keys=True).encode()).hexdigest()
            if pa.parameter_hash not in (dict_hash, raw_hash):
                tampered = True

        if tampered:
            self.repo.log_audit_entry(
                session_id=session_id,
                action_id=action_id,
                user_id=user_id,
                user_role=user_role,
                tool_name=pa.tool_name,
                arguments=pa.params,
                risk_level="critical",
                confirmation_status="SECURITY_TAMPER_DETECTED",
                user_confirmed_at=now_iso,
                execution_status="TAMPERED",
                result={"error": "Parameters have been tampered with."},
                idempotency_key=pa.idempotency_key,
            )
            return False, {"status": "SECURITY_ERROR", "message": "Parameters have been tampered with."}, pa

        # Idempotency check against previously executed keys
        if pa.idempotency_key and pa.idempotency_key in self.executed_idempotency_keys:
            prev = self.executed_idempotency_keys[pa.idempotency_key]
            self.repo.log_audit_entry(
                session_id=session_id,
                action_id=action_id,
                user_id=user_id,
                user_role=user_role,
                tool_name=pa.tool_name,
                arguments=pa.params,
                risk_level="high",
                confirmation_status="DUPLICATE_IDEMPOTENCY_KEY",
                user_confirmed_at=now_iso,
                execution_status="ALREADY_EXECUTED",
                result=prev.get("result", {}),
                idempotency_key=pa.idempotency_key,
            )
            return False, {
                "status": "ALREADY_EXECUTED",
                "message": f"Operation with idempotency key '{pa.idempotency_key}' was already executed.",
                "result": prev.get("result", {}),
            }, pa

        return True, {}, pa

    def execute_confirmed_action(
        self,
        action_id: str,
        user_id: str,
        user_role: str,
        execute_fn: Any,
        session_id: str = "SESSION-UNKNOWN",
    ) -> Dict[str, Any]:
        """
        Executes a pending action after approval. Validates authorization, expiry,
        and parameter tamper protection. Logs to append-only audit log.
        """
        valid, err_res, pa = self._validate_and_authorize_action(
            action_id=action_id,
            user_id=user_id,
            user_role=user_role,
            session_id=session_id,
        )
        if not valid:
            return err_res

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        try:
            res = execute_fn(pa)
            if inspect.isawaitable(res):
                import asyncio
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Running in event loop, schedule it
                    res = loop.run_until_complete(res)
                else:
                    res = asyncio.run(res)

            status = "SUCCESS" if res.get("status") in ("SUCCESS", "ALREADY_EXISTS", "ok") else "FAILED"
            self.repo.log_audit_entry(
                session_id=session_id,
                action_id=action_id,
                user_id=user_id,
                user_role=user_role,
                tool_name=pa.tool_name,
                arguments=pa.params,
                risk_level="high",
                confirmation_status="CONFIRMED",
                user_confirmed_at=now_iso,
                execution_status=status,
                result=res,
                idempotency_key=pa.idempotency_key,
            )

            # Record in executed history and clean up pending
            self.executed_actions[action_id] = {
                "tool_name": pa.tool_name,
                "params": pa.params,
                "status": status,
                "result": res,
                "idempotency_key": pa.idempotency_key,
            }
            if pa.idempotency_key:
                self.executed_idempotency_keys[pa.idempotency_key] = {
                    "action_id": action_id,
                    "result": res,
                }
            self.pending_actions.pop(action_id, None)
            return res
        except Exception as e:
            logger.error(f"Error executing confirmed action {action_id}: {e}")
            fail_res = {"status": "FAILED", "message": str(e)}
            self.repo.log_audit_entry(
                session_id=session_id,
                action_id=action_id,
                user_id=user_id,
                user_role=user_role,
                tool_name=pa.tool_name,
                arguments=pa.params,
                risk_level="high",
                confirmation_status="CONFIRMED",
                user_confirmed_at=now_iso,
                execution_status="FAILED",
                result=fail_res,
                idempotency_key=pa.idempotency_key,
            )
            return fail_res

    async def execute_confirmed_action_async(
        self,
        action_id: str,
        user_id: str,
        user_role: str,
        execute_fn: Any,
        session_id: str = "SESSION-UNKNOWN",
    ) -> Dict[str, Any]:
        """
        Asynchronous variant of execute_confirmed_action. Validates authorization, expiry,
        and parameters, awaits execute_fn(pa), and logs to append-only audit log.
        """
        valid, err_res, pa = self._validate_and_authorize_action(
            action_id=action_id,
            user_id=user_id,
            user_role=user_role,
            session_id=session_id,
        )
        if not valid:
            return err_res

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        try:
            if inspect.iscoroutinefunction(execute_fn):
                res = await execute_fn(pa)
            else:
                res = execute_fn(pa)
                if inspect.isawaitable(res):
                    res = await res

            status = "SUCCESS" if res.get("status") in ("SUCCESS", "ALREADY_EXISTS", "ok") else "FAILED"
            self.repo.log_audit_entry(
                session_id=session_id,
                action_id=action_id,
                user_id=user_id,
                user_role=user_role,
                tool_name=pa.tool_name,
                arguments=pa.params,
                risk_level="high",
                confirmation_status="CONFIRMED",
                user_confirmed_at=now_iso,
                execution_status=status,
                result=res,
                idempotency_key=pa.idempotency_key,
            )

            # Record in executed history and clean up pending
            self.executed_actions[action_id] = {
                "tool_name": pa.tool_name,
                "params": pa.params,
                "status": status,
                "result": res,
                "idempotency_key": pa.idempotency_key,
            }
            if pa.idempotency_key:
                self.executed_idempotency_keys[pa.idempotency_key] = {
                    "action_id": action_id,
                    "result": res,
                }
            self.pending_actions.pop(action_id, None)
            return res
        except Exception as e:
            logger.error(f"Error executing confirmed action {action_id}: {e}")
            fail_res = {"status": "FAILED", "message": str(e)}
            self.repo.log_audit_entry(
                session_id=session_id,
                action_id=action_id,
                user_id=user_id,
                user_role=user_role,
                tool_name=pa.tool_name,
                arguments=pa.params,
                risk_level="high",
                confirmation_status="CONFIRMED",
                user_confirmed_at=now_iso,
                execution_status="FAILED",
                result=fail_res,
                idempotency_key=pa.idempotency_key,
            )
            return fail_res

