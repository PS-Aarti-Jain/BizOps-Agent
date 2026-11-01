import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Union

from app.config import (
    DEFAULT_USER_ID,
    DEFAULT_USER_ROLE,
    DEVELOPER_MODE,
    MAX_REACT_ITERATIONS,
    MULTI_AGENT,
    OBSERVATION_CHAR_LIMIT,
    QUERY_TIMEOUT_SECONDS,
)
from database.models import DiscoveredTool, ReActAction
from domain.entity_resolver import EntityResolver
from llm.base import LLMClient
from llm.groq_client import GroqClient
from llm.prompts import REACT_SYSTEM_PROMPT
from mcp.router import MCPRouterManager
from security.redaction import SecuritySanitizer
from security.safety_gateway import SafetyGateway
from services.date_resolver import get_current_date
from services.language_policy import LanguagePolicy
from services.query_normalizer import normalize_query_typos
from agent.domain_guard import DomainGuard, OUT_OF_DOMAIN_REJECTION
from agent.planner import AggregatorAgent, PlannerAgent
from agent.react_loop import DeterministicFallbackEngine
from agent.tool_validator import GroundingValidator, ToolArgumentValidator
from agent.response_formatter import ResponseFormatter
from agent.state import ConversationMemory
from agent.tool_selector import ToolSelector

logger = logging.getLogger(__name__)


class BizOpsAgent:
    """
    Central BizOps Agent Orchestrator.
    Executes business operations queries via ReAct reasoning loop,
    integrates official Groq API as primary LLM, Google Sheets external data,
    authoritative MCP services, and strict Safety Gateway confirmation controls.
    """

    def __init__(
        self,
        user_id: str = DEFAULT_USER_ID,
        user_role: str = DEFAULT_USER_ROLE,
        auto_confirm_writes: bool = False,
        interactive_confirm_fn: Optional[Callable[[str], bool]] = None,
        verbose: bool = True,
        router: Optional[MCPRouterManager] = None,
        safety_gateway: Optional[SafetyGateway] = None,
        memory: Optional[ConversationMemory] = None,
        llm: Optional[LLMClient] = None,
    ):
        self.user_id = user_id
        self.user_role = user_role
        self.auto_confirm_writes = auto_confirm_writes
        self.interactive_confirm_fn = interactive_confirm_fn
        self.verbose = verbose

        self.router = router or MCPRouterManager()
        self.safety_gateway = safety_gateway or SafetyGateway()
        self.memory = memory or ConversationMemory(max_turns=3)
        self.llm = llm or GroqClient()

        self.query_timeout_seconds: float = float(QUERY_TIMEOUT_SECONDS)
        self.last_transcript: List[Dict[str, Any]] = []
        if hasattr(self.llm, "model_name"):
            self.last_llm_display_label = f"LLM: {getattr(self.llm, 'provider_name', 'groq')}/{self.llm.model_name}"
        else:
            self.last_llm_display_label = "LLM: rule-based fallback"
        self._last_guard_badge: Optional[str] = None

    @property
    def ollama(self) -> LLMClient:
        """Backward-compatibility alias."""
        return self.llm

    @ollama.setter
    def ollama(self, client: LLMClient) -> None:
        self.llm = client

    async def initialize(self):
        """
        Connects concurrently to MCP servers and Google Sheets integration,
        and verifies LLM provider availability.
        """
        await self.router.initialize_connections()
        if hasattr(self, "llm") and self.llm:
            try:
                await self.llm.is_available()
            except Exception as e:
                logger.warning(f"LLM warm-up check encountered notice: {e}")

    async def close(self):
        """Cleanly closes open router sessions and stdio processes."""
        if hasattr(self, "router") and self.router and hasattr(self.router, "close"):
            await self.router.close()

    def _format_provenance_badge(self) -> str:
        """Determines honest response provenance."""
        if getattr(self, "_last_guard_badge", None):
            badge = self._last_guard_badge
            self._last_guard_badge = None
            return badge
        if hasattr(self.llm, "provider_name") and self.llm.provider_name == "groq":
            if getattr(self.llm, "last_status", "") == "SUCCESS":
                return f"LLM: groq/{self.llm.model_name}"
            elif getattr(self.llm, "last_status", "") == "KEY_MISSING":
                return "LLM: rule-based fallback (GROQ_API_KEY unset)"
        lbl = getattr(self, "last_llm_display_label", "")
        if lbl and lbl != "UNKNOWN":
            return lbl
        if hasattr(self.llm, "model_name"):
            return f"LLM: {getattr(self.llm, 'provider_name', 'groq')}/{self.llm.model_name}"
        return "LLM: rule-based fallback"

    def _append_response_footer(self, answer: str, elapsed_seconds: Optional[float] = None) -> str:
        if not isinstance(answer, str) or os.getenv("ENVIRONMENT") == "test":
            return answer
        badge = self._format_provenance_badge()
        parts = [badge]
        if elapsed_seconds is not None:
            parts.append(f"{elapsed_seconds:.2f}s")
        footer = f"\n\n---\n*[{' | '.join(parts)}]*"
        if footer not in answer:
            return answer + footer
        return answer

    async def run_query(
        self,
        user_query: str,
        session_id: Optional[str] = None,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> Union[str, Dict[str, Any]]:
        active_session = session_id or f"SESS-{uuid.uuid4().hex[:8].upper()}"

        # Language policy: Detect language with session persistence
        current_lang = self.memory.get_language(active_session)
        detected_lang = LanguagePolicy.detect_language(user_query, current_session_lang=current_lang)
        self.memory.set_language(active_session, detected_lang)

        # Typo tolerance: Normalize query before follow-up resolution
        clean_user_query = normalize_query_typos(user_query)

        # 1. Resolve conversational follow-up references using session context
        resolved_query, context_params = self.memory.resolve_follow_up(active_session, clean_user_query)
        if resolved_query == "CANCEL_OPERATION":
            cancel_msg = (
                "**Answer**: The pending operation has been cancelled as requested.\n\n"
                "**Status**: CANCELLED_BY_USER\n\n"
                "**Next Step**: No records or transactions were modified."
            )
            self.memory.add_turn(active_session, user_query, cancel_msg, intent="cancel")
            return self._append_response_footer(cancel_msg, 0.05)

        target_query = resolved_query

        # Multi-Agent Planner check for composite queries
        if MULTI_AGENT:
            subtasks = PlannerAgent.plan_subtasks(target_query)
            if len(subtasks) > 1:
                if on_progress:
                    on_progress(f"Decomposing query into {len(subtasks)} operational tasks...")
                results = []
                for sub in subtasks:
                    res = await self._run_single_query(sub, active_session, on_progress)
                    if isinstance(res, dict) and res.get("status") == "PENDING_CONFIRMATION":
                        return res
                    results.append(str(res))
                aggregated = AggregatorAgent.aggregate_results(target_query, subtasks, results)
                self.memory.add_turn(active_session, user_query, aggregated)
                return aggregated

        res = await self._run_single_query(target_query, active_session, on_progress)
        if isinstance(res, str):
            # Extract structured context to retain for follow-ups
            ent_dict = {}
            if hasattr(self, "_last_entity_res") and self._last_entity_res:
                er = self._last_entity_res
                if er.product:
                    ent_dict["product_id"] = er.product.canonical_id
                    ent_dict["product_name"] = er.product.canonical_name
                if er.supplier:
                    ent_dict["supplier_id"] = er.supplier.canonical_id
                    ent_dict["supplier_name"] = er.supplier.canonical_name
                if er.customer:
                    ent_dict["customer_id"] = er.customer.canonical_id
                    ent_dict["customer_name"] = er.customer.canonical_name
                if er.quantity:
                    ent_dict["quantity"] = er.quantity

            last_tool = None
            last_tool_args = None
            if self.last_transcript:
                last_step = self.last_transcript[-1]
                last_tool = last_step.get("action")
                last_tool_args = last_step.get("args")

            detected_intent = None
            q_low = target_query.lower()
            if OUT_OF_DOMAIN_REJECTION in str(res):
                detected_intent = "out_of_domain"
            elif any(w in str(res) for w in ["Hello! I'm BizOps Agent", "You're welcome!", "Goodbye!"]):
                detected_intent = "conversational"
            elif "**Status**: CALCULATED" in str(res):
                detected_intent = "business_calculation"
            elif any(w in q_low for w in ["revenue", "financial summary", "invoice", "sales"]):
                detected_intent = "financial_summary"
            elif any(w in q_low for w in ["pending order", "pending orders", "orders pending", "total pending", "how many pending"]):
                detected_intent = "pending_orders"
            elif any(w in q_low for w in ["supplier performance", "biggest supplier", "top supplier"]):
                detected_intent = "supplier_performance"
            elif any(w in q_low for w in ["which supplier", "compare supplier", "vendor"]):
                detected_intent = "supplier_comparison"
            elif any(w in q_low for w in ["stock", "inventory", "available"]):
                detected_intent = "inventory"
            elif any(w in q_low for w in ["who owes", "pending payment", "pending payments"]):
                detected_intent = "pending_payments"

            self.memory.add_turn(
                session_id=active_session,
                query=user_query,
                answer=res,
                intent=detected_intent,
                entities=ent_dict,
                tool=last_tool,
                tool_args=last_tool_args,
            )
        return res

    async def _run_single_query(
        self,
        user_query: str,
        session_id: str,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> Union[str, Dict[str, Any]]:
        query_start_time = time.time()
        if on_progress:
            on_progress("Analyzing operations query...")

        resolver = EntityResolver()
        entity_res = resolver.resolve_query(user_query)
        self._last_entity_res = entity_res

        # Strict Deterministic Business Domain Guard
        guard_decision = DomainGuard.evaluate(
            user_query=user_query,
            entity_res=entity_res,
            memory=self.memory,
            session_id=session_id,
        )

        if guard_decision.category in ("OUT_OF_DOMAIN", "ADVERSARIAL"):
            self._last_guard_badge = "Domain Guard: deterministic (Groq bypassed)"
            self.last_llm_display_label = "Domain Guard: deterministic (Groq bypassed)"
            rejection_text = guard_decision.rejection_message or OUT_OF_DOMAIN_REJECTION
            return self._append_response_footer(rejection_text, time.time() - query_start_time)

        if guard_decision.category == "CONVERSATIONAL":
            self._last_guard_badge = "Domain Guard: deterministic (Groq bypassed)"
            self.last_llm_display_label = "Domain Guard: deterministic (Groq bypassed)"
            conv_text = guard_decision.conversational_response or "Hello! How can I help with business operations?"
            return self._append_response_footer(conv_text, time.time() - query_start_time)

        if guard_decision.category == "BUSINESS_CALCULATION" and guard_decision.calculation_response:
            self._last_guard_badge = "Domain Guard: deterministic (Groq bypassed)"
            self.last_llm_display_label = "Domain Guard: deterministic (Groq bypassed)"
            return self._append_response_footer(guard_decision.calculation_response, time.time() - query_start_time)

        if guard_decision.category == "MIXED" and guard_decision.in_domain_query:
            # Execute solely the in-domain subquery
            inner_res = await self._run_single_query(guard_decision.in_domain_query, session_id, on_progress)
            if isinstance(inner_res, str):
                import re as regex_mod
                clean_inner = regex_mod.sub(r"\n\n---\n\*\[.*?\]\*", "", inner_res).strip()
                ood_desc = guard_decision.out_of_domain_part or "general questions"
                mixed_answer = (
                    f"I cannot assist with general knowledge or arithmetic like '{ood_desc}'. "
                    f"However, regarding your business operations:\n\n{clean_inner}"
                )
                return self._append_response_footer(mixed_answer, time.time() - query_start_time)
            return inner_res

        # Dynamic tool selection based on intent
        relevant_tools = ToolSelector.select_relevant_tools(user_query, self.router.registry)
        tools_desc = "\n".join(
            f"- Tool: {t.metadata.domain}.{t.name} (Alias: '{t.name}')\n"
            f"  Description: {t.description}\n"
            f"  Input Schema: {t.input_schema.get('properties', {})}"
            for t in relevant_tools
        )

        transcript: List[Dict[str, Any]] = []
        self.last_transcript = transcript
        called_actions: Set[str] = set()

        # Check LLM availability
        use_fallback = not (await self.llm.is_available())
        if use_fallback:
            self.last_llm_display_label = "LLM: rule-based fallback"
        else:
            self.last_llm_display_label = f"LLM: groq/{self.llm.model_name}"

        timeout_budget = self.query_timeout_seconds

        for iteration in range(1, MAX_REACT_ITERATIONS + 1):
            elapsed = time.time() - query_start_time
            if elapsed >= timeout_budget:
                timeout_msg = (
                    f"**Status:** TIMED_OUT\n\n"
                    f"Execution reached the {timeout_budget:.1f}s time limit before concluding."
                )
                return self._append_response_footer(timeout_msg, elapsed)

            action_obj: Optional[ReActAction] = None

            # 1. LLM or Fallback Action Determination
            if not use_fallback:
                prompt_text = (
                    f"USER QUERY: {user_query}\n\n"
                    f"RECENT SESSION HISTORY:\n{self.memory.get_context_summary(session_id)}\n\n"
                    f"OBSERVATION TRANSCRIPT:\n{json.dumps(transcript, indent=2)}\n\n"
                    f"Determine next action matching the JSON schema."
                )
                today_ist = get_current_date()
                active_lang = self.memory.get_language(session_id)
                lang_instructions = LanguagePolicy.get_system_prompt_instructions(active_lang)
                sys_prompt = REACT_SYSTEM_PROMPT.format(
                    tools_description=tools_desc,
                    current_date=today_ist.strftime("%d-%m-%Y (%Y-%m-%d)"),
                    current_month=today_ist.strftime("%B %Y (%Y-%m)"),
                    language_policy_instructions=lang_instructions,
                )

                if on_progress:
                    on_progress(f"Step {iteration}: Generating action via Groq...")

                llm_response = await self.llm.generate_json(prompt_text, sys_prompt)
                if llm_response and isinstance(llm_response, dict):
                    if "answer" in llm_response and "action" not in llm_response:
                        llm_response["action"] = "final"
                    try:
                        action_obj = ReActAction(**llm_response)
                    except Exception:
                        action_obj = None

                if not action_obj:
                    logger.warning(f"Groq LLM output unparseable; engaging fallback for iteration {iteration}.")
                    use_fallback = True
                    action_obj = DeterministicFallbackEngine.determine_next_action(
                        user_query, self.router.registry, transcript, entity_res
                    )
            else:
                if on_progress:
                    on_progress(f"Step {iteration}: Resolving via deterministic business logic...")
                action_obj = DeterministicFallbackEngine.determine_next_action(
                    user_query, self.router.registry, transcript, entity_res
                )

            # 2. Check if Final Answer reached
            if action_obj.action == "final":
                final_answer = action_obj.answer or "Query completed."
                return self._append_response_footer(final_answer, time.time() - query_start_time)

            # 3. Tool Execution Handling
            resolved_tool_name = self.router.registry.resolve_tool_name(action_obj.action)
            if not resolved_tool_name:
                transcript.append({
                    "iteration": iteration,
                    "action": action_obj.action,
                    "observation": {"status": "INVALID_REQUEST", "message": f"Tool '{action_obj.action}' not found."},
                })
                continue

            tool_def = self.router.registry.get_tool(resolved_tool_name)
            
            # Argument validation against discovered MCP tool schema
            is_valid, cleaned_args, val_err = ToolArgumentValidator.validate_tool_arguments(tool_def, action_obj.args)
            if not is_valid:
                transcript.append({
                    "iteration": iteration,
                    "action": action_obj.action,
                    "args": action_obj.args,
                    "observation": {
                        "status": "INVALID_ARGUMENTS",
                        "message": val_err,
                        "expected_schema": tool_def.input_schema if tool_def else {},
                    },
                })
                continue
            action_obj.args = cleaned_args

            is_write = tool_def and tool_def.metadata.operation_type == "write"

            # 4. Safety Gateway Interception for Write Operations
            if is_write:
                # Authorization check
                auth_ok, auth_msg = self.safety_gateway.evaluate_authorization(tool_def, self.user_role)
                if not auth_ok:
                    perm_denied = (
                        f"**Answer**: Write operation '{tool_def.name}' was blocked by Safety Gateway.\n\n"
                        f"**Evidence**: User role '{self.user_role}' does not meet required role '{tool_def.metadata.required_role}'.\n\n"
                        f"**Status**: PERMISSION_DENIED\n\n"
                        f"**Next Step**: Re-run query with an authorized Manager or Admin account."
                    )
                    return self._append_response_footer(perm_denied, time.time() - query_start_time)

                # Check Confirmation Requirement
                pending_action = self.safety_gateway.create_pending_action(
                    tool=tool_def,
                    args=action_obj.args,
                    user_id=self.user_id,
                    user_role=self.user_role,
                )

                if self.auto_confirm_writes:
                    exec_res = await self.execute_confirmed_action(
                        action_id=pending_action.action_id,
                        approved=True,
                        user_id=self.user_id,
                        user_role=self.user_role,
                        session_id=session_id,
                    )
                    transcript.append({
                        "iteration": iteration,
                        "action": resolved_tool_name,
                        "args": pending_action.params,
                        "observation": exec_res,
                    })
                    called_actions.add(resolved_tool_name)
                    continue

                card = self.safety_gateway.format_confirmation_card(pending_action)

                # Interactive CLI Confirmation
                if self.interactive_confirm_fn:
                    approved = self.interactive_confirm_fn(card)
                    if approved:
                        exec_res = await self.execute_confirmed_action(
                            action_id=pending_action.action_id,
                            approved=True,
                            user_id=self.user_id,
                            user_role=self.user_role,
                            session_id=session_id,
                        )
                        transcript.append({
                            "iteration": iteration,
                            "action": resolved_tool_name,
                            "args": pending_action.params,
                            "observation": exec_res,
                        })
                        called_actions.add(resolved_tool_name)
                        continue
                    else:
                        await self.execute_confirmed_action(
                            action_id=pending_action.action_id,
                            approved=False,
                            user_id=self.user_id,
                            user_role=self.user_role,
                            session_id=session_id,
                        )
                        cancel_msg = (
                            f"**Answer**: Operation '{tool_def.name}' was cancelled by user.\n\n"
                            f"**Status**: CANCELLED_BY_USER\n\n"
                            f"**Next Step**: No records were modified."
                        )
                        return self._append_response_footer(cancel_msg, time.time() - query_start_time)

                # Return confirmation structure to Web UI
                return {
                    "status": "PENDING_CONFIRMATION",
                    "action_id": pending_action.action_id,
                    "card": card,
                    "action_type": pending_action.action_type,
                    "params": pending_action.params,
                }

            # 5. Read Tool or Auto-Confirmed Write Tool Execution
            if on_progress:
                on_progress(f"Executing tool '{resolved_tool_name}'...")

            tool_obs = await self.router.execute_tool(resolved_tool_name, action_obj.args)

            # Check character limit to prevent context explosion
            obs_str = json.dumps(tool_obs, default=str)
            if len(obs_str) > OBSERVATION_CHAR_LIMIT:
                logger.debug(f"Truncating tool observation from {len(obs_str)} chars to {OBSERVATION_CHAR_LIMIT}")
                if isinstance(tool_obs, dict):
                    truncated_dict = dict(tool_obs)
                    if "items" in truncated_dict and isinstance(truncated_dict["items"], list) and len(truncated_dict["items"]) > 10:
                        orig_len = len(truncated_dict["items"])
                        truncated_dict["items"] = truncated_dict["items"][:10]
                        truncated_dict["total_count"] = orig_len
                        truncated_dict["returned_count"] = 10
                        truncated_dict["is_complete"] = False
                        truncated_dict["_notice"] = f"Showing 10 of {orig_len} items (truncated for context)"
                        tool_obs = truncated_dict
                    elif "records" in truncated_dict and isinstance(truncated_dict["records"], list) and len(truncated_dict["records"]) > 10:
                        orig_len = len(truncated_dict["records"])
                        truncated_dict["records"] = truncated_dict["records"][:10]
                        truncated_dict["total_count"] = orig_len
                        truncated_dict["returned_count"] = 10
                        truncated_dict["is_complete"] = False
                        truncated_dict["_notice"] = f"Showing 10 of {orig_len} records (truncated for context)"
                        tool_obs = truncated_dict
                    else:
                        tool_obs = {"data": obs_str[:OBSERVATION_CHAR_LIMIT], "_truncated": True, "is_complete": False}
                else:
                    tool_obs = {"data": str(tool_obs)[:OBSERVATION_CHAR_LIMIT], "_truncated": True, "is_complete": False}

            transcript.append({
                "iteration": iteration,
                "action": resolved_tool_name,
                "args": action_obj.args,
                "observation": tool_obs,
            })
            called_actions.add(resolved_tool_name)

        # Max iterations reached without final answer: generate business response from transcript
        obs_map = {step["action"]: step.get("observation", {}) for step in transcript if step.get("observation")}
        summary_ans = ResponseFormatter.format_business_response(user_query, obs_map, entity_res, DEVELOPER_MODE)
        return self._append_response_footer(summary_ans, time.time() - query_start_time)

    async def execute_confirmed_action(
        self,
        action_id: str,
        approved: bool = True,
        user_id: Optional[str] = None,
        user_role: Optional[str] = None,
        session_id: str = "SESSION-UNKNOWN",
    ) -> Dict[str, Any]:
        """
        Executes or cancels a pending action stored in SafetyGateway.
        """
        u_id = user_id or self.user_id
        u_role = user_role or self.user_role

        if not approved:
            return self.safety_gateway.cancel_pending_action(
                action_id=action_id,
                user_id=u_id,
                user_role=u_role,
                session_id=session_id,
            )

        async def execute_tool_cb(pa):
            resolved_name = self.router.registry.resolve_tool_name(pa.tool_name) or pa.tool_name
            return await self.router.execute_tool(resolved_name, pa.params)

        return await self.safety_gateway.execute_confirmed_action_async(
            action_id=action_id,
            user_id=u_id,
            user_role=u_role,
            execute_fn=execute_tool_cb,
            session_id=session_id,
        )

    async def confirm_action(self, action_id: str, approved: bool = True) -> Dict[str, Any]:
        """Convenience alias for execute_confirmed_action."""
        return await self.execute_confirmed_action(action_id, approved=approved)

    async def close(self):
        await self.router.close()
        if hasattr(self.llm, "close"):
            await self.llm.close()
