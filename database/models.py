import datetime
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, field_validator


# --- MCP Capability Metadata ---
class ToolMetadata(BaseModel):
    operation_type: Literal["read", "write"]
    risk_level: Literal["none", "low", "medium", "high"]
    requires_confirmation: bool = False
    required_role: Literal["Operator", "Manager", "Admin"] = "Operator"
    idempotent: bool = True
    domain: str


class DiscoveredTool(BaseModel):
    name: str
    description: str
    input_schema: Dict[str, Any]
    owning_server: str
    metadata: ToolMetadata


# --- ReAct Protocol Models ---
class ReActAction(BaseModel):
    action: str  # tool_name or 'final'
    args: Dict[str, Any] = Field(default_factory=dict)
    answer: Optional[str] = None


class TaskEvaluation(BaseModel):
    outcome: Literal["SUCCESS", "PARTIAL_SUCCESS", "NEEDS_CLARIFICATION", "FAILED", "DEGRADED"] = "SUCCESS"
    tool_execution_status: str = "ALL_PASSED"
    input_validity: str = "VALID_ENTITIES"
    evidence_sufficiency: str = "COMPLETE_EVIDENCE"
    business_consistency: str = "CONSISTENT"
    response_completeness: str = "DIRECTLY_ANSWERS_QUERY"
    safety_compliance: str = "COMPLIANT"
    llm_availability: str = "ONLINE"
    details: str = "Task evaluated successfully."


class ReflectionResult(BaseModel):
    status: Literal["ready", "needs_tool"]
    reason: str
    missing_tool: Optional[str] = None
    outcome: Literal["SUCCESS", "PARTIAL_SUCCESS", "NEEDS_CLARIFICATION", "FAILED", "DEGRADED"] = "SUCCESS"
    evaluation: Optional[TaskEvaluation] = None


# --- Pending Action (Safety Gateway) ---
class PendingAction(BaseModel):
    action_id: str
    tool_name: str
    owning_server: str
    user_id: str
    user_role: str
    action_type: str = "create_purchase_order"
    params: Dict[str, Any] = Field(default_factory=dict)

    # Fields for create_purchase_order compatibility
    supplier_id: Optional[str] = None
    supplier_name: Optional[str] = None
    product_id: Optional[str] = None
    product_name: Optional[str] = None
    quantity: Optional[int] = None
    unit_price: Optional[Decimal] = None
    total_cost: Optional[Decimal] = None
    lead_time_days: Optional[int] = None
    reason: Optional[str] = None

    # Fields for receive_goods compatibility
    po_id: Optional[str] = None
    received_by: Optional[str] = None

    # Fields for Google Sheets write operations
    spreadsheet_id: Optional[str] = None
    worksheet_name: Optional[str] = None
    target_row: Optional[int] = None
    fields_changed: Optional[Dict[str, Any]] = None
    proposed_values: Optional[Dict[str, Any]] = None

    idempotency_key: str
    created_at: str
    expires_at: Optional[str] = None
    parameter_hash: Optional[str] = None
    trade_off_note: Optional[str] = None

    @field_validator("quantity")
    def validate_qty(cls, v):
        if v is not None and v <= 0:
            raise ValueError("Quantity must be greater than zero")
        return v

    @field_validator("unit_price", "total_cost", mode="before")
    def validate_cost(cls, v):
        if v is None:
            return None
        dec = Decimal(str(v))
        if dec < 0:
            raise ValueError("Monetary amount cannot be negative")
        return dec

    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        try:
            exp = datetime.datetime.fromisoformat(self.expires_at)
            now = datetime.datetime.now(datetime.timezone.utc)
            return now > exp
        except Exception:
            return False


# --- Domain Output Contracts ---
class StockInfo(BaseModel):
    product_id: str
    product_name: str
    sku: str
    on_hand_qty: int
    reserved_qty: int
    allocated_qty: int
    available_qty: int
    incoming_qty: int = 0
    open_purchase_orders: List[str] = Field(default_factory=list)
    reorder_threshold: Optional[int] = None
    is_low_stock: bool
    is_out_of_stock: bool = False
    classification: str = "IN_STOCK"
    available_pct: float = 0.0
    location: str


class SupplierOption(BaseModel):
    supplier_id: str
    supplier_name: str
    contact_email: str
    product_id: str
    product_name: str
    unit_price: float
    available_qty: int
    lead_time_days: int
    min_order_qty: int
    total_cost_for_req: float
    can_fulfill_qty: bool
    rating: float
    reliability_score: float
    rank: int
    trade_off_vs_recommendation: Optional[str] = None

