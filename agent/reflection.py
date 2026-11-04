import json
import logging
import re
from typing import Any, Dict, List, Literal, Optional
from domain.models import ReflectionResult, TaskEvaluation

logger = logging.getLogger(__name__)


class ReflectionEngine:
    @classmethod
    def evaluate_task(
        cls,
        user_query: str,
        transcript: List[Dict[str, Any]],
        proposed_answer: str,
        llm_online: bool = True,
        grounding_result: Optional[Dict[str, Any]] = None,
    ) -> TaskEvaluation:
        """
        Multi-dimensional task success and business consistency evaluation.
        Separates tool execution, input validity, evidence sufficiency, business consistency,
        response completeness, safety compliance, and LLM availability.
        Integrates Layer 1 deterministic numerical verification and Grounding check.
        """
        # 1. Tool Execution Status
        tool_status = "ALL_PASSED"
        has_failure = False
        has_timeout = False
        called_tools = []

        for step in transcript:
            action = step.get("action", "")
            called_tools.append(action)
            obs = step.get("observation", {})
            if isinstance(obs, dict):
                st = obs.get("status")
                if st in ("SERVICE_FAILURE", "EXECUTION_ERROR", "INVALID_ARGUMENTS", "TOOL_NOT_FOUND"):
                    has_failure = True
                elif st == "TIMEOUT":
                    has_timeout = True

        if has_failure:
            tool_status = "SERVICE_FAILURE"
        elif has_timeout:
            tool_status = "TIMEOUT"
        elif not called_tools:
            tool_status = "NO_TOOLS_CALLED"

        # 2. Input Validity Check (e.g. check for corrupted customer/product entities)
        input_validity = "VALID_ENTITIES"
        for step in transcript:
            args = step.get("args", {})
            for k, v in args.items():
                if isinstance(v, str):
                    if re.search(r"^(?:t\s+we\s+shipped|we\s+shipped|why\s+haven't)", v.strip(), re.IGNORECASE):
                        input_validity = "CORRUPTED_ENTITY"
                        break
                    if len(v.split()) > 6 and ("id" in k or "name" in k):
                        input_validity = "CORRUPTED_ENTITY"
                        break

        # 3. Evidence Sufficiency
        q_lower = user_query.lower()
        evidence_sufficiency = "COMPLETE_EVIDENCE"
        missing_aspects = []

        if any(w in q_lower for w in ["ship", "why haven't", "why have not"]):
            has_orders = any("orders" in t for t in called_tools)
            has_inv = any("inventory" in t for t in called_tools)
            if not has_orders:
                missing_aspects.append("order records")
            if not has_inv:
                missing_aspects.append("inventory check")

        if any(w in q_lower for w in ["fulfill", "can we fulfill"]):
            has_inv = any("inventory" in t for t in called_tools)
            if not has_inv:
                missing_aspects.append("inventory check")

        if missing_aspects:
            evidence_sufficiency = f"INSUFFICIENT_EVIDENCE (Missing: {', '.join(missing_aspects)})"

        # 4. Layer 1 Deterministic Verification & Grounding Check
        layer1_check = cls.run_layer1_deterministic_check(transcript, proposed_answer, user_query)
        has_unverified_claims = False
        unverified_details = ""
        if grounding_result and not grounding_result.get("is_grounded", True):
            has_unverified_claims = True
            unverified_details = "; ".join(grounding_result.get("reasons", ["Ungrounded entities or numbers"]))
        elif layer1_check and layer1_check.status != "ready":
            has_unverified_claims = True
            unverified_details = layer1_check.reason

        # 5. Business Consistency
        business_consistency = "CONSISTENT"
        if has_unverified_claims:
            business_consistency = "UNVERIFIED_CLAIMS"
        elif "shortage" in proposed_answer.lower() or "not fulfill" in proposed_answer.lower() or "stock is 0" in proposed_answer.lower():
            confirmed_shortage = False
            for step in transcript:
                obs = step.get("observation", {})
                if isinstance(obs, dict) and obs.get("status") == "SUCCESS":
                    if obs.get("all_fulfillable") is False:
                        confirmed_shortage = True
                    d = obs.get("data", {})
                    if isinstance(d, dict) and d.get("available_qty") == 0:
                        confirmed_shortage = True
            if not confirmed_shortage and evidence_sufficiency == "COMPLETE_EVIDENCE":
                business_consistency = "UNVERIFIED_SHORTAGE_CLAIM"

        # 6. Response Completeness
        if proposed_answer.strip().startswith("{") and proposed_answer.strip().endswith("}"):
            response_completeness = "RAW_JSON_DUMP"
        elif "Details: {" in proposed_answer:
            response_completeness = "UNFORMATTED_RAW_TOOL_OUTPUT"
        else:
            response_completeness = "DIRECTLY_ANSWERS_QUERY"

        # 7. Safety Compliance
        safety_compliance = "COMPLIANT"
        for step in transcript:
            action = step.get("action", "")
            if "create_purchase_order" in action:
                args = step.get("args", {})
                if "idempotency_key" not in args:
                    safety_compliance = "MISSING_IDEMPOTENCY_KEY"

        # 8. LLM Availability
        llm_avail = "ONLINE" if llm_online else "DEGRADED"

        # Overall Outcome Synthesis
        if has_unverified_claims:
            outcome = "FAILED"
            details = f"Validation failed: {unverified_details}"
        elif input_validity == "CORRUPTED_ENTITY":
            outcome = "FAILED"
            details = "Input entity parameters were corrupted or malformed during extraction."
        elif has_failure or has_timeout:
            outcome = "FAILED"
            details = f"Underlying tool execution failed: {tool_status}"
        elif "INSUFFICIENT" in evidence_sufficiency:
            outcome = "NEEDS_CLARIFICATION"
            details = evidence_sufficiency
        elif business_consistency == "UNVERIFIED_SHORTAGE_CLAIM":
            outcome = "PARTIAL_SUCCESS"
            details = "Claimed stock shortage is not backed by authoritative tool results."
        elif not llm_online:
            outcome = "DEGRADED"
            details = "Completed safely via deterministic fallback engine."
        else:
            outcome = "SUCCESS"
            details = "Task completed successfully with verified tool evidence."

        return TaskEvaluation(
            outcome=outcome,
            tool_execution_status=tool_status,
            input_validity=input_validity,
            evidence_sufficiency=evidence_sufficiency,
            business_consistency=business_consistency,
            response_completeness=response_completeness,
            safety_compliance=safety_compliance,
            llm_availability=llm_avail,
            details=details,
        )

    @classmethod
    def run_layer1_deterministic_check(
        cls, transcript: List[Dict[str, Any]], proposed_answer: str, user_query: str = ""
    ) -> Optional[ReflectionResult]:
        """
        Layer 1: Code-based deterministic verification checks.
        Prevents false success reporting and identifies missing tools or corrupted state.
        """
        # Check 1: Tool failures or timeouts
        for step in transcript:
            obs = step.get("observation", {})
            if isinstance(obs, dict):
                status = obs.get("status")
                if status == "SERVICE_FAILURE":
                    return ReflectionResult(
                        status="needs_tool",
                        reason=f"Tool execution failed on server. Missing valid observation data: {obs.get('message')}",
                        outcome="FAILED",
                    )
                if status == "TIMEOUT":
                    return ReflectionResult(
                        status="needs_tool",
                        reason="Tool execution timed out. Data could not be confirmed.",
                        outcome="FAILED",
                    )

        # Check 2: Numerical stock claim verification against observations
        observed_availabilities = []
        has_shortage_obs = False
        for step in transcript:
            obs = step.get("observation", {})
            if isinstance(obs, dict) and obs.get("status") == "SUCCESS":
                data = obs.get("data", {})
                if isinstance(data, dict):
                    if "available_qty" in data and isinstance(data["available_qty"], (int, float)):
                        observed_availabilities.append(int(data["available_qty"]))
                # Check items in data or at root observation level (e.g. receive_goods)
                items_list = []
                if "items" in obs and isinstance(obs["items"], list):
                    items_list.extend(obs["items"])
                if isinstance(data, dict) and "items" in data and isinstance(data["items"], list):
                    items_list.extend(data["items"])
                for it in items_list:
                    if isinstance(it, dict):
                        for k in ("available_qty", "new_available_qty", "received_qty", "quantity", "on_hand_qty", "new_on_hand_qty"):
                            if k in it and isinstance(it[k], (int, float)):
                                observed_availabilities.append(int(it[k]))
                if obs.get("all_fulfillable") is False:
                    has_shortage_obs = True

        # Extract claimed stock numbers from proposed answer
        ans_lower = proposed_answer.lower()
        stock_patterns = [
            r"(?:available\s+stock\s+is|stock\s+is|available\s+qty(?:\s+is)?|available:?)\s*(\d+)",
            r"(\d+)\s+units?\s+(?:available|in\s+stock)",
            r"available\s+stock\s+of\s+(\d+)",
        ]
        claimed_stock_numbers = []
        for pat in stock_patterns:
            for match in re.finditer(pat, ans_lower):
                claimed_stock_numbers.append(int(match.group(1)))

        if "stock is 0" in ans_lower or "zero stock" in ans_lower or "no stock" in ans_lower or "available stock is 0" in ans_lower:
            claimed_stock_numbers.append(0)

        # If a stock quantity was claimed (e.g. 0), verify it is grounded in observed data
        if claimed_stock_numbers:
            for num in claimed_stock_numbers:
                if num == 0 and (0 in observed_availabilities or has_shortage_obs):
                    continue  # Grounded and verified
                if num in observed_availabilities:
                    continue  # Grounded and verified
                return ReflectionResult(
                    status="needs_tool",
                    reason=f"Proposed answer claims stock quantity {num}, but tool observations contain {observed_availabilities}.",
                    missing_tool="inventory.get_stock",
                    outcome="PARTIAL_SUCCESS",
                )
            if transcript:
                return ReflectionResult(
                    status="ready",
                    reason="Deterministic verification passed: numerical claims grounded in observations.",
                    outcome="SUCCESS",
                )

        return None

    @staticmethod
    def parse_llm_reflection_response(raw_llm_json: Dict[str, Any]) -> ReflectionResult:
        """
        Layer 2: Parses and validates LLM Reflection JSON output.
        """
        status = raw_llm_json.get("status", "ready")
        reason = raw_llm_json.get("reason", "Observation transcript verified.")
        missing_tool = raw_llm_json.get("missing_tool")

        if status not in ("ready", "needs_tool"):
            status = "ready"

        outcome = "SUCCESS" if status == "ready" else "PARTIAL_SUCCESS"

        return ReflectionResult(
            status=status,
            reason=reason,
            missing_tool=missing_tool,
            outcome=outcome,
        )
