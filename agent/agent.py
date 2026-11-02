import asyncio
import json
import logging
import os
import re
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Set, Union

from app.config import (
    DEFAULT_USER_ID,
    DEFAULT_USER_ROLE,
    DEVELOPER_MODE,
    MAX_REACT_ITERATIONS,
    MULTI_AGENT,
    OBSERVATION_CHAR_LIMIT,
    QUERY_TIMEOUT_SECONDS,
)
from database.models import DiscoveredTool
from domain.entity_resolver import EntityResolver
from llm.base import LLMClient
from llm.groq_client import GroqClient
from mcp.router import MCPRouterManager
from security.redaction import SecuritySanitizer
from security.safety_gateway import SafetyGateway
from services.language_policy import LanguagePolicy
from services.query_normalizer import normalize_query_typos
from agent.domain_guard import DomainGuard, OUT_OF_DOMAIN_REJECTION
from agent.memory import ConversationMemory
from agent.react_loop import DeterministicFallbackEngine, ExecutionResult, ReActExecutionEngine
from agent.reflection import ReflectionEngine
from agent.response_formatter import ResponseFormatter
from agent.tool_selector import ToolSelector
from agent.tool_validator import GroundingValidator

logger = logging.getLogger(__name__)


class PlannerAgent:
    """Decomposes compound or multi-intent business queries into discrete subtasks."""

    @staticmethod
    def plan_subtasks(user_query: str) -> List[str]:
        q = user_query.strip()
        if " and " in q.lower() and any(w in q.lower() for w in ["check", "compare", "update", "verify", "what", "find"]):
            parts = [p.strip() for p in q.split(" and ") if len(p.strip()) > 5]
            if len(parts) > 1:
                return parts
        return [q]


class AggregatorAgent:
    """Synthesizes multiple subtask results into an integrated business briefing."""

    @staticmethod
    def aggregate_results(user_query: str, subtasks: List[str], results: List[str]) -> str:
        if len(results) == 1:
            return results[0]
        sections = []
        for i, (sub, res) in enumerate(zip(subtasks, results), start=1):
            sections.append(f"### Subtask {i}: {sub}\n{res}")
        return "\n\n---\n\n".join(sections)


class BizOpsAgent:
    """
    Central BizOps Agent Coordinator and Primary Entry Point.
    Coordinates Domain Boundary validation, Conversation Memory, Tool Selection,
    ReAct execution, Grounding Validation, Self-Reflection, and Safety Gateway
    approval workflows.
    """

    def __init__(
        self,
        user_id: str = DEFAULT_USER_ID,
        user_role: str = DEFAULT_USER_ROLE,
        auto_confirm_writes: bool = False,
        interactive_confirm_fn: Optional[Callable[[str], bool]] = None,
        verbose: bool = False,
        router: Optional[MCPRouterManager] = None,
        safety_gateway: Optional[SafetyGateway] = None,
        memory: Optional[ConversationMemory] = None,
        llm: Optional[LLMClient] = None,
        ref_date: Optional[Any] = None,
    ):
        self.user_id = user_id
        self.user_role = user_role
        self.auto_confirm_writes = auto_confirm_writes
        self.interactive_confirm_fn = interactive_confirm_fn
        self.verbose = verbose
        self.ref_date = ref_date

        self.router = router or MCPRouterManager()
        self.safety_gateway = safety_gateway or SafetyGateway()
        self.memory = memory or ConversationMemory()
        self.llm = llm or GroqClient()

        self.query_timeout_seconds = QUERY_TIMEOUT_SECONDS
        self.last_transcript: List[Dict[str, Any]] = []
        self._last_guard_badge: Optional[str] = None
        self._last_entity_res: Optional[Any] = None
        self.last_llm_display_label: str = f"LLM: groq/{self.llm.model_name}" if hasattr(self.llm, "model_name") else "LLM: Groq"

        # Dedicated ReAct Execution Engine
        self.react_engine = ReActExecutionEngine(
            router=self.router,
            llm=self.llm,
            safety_gateway=self.safety_gateway,
            user_id=self.user_id,
            user_role=self.user_role,
            auto_confirm_writes=self.auto_confirm_writes,
            interactive_confirm_fn=self.interactive_confirm_fn,
            query_timeout_seconds=self.query_timeout_seconds,
            observation_char_limit=OBSERVATION_CHAR_LIMIT,
            max_iterations=MAX_REACT_ITERATIONS,
        )

    @property
    def ollama(self) -> LLMClient:
        return self.llm

    @ollama.setter
    def ollama(self, client: LLMClient) -> None:
        self.llm = client
        self.react_engine.llm = client

    async def initialize(self):
        """Initializes MCP server connections and discovers tools."""
        await self.router.initialize_connections()

    async def close(self):
        """Closes all MCP subprocesses and LLM sessions."""
        await self.router.close()
        if hasattr(self.llm, "close"):
            await self.llm.close()

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
        lbl = getattr(self, "last_llm_display_label", None)
        if lbl:
            return lbl
        if hasattr(self.llm, "model_name"):
            return f"LLM: {getattr(self.llm, 'provider_name', 'groq')}/{self.llm.model_name}"
        return "LLM: rule-based fallback"

    def _append_response_footer(self, answer: str, elapsed_seconds: Optional[float] = None) -> str:
        """Appends provenance badge to the business response."""
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

    def _plan_subtasks(self, query: str) -> List[str]:
        return PlannerAgent.plan_subtasks(query)

    def _aggregate_results(self, user_query: str, subtasks: List[str], results: List[str]) -> str:
        return AggregatorAgent.aggregate_results(user_query, subtasks, results)

    async def run_query(
        self,
        user_query: str,
        session_id: Optional[str] = None,
        on_progress: Optional[Callable[[str], None]] = None,
        ref_date: Optional[Any] = None,
    ) -> Union[str, Dict[str, Any]]:
        """
        Primary entry point for user operational queries.
        Coordinates Domain Guard, Memory Context, Tool Selection, ReAct Loop,
        Reflection, Grounding, and Safety Gateway.
        """
        active_session = session_id or f"SESS-{uuid.uuid4().hex[:8].upper()}"
        active_ref_date = ref_date or self.ref_date

        self.user_query = user_query
        self._last_guard_badge = None

        # Multi-turn language policy & query normalization
        current_lang = self.memory.get_language(active_session)
        detected_lang = LanguagePolicy.detect_language(user_query, current_session_lang=current_lang)
        self.memory.set_language(active_session, detected_lang)

        # Typo tolerance: Normalize query before follow-up resolution
        clean_user_query = normalize_query_typos(user_query)

        # 1. Resolve conversational follow-up references using session context
        resolved_query, context_params = self.memory.resolve_follow_up(
            active_session, clean_user_query, ref_date=active_ref_date
        )
        if resolved_query == "CANCEL_OPERATION":
            cancel_msg = (
                "**Answer**: The pending operation has been cancelled as requested.\n\n"
                "**Status**: CANCELLED_BY_USER\n\n"
                "**Next Step**: No records or transactions were modified."
            )
            self.memory.add_turn(active_session, user_query, cancel_msg, intent="cancel")
            return self._append_response_footer(cancel_msg, 0.05)

        if resolved_query == "CLARIFY_QUERY" or (context_params and context_params.get("action") == "CLARIFY"):
            clarify_msg = context_params.get("clarification_message") or (
                "Could you please clarify what specific information you need? "
                "For example, you can specify a date range, a product name, or a customer name."
            )
            self.memory.add_turn(active_session, user_query, clarify_msg, intent="clarification")
            return self._append_response_footer(clarify_msg, 0.02)

        if resolved_query == "CONFIRM_STAGED_ACTION" or (context_params and context_params.get("action") == "CONFIRM_STAGED_ACTION"):
            pa_id = context_params.get("action_id")
            if pa_id:
                exec_res = await self.execute_confirmed_action(
                    action_id=pa_id,
                    approved=True,
                    user_id=self.user_id,
                    user_role=self.user_role,
                    session_id=active_session,
                )
                self.memory.update_state(active_session, {"pending_action_id": None, "last_status": None})
                if isinstance(exec_res, dict):
                    status_str = exec_res.get("status", "")
                    if status_str == "SUCCESS":
                        ans = f"**Status**: SUCCESS\n\nAction '{pa_id}' has been approved and executed successfully."
                    elif status_str == "PERMISSION_DENIED":
                        ans = f"**Status**: PERMISSION_DENIED\n\nUser role '{self.user_role}' is not authorized to approve action '{pa_id}'."
                    else:
                        ans = f"**Status**: {status_str}\n\n{exec_res.get('message', '')}"
                else:
                    ans = str(exec_res)
                self.memory.add_turn(active_session, user_query, ans, intent="execute_confirmed")
                return self._append_response_footer(ans)

        target_query = resolved_query

        # Multi-Agent Planner check for composite queries
        if MULTI_AGENT:
            subtasks = self._plan_subtasks(target_query)
            if len(subtasks) > 1:
                if on_progress:
                    on_progress(f"Decomposing query into {len(subtasks)} operational tasks...")
                results = []
                for subtask in subtasks:
                    sub_res = await self._run_single_query(subtask, active_session, on_progress)
                    if isinstance(sub_res, dict) and sub_res.get("status") == "PENDING_CONFIRMATION":
                        return sub_res
                    clean_res = re.sub(r"\n\n---\n\*\[.*?\]\*", "", str(sub_res)).strip()
                    results.append(clean_res)
                aggregated = self._aggregate_results(target_query, subtasks, results)
                self.memory.add_turn(active_session, user_query, aggregated)
                return self._append_response_footer(aggregated)

        res = await self._run_single_query(target_query, active_session, on_progress)
        if isinstance(res, dict) and res.get("status") == "PENDING_CONFIRMATION":
            self.memory.add_turn(
                session_id=active_session,
                query=user_query,
                answer=f"Staged write action {res.get('action_id')} requires confirmation.",
                intent="pending_confirmation",
            )
            self.memory.update_state(active_session, {
                "pending_action_id": res.get("action_id"),
                "pending_action_type": res.get("action_type"),
                "last_status": "PENDING_CONFIRMATION",
            })
            return res

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
                for step in reversed(self.last_transcript):
                    if step.get("action") and step.get("action") != "final":
                        last_tool = step.get("action")
                        last_tool_args = dict(step.get("args") or {})
                        obs = step.get("observation")
                        if isinstance(obs, dict):
                            if obs.get("start_date") and obs.get("end_date"):
                                last_tool_args["start_date"] = obs["start_date"]
                                last_tool_args["end_date"] = obs["end_date"]
                        break

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

        # 1. Strict Deterministic Business Domain Guard
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
            inner_res = await self._run_single_query(guard_decision.in_domain_query, session_id, on_progress)
            if isinstance(inner_res, str):
                clean_inner = re.sub(r"\n\n---\n\*\[.*?\]\*", "", inner_res).strip()
                ood_desc = guard_decision.out_of_domain_part or "general questions"
                mixed_answer = (
                    f"I cannot assist with general knowledge or arithmetic like '{ood_desc}'. "
                    f"However, regarding your business operations:\n\n{clean_inner}"
                )
                return self._append_response_footer(mixed_answer, time.time() - query_start_time)
            return inner_res

        # 2. Dynamic Tool Selection
        relevant_tools = ToolSelector.select_relevant_tools(user_query, self.router.registry)

        # 3. ReAct Execution Engine Dispatch
        exec_res: ExecutionResult = await self.react_engine.execute(
            user_query=user_query,
            session_id=session_id,
            memory=self.memory,
            entity_res=entity_res,
            relevant_tools=relevant_tools,
            execute_confirmed_cb=self.execute_confirmed_action,
            on_progress=on_progress,
            query_start_time=query_start_time,
        )

        self.last_transcript = exec_res.transcript
        self.last_llm_display_label = exec_res.llm_display_label

        if exec_res.is_pending_confirmation:
            return exec_res.pending_action_data or {}

        final_answer = exec_res.final_answer

        # 4. Post-Execution Reflection & Grounding Validation
        obs_map = {step["action"]: step.get("observation", {}) for step in exec_res.transcript if step.get("observation")}
        self.last_grounding = GroundingValidator.validate_answer_grounding(final_answer, obs_map)
        self.last_reflection = ReflectionEngine.evaluate_task(
            user_query=user_query,
            transcript=exec_res.transcript,
            proposed_answer=final_answer,
            llm_online=not exec_res.used_fallback,
        )

        return self._append_response_footer(final_answer, exec_res.elapsed_seconds)

    async def execute_confirmed_action(
        self,
        action_id: str,
        approved: bool = True,
        user_id: Optional[str] = None,
        user_role: Optional[str] = None,
        session_id: str = "SESSION-UNKNOWN",
    ) -> Dict[str, Any]:
        """Executes or cancels a pending action stored in SafetyGateway."""
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
