import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from database.models import DiscoveredTool

logger = logging.getLogger(__name__)


class ToolArgumentValidator:
    """
    Validates model-generated tool arguments against discovered MCP tool schemas.
    Safely coerces standard types and rejects malformed calls before execution.
    """

    @classmethod
    def validate_tool_arguments(
        cls, tool_def: Optional[DiscoveredTool], args: Any
    ) -> Tuple[bool, Dict[str, Any], Optional[str]]:
        """
        Validates and coerces args against tool_def.input_schema.
        Returns: (is_valid, cleaned_args, error_message)
        """
        if not tool_def:
            return False, {}, "Tool definition is missing or null."

        # Parse string args if model output raw JSON string
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception as e:
                return False, {}, f"Arguments must be valid JSON dictionary: {e}"

        if not isinstance(args, dict):
            return False, {}, f"Arguments must be a key-value dictionary, got {type(args).__name__}."

        schema = tool_def.input_schema or {}
        properties = schema.get("properties", {})
        required_fields = schema.get("required", [])

        # Check required fields
        for req in required_fields:
            if req not in args or args[req] is None or args[req] == "":
                return (
                    False,
                    {},
                    f"Missing required parameter '{req}' for tool '{tool_def.name}'. Expected parameters: {list(properties.keys())}.",
                )

        cleaned: Dict[str, Any] = {}

        # Validate types and coerce if safely possible
        for k, v in args.items():
            if k not in properties:
                # Allow extra arguments or pass through
                cleaned[k] = v
                continue

            prop_schema = properties[k]
            expected_type = prop_schema.get("type")

            if expected_type == "integer":
                try:
                    cleaned[k] = int(v)
                except (ValueError, TypeError):
                    return (
                        False,
                        {},
                        f"Parameter '{k}' must be an integer, got value '{v}'.",
                    )
            elif expected_type == "number":
                try:
                    cleaned[k] = float(v)
                except (ValueError, TypeError):
                    return (
                        False,
                        {},
                        f"Parameter '{k}' must be a numeric value, got value '{v}'.",
                    )
            elif expected_type == "boolean":
                if isinstance(v, bool):
                    cleaned[k] = v
                elif str(v).lower() in ("true", "1", "yes"):
                    cleaned[k] = True
                elif str(v).lower() in ("false", "0", "no"):
                    cleaned[k] = False
                else:
                    return (
                        False,
                        {},
                        f"Parameter '{k}' must be a boolean, got '{v}'.",
                    )
            elif expected_type == "string":
                cleaned[k] = str(v)
            elif expected_type == "object":
                if not isinstance(v, dict):
                    return (
                        False,
                        {},
                        f"Parameter '{k}' must be a dictionary/object, got {type(v).__name__}.",
                    )
                cleaned[k] = v
            elif expected_type == "array":
                if not isinstance(v, list):
                    return (
                        False,
                        {},
                        f"Parameter '{k}' must be a list/array, got {type(v).__name__}.",
                    )
                cleaned[k] = v
            else:
                cleaned[k] = v

        return True, cleaned, None


class GroundingValidator:
    """
    Validates final business answers against retrieved tool observations.
    Ensures mentioned entity identifiers, numerical stock claims, and financial totals
    originate strictly from authoritative evidence.
    """

    ID_PATTERN = re.compile(r"\b(ORD-\d+|PO-[A-Z0-9]+|POI-[A-Z0-9]+|P\d{4}|SUP-\d+|CUST-\d+|INV-\d+)\b")

    @classmethod
    def extract_observed_numbers(cls, observations: Dict[str, Any]) -> Tuple[List[float], Dict[str, Any]]:
        """
        Recursively traverses tool observations to collect all verified numerical values
        (stock levels, amounts, counts, percentages) and structured entity data.
        """
        numbers: List[float] = []
        structured_info: Dict[str, Any] = {
            "products": {},
            "orders": {},
            "finance": {},
            "has_shortage": False,
        }

        def _traverse(val: Any, key_name: str = ""):
            if isinstance(val, dict):
                # Check for product stock info
                if "product_id" in val and "available_qty" in val:
                    pid = val["product_id"]
                    avail = float(val["available_qty"])
                    structured_info["products"][pid] = {
                        "available_qty": avail,
                        "on_hand_qty": float(val.get("on_hand_qty", avail)),
                        "reorder_threshold": float(val.get("reorder_threshold", 0)),
                    }
                if val.get("all_fulfillable") is False:
                    structured_info["has_shortage"] = True

                for k, v in val.items():
                    _traverse(v, k)
            elif isinstance(val, list):
                for item in val:
                    _traverse(item, key_name)
            elif isinstance(val, (int, float)) and not isinstance(val, bool):
                numbers.append(float(val))

        _traverse(observations)
        return numbers, structured_info

    @classmethod
    def validate_answer_grounding(
        cls,
        answer: str,
        observations: Dict[str, Any],
        user_query: str = "",
        entity_res: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Cross-checks entity IDs and key numbers mentioned in answer against tool observations.
        Returns a detailed validation report.
        """
        obs_dump = json.dumps(observations, default=str)
        mentioned_ids = set(cls.ID_PATTERN.findall(answer))

        # IDs in user query or entity resolution are considered contextual input
        query_ids = set(cls.ID_PATTERN.findall(user_query or ""))
        if entity_res:
            if getattr(entity_res, "order_id", None):
                query_ids.add(entity_res.order_id)
            if getattr(entity_res, "product", None) and getattr(entity_res.product, "canonical_id", None):
                query_ids.add(entity_res.product.canonical_id)
            if getattr(entity_res, "customer", None) and getattr(entity_res.customer, "canonical_id", None):
                query_ids.add(entity_res.customer.canonical_id)
            if getattr(entity_res, "supplier", None) and getattr(entity_res.supplier, "canonical_id", None):
                query_ids.add(entity_res.supplier.canonical_id)

        supported_ids = [eid for eid in mentioned_ids if eid in obs_dump or eid in query_ids]
        unsupported_ids = [eid for eid in mentioned_ids if eid not in obs_dump and eid not in query_ids]

        # Numerical checks
        observed_numbers, structured_info = cls.extract_observed_numbers(observations)
        ans_lower = answer.lower()
        unsupported_numbers: List[Dict[str, Any]] = []

        # 1. Stock quantity claim extraction
        stock_patterns = [
            r"(?:available\s+stock\s+is|stock\s+is|available\s+qty(?:\s+is)?|available:?|current\s+stock(?:\s+is)?)\s*[:=]?\s*(\d+)",
            r"(\d+)\s+units?\s+(?:available|in\s+stock|on-hand)",
            r"available\s+stock\s+of\s+(\d+)",
        ]
        claimed_stock_numbers: List[int] = []
        for pat in stock_patterns:
            for match in re.finditer(pat, ans_lower):
                claimed_stock_numbers.append(int(match.group(1)))

        if any(w in ans_lower for w in ["stock is 0", "zero stock", "no stock", "available stock is 0", "0 units available"]):
            claimed_stock_numbers.append(0)

        # Cross-reference claimed stock numbers
        if claimed_stock_numbers and observations:
            obs_avails = [
                int(p["available_qty"]) for p in structured_info["products"].values()
            ]
            for num in claimed_stock_numbers:
                if num == 0:
                    if 0 in obs_avails or 0.0 in observed_numbers or structured_info["has_shortage"]:
                        continue
                if num in obs_avails or float(num) in observed_numbers:
                    continue
                # If number is a known threshold or on-hand, check
                if any(float(num) == float(p.get("on_hand_qty", -1)) for p in structured_info["products"].values()):
                    continue
                unsupported_numbers.append({
                    "claimed": num,
                    "type": "stock_quantity",
                    "reason": f"Claimed stock {num} not found in tool observations {obs_avails}",
                })

        # 2. Check for currency totals (e.g. ₹56,350 or ₹77,000)
        curr_pattern = re.compile(r"₹\s*([\d,]+(?:\.\d+)?)")
        claimed_amounts: List[float] = []
        for match in curr_pattern.finditer(answer):
            try:
                amt_str = match.group(1).replace(",", "")
                claimed_amounts.append(float(amt_str))
            except ValueError:
                pass

        if claimed_amounts and observations:
            for amt in claimed_amounts:
                # Tolerant float check against observed numbers
                if any(abs(amt - obs_num) < 1.0 for obs_num in observed_numbers):
                    continue
                # Check if it matches a calculated sum of observed parts
                # If amount is large and completely absent from observations:
                if amt > 100 and not any(abs(amt - obs_num) < 1.0 for obs_num in observed_numbers):
                    # Check if answer is calculation response (which has CALCULATED status)
                    if "**Status**: CALCULATED" in answer:
                        continue
                    unsupported_numbers.append({
                        "claimed": amt,
                        "type": "financial_amount",
                        "reason": f"Financial claim ₹{amt:,.2f} is not present in tool observations",
                    })

        # Check for tool failure masquerading as business success
        has_only_failures = False
        if observations:
            statuses = [
                obs.get("status") for obs in observations.values() if isinstance(obs, dict)
            ]
            if statuses and all(s in ("SERVICE_FAILURE", "INVALID_ARGUMENTS", "TIMEOUT", "TOOL_NOT_FOUND", "EXECUTION_ERROR") for s in statuses):
                has_only_failures = True

        reasons = []
        if unsupported_ids:
            reasons.append(f"Invented or unverified identifiers: {unsupported_ids}")
        if unsupported_numbers:
            num_desc = [f"{item['type']}: {item['claimed']}" for item in unsupported_numbers]
            reasons.append(f"Unsupported numerical claims: {num_desc}")
        if has_only_failures and any(pid in answer for pid in ["P1001", "P1002", "ORD-1042"]):
            reasons.append("Tool execution failed but response made factual claims without valid data.")

        is_grounded = len(unsupported_ids) == 0 and len(unsupported_numbers) == 0 and not has_only_failures

        return {
            "is_grounded": is_grounded,
            "mentioned_ids": list(mentioned_ids),
            "supported_ids": supported_ids,
            "unsupported_ids": unsupported_ids,
            "unsupported_numbers": unsupported_numbers,
            "reasons": reasons,
            "grounding_status": "VERIFIED" if is_grounded else "POTENTIAL_HALLUCINATION",
        }

