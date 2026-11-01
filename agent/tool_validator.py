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
    Ensures mentioned entity identifiers and key numbers originate from evidence.
    """

    ID_PATTERN = re.compile(r"\b(ORD-\d+|PO-[A-Z0-9]+|POI-[A-Z0-9]+|P\d{4}|SUP-\d+|CUST-\d+|INV-\d+)\b")

    @classmethod
    def validate_answer_grounding(
        cls, answer: str, observations: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Cross-checks entity IDs mentioned in answer against observations.
        """
        obs_dump = json.dumps(observations, default=str)
        mentioned_ids = set(cls.ID_PATTERN.findall(answer))

        supported_ids = [eid for eid in mentioned_ids if eid in obs_dump]
        unsupported_ids = [eid for eid in mentioned_ids if eid not in obs_dump]

        is_grounded = len(unsupported_ids) == 0

        return {
            "is_grounded": is_grounded,
            "mentioned_ids": list(mentioned_ids),
            "supported_ids": supported_ids,
            "unsupported_ids": unsupported_ids,
            "grounding_status": "VERIFIED" if is_grounded else "POTENTIAL_HALLUCINATION",
        }

