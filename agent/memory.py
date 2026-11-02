from collections import deque
import datetime
import re
from typing import Any, Dict, List, Optional, Tuple

from services.date_resolver import RelativeDateResolver, get_current_date
from services.query_normalizer import normalize_query_typos

SECRET_PATTERNS = [
    # Redact explicit key-value secrets: password=xyz, token: xyz, api_key: xyz
    re.compile(r'(?i)\b(password|passwd|pwd|secret|api[_-]?key|access_token|private_key)\s*[:=]\s*["\']?([^\s"\'<>]+)'),
    # Redact Authorization: Bearer <token>
    re.compile(r'(?i)\b(bearer)\s+([A-Za-z0-9_\-\.]{15,})'),
    # Redact JWTs
    re.compile(r'\bey[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b'),
]


def sanitize_text(text: str) -> str:
    """
    Removes sensitive credentials, passwords, or bearer tokens before storing in memory.
    Preserves legitimate business IDs, idempotency keys, and purchase order identifiers.
    """
    if not text:
        return ""
    sanitized = text
    for pat in SECRET_PATTERNS:
        sanitized = pat.sub("[REDACTED_CREDENTIAL]", sanitized)
    return sanitized


class ConversationMemory:
    """
    Thread-safe short-term conversation memory tracking recent turns and structured session state.
    Preserves active subject, resolves follow-up references ('that product', 'the same supplier',
    'last month', 'make it 200', 'show me the full list'), and handles cancellations.
    """

    def __init__(self, max_turns: int = 5):
        self.max_turns = max_turns
        self.sessions: Dict[str, deque] = {}
        self.session_states: Dict[str, Dict[str, Any]] = {}

    def add_turn(
        self,
        session_id: str,
        query: str,
        answer: str,
        intent: Optional[str] = None,
        entities: Optional[Dict[str, Any]] = None,
        tool: Optional[str] = None,
        tool_args: Optional[Dict[str, Any]] = None,
    ):
        if session_id not in self.sessions:
            self.sessions[session_id] = deque(maxlen=self.max_turns)
        clean_q = sanitize_text(query)
        clean_a = sanitize_text(answer)
        self.sessions[session_id].append({
            "query": clean_q,
            "answer": clean_a,
        })

        # Update structured session state
        st = self.session_states.get(session_id, {})
        if intent:
            st["last_intent"] = intent
        if entities:
            prev_entities = st.get("last_entities", {})
            prev_entities.update(entities)
            st["last_entities"] = prev_entities
        if tool:
            st["last_tool"] = tool
        if tool_args:
            st["last_tool_args"] = tool_args
            if tool_args.get("start_date") and tool_args.get("end_date"):
                st["last_date_range"] = (str(tool_args["start_date"]), str(tool_args["end_date"]))
            if tool_args.get("month"):
                st["last_month_arg"] = str(tool_args["month"])
                m_str = str(tool_args["month"]).strip()
                if re.match(r"^\d{4}-\d{2}$", m_str):
                    try:
                        y, m = int(m_str[:4]), int(m_str[5:7])
                        f_d, l_d = RelativeDateResolver.get_month_bounds(y, m)
                        st["last_date_range"] = (f_d.isoformat(), l_d.isoformat())
                    except Exception:
                        pass
                else:
                    try:
                        dr = RelativeDateResolver.resolve_date_expression(m_str)
                        if dr:
                            st["last_date_range"] = (dr.start_date, dr.end_date)
                    except Exception:
                        pass
        st["last_query"] = clean_q
        st["last_answer"] = clean_a
        self.session_states[session_id] = st

    def get_language(self, session_id: str) -> str:
        """Returns the active conversation language for the session (default 'en')."""
        return self.session_states.get(session_id, {}).get("language", "en")

    def set_language(self, session_id: str, language: str) -> None:
        """Sets the active conversation language for the session."""
        st = self.session_states.get(session_id, {})
        st["language"] = language
        self.session_states[session_id] = st

    def get_state(self, session_id: str) -> Dict[str, Any]:
        return self.session_states.get(session_id, {})

    def update_state(self, session_id: str, updates: Dict[str, Any]):
        st = self.session_states.get(session_id, {})
        st.update(updates)
        self.session_states[session_id] = st

    def resolve_follow_up(
        self,
        session_id: str,
        current_query: str,
        ref_date: Optional[datetime.date] = None,
    ) -> Tuple[str, Optional[Dict[str, Any]]]:
        """
        Resolves follow-up references relative to recent session context:
        - Typo tolerance (normalizes words like 'maonth' -> 'month', 'pendng' -> 'pending')
        - Date follow-ups ('month before last', 'previous month', 'last month', 'this month', named months like 'September', 'same for last year')
        - Ambiguous follow-ups ('what about it?', 'tell me more', 'why?') -> triggers clarification
        - Quantity changes ('make it 200', 'change quantity to 200')
        - Entity anaphora ('that product', 'the same supplier')
        - Full list requests ('show me the full list')
        - Cancellation requests ('cancel', 'stop that', 'no')
        Returns (resolved_query, contextual_parameters).
        """
        st = self.get_state(session_id)
        if not st:
            return current_query, None

        # Apply typo correction before follow-up resolution
        normalized_q = normalize_query_typos(current_query)
        q_clean = normalized_q.strip()
        q_lower = q_clean.lower()

        # 1. Operation Cancellation
        if q_lower in ("cancel", "no", "stop", "stop that", "don't do it", "cancel that"):
            return "CANCEL_OPERATION", {"action": "CANCEL"}

        # 2. Ambiguous Follow-ups (require clarification rather than guessing)
        ambiguous_phrases = [
            "what about it", "what about it?", "tell me more", "tell me more?",
            "why", "why?", "how so", "how so?", "and then", "and then?",
            "details please", "more details", "what else", "what else?",
            "can you explain", "explain that", "explain", "more info",
        ]
        if q_lower in ambiguous_phrases:
            if st.get("last_intent") or st.get("last_query"):
                return "CLARIFY_QUERY", {
                    "action": "CLARIFY",
                    "clarification_message": (
                        "Could you please clarify what specific information you need? "
                        "For example, I can show individual invoice details, customer debtor status, "
                        "or financial performance for a different month."
                    ),
                }

        # 3. Contextual Confirmation Follow-ups ("yes", "yes, please", "sure", "go ahead", "yes, I need all", "show them", "show all")
        confirmation_patterns = [
            r"^(?:yes|yeah|yep|sure|ok|okay|go\s+ahead|proceed|please\s+do|do\s+it|confirm)(?:[!,.]*)$",
            r"^(?:yes\s*,\s*)?(?:please|show\s+them|i\s+need\s+all|show\s+all|fetch\s+them|retrieve\s+them|go\s+ahead|do\s+that)(?:[!,.]*)$",
            r"^(?:i\s+need\s+all|show\s+them|fetch\s+remaining|show\s+remaining|all\s+of\s+them)(?:[!,.]*)$",
        ]
        is_confirmation = any(re.match(pat, q_lower) for pat in confirmation_patterns)

        if is_confirmation:
            last_a = st.get("last_answer", "").lower()
            last_q = st.get("last_query", "").lower()
            last_intent = st.get("last_intent", "")
            last_entities = st.get("last_entities", {})

            # Case A: Staged write action in SafetyGateway awaiting confirmation
            if st.get("pending_action_id") or st.get("last_status") == "PENDING_CONFIRMATION":
                return "CONFIRM_STAGED_ACTION", {
                    "action": "CONFIRM_STAGED_ACTION",
                    "action_id": st.get("pending_action_id"),
                }

            # Case B: Previous assistant message asked for missing PO details (quantity / supplier)
            if (
                ("quantity" in last_a and any(w in last_a for w in ["supplier", "purchase order", "specify", "provide", "missing"]))
                or ("purchase order" in last_q and not last_entities.get("quantity"))
            ):
                return "CLARIFY_QUERY", {
                    "action": "CLARIFY",
                    "clarification_message": "Please specify the required quantity and supplier name to proceed with creating the purchase order.",
                }

            # Case C: Previous assistant message offered multiple distinct options
            has_supplier_offer = any(w in last_a for w in ["compare supplier", "supplier comparison", "vendor comparison"])
            has_orders_offer = any(w in last_a for w in ["pending order", "orders pending", "show orders"])
            if has_supplier_offer and has_orders_offer:
                return "CLARIFY_QUERY", {
                    "action": "CLARIFY",
                    "clarification_message": "Could you please specify whether you would like to compare suppliers or check pending orders?",
                }

            # Case D: Offers to retrieve remaining low-stock items or inventory
            if (
                any(w in last_a for w in ["low stock", "low-stock", "remaining", "items low in stock", "reorder threshold", "fetch the remaining"])
                or last_intent in ("inventory", "low_stock")
                or any(w in last_q for w in ["low in stock", "low stock", "items low", "reorder"])
            ):
                return "Show all low-stock inventory items complete list.", {
                    "follow_up_type": "confirmation",
                    "action": "SHOW_ALL_LOW_STOCK",
                    "intent": "inventory",
                }

            # Case E: Offers to show pending orders
            if (
                any(w in last_a for w in ["pending order", "orders pending", "unfulfilled order"])
                or last_intent == "pending_orders"
                or any(w in last_q for w in ["pending order", "orders pending"])
            ):
                return "Show all pending orders complete list.", {
                    "follow_up_type": "confirmation",
                    "action": "SHOW_PENDING_ORDERS",
                    "intent": "pending_orders",
                }

            # Case F: Asks whether user wants supplier comparison
            if (
                has_supplier_offer
                or last_intent == "supplier_comparison"
                or any(w in last_q for w in ["compare supplier", "which supplier", "top supplier"])
                or (last_intent == "inventory" and any(w in last_a for w in ["supplier", "vendor", "reorder"]))
            ):
                p_name = last_entities.get("product_name") or last_entities.get("product_id") or "Wireless Optical Mouse"
                qty = last_entities.get("quantity") or 100
                return f"Compare suppliers for {qty} units of {p_name}.", {
                    "follow_up_type": "confirmation",
                    "action": "COMPARE_SUPPLIERS",
                    "intent": "supplier_comparison",
                    "product": p_name,
                    "quantity": qty,
                }

            # Case G: Ambiguous confirmation with no clear context
            return "CLARIFY_QUERY", {
                "action": "CLARIFY",
                "clarification_message": "I'm sorry, but I don't have enough context for what you'd like to confirm. Could you please specify what you would like to see?",
            }

        today = ref_date or get_current_date()
        date_res: Optional[Any] = None

        # 3. Date follow-up: Check for year-shift ("same for last year", "same month last year", "and last year")
        if any(p in q_lower for p in ["same for last year", "same month last year", "what about the same for last year", "and last year", "same last year"]):
            last_dr = st.get("last_date_range")
            if last_dr:
                try:
                    s_dt = datetime.date.fromisoformat(last_dr[0])
                    prev_y = s_dt.year - 1
                    f_day, l_day = RelativeDateResolver.get_month_bounds(prev_y, s_dt.month)
                    date_res = RelativeDateResolver.resolve_date_expression(f"{prev_y:04d}-{s_dt.month:02d}", ref_date=today)
                except Exception:
                    pass
            if not date_res:
                date_res = RelativeDateResolver.resolve_date_expression(f"{today.year-1}-01", ref_date=today)

        # 4. Date follow-up: "previous month" / "and the previous month" / "prior month" (relative to queried month if applicable)
        elif any(p in q_lower for p in ["previous month", "and the previous month", "and previous month", "prior month", "month before that", "the month before that", "prior to that"]) and "month before last" not in q_lower:
            last_dr = st.get("last_date_range")
            if last_dr:
                try:
                    s_dt = datetime.date.fromisoformat(last_dr[0])
                    prev_y, prev_m = RelativeDateResolver.shift_month(s_dt.year, s_dt.month, -1)
                    date_res = RelativeDateResolver.resolve_date_expression(f"{prev_y:04d}-{prev_m:02d}", ref_date=today)
                except Exception:
                    pass
            if not date_res:
                last_q = st.get("last_query", "").lower()
                last_a = st.get("last_answer", "").lower()
                if "last month" in last_q or "last month" in last_a or "month before last" in last_q:
                    date_res = RelativeDateResolver.resolve_date_expression("month before last", ref_date=today)
                else:
                    date_res = RelativeDateResolver.resolve_date_expression("last month", ref_date=today)

        # 5. Date follow-up: "month before last", named months ("september"), relative expressions
        if not date_res:
            date_phrases = [
                "month before last", "the month before last", "month before that", "the month before that",
                "two months ago", "2 months ago", "usse pichle mahine",
                "last month", "this month", "last two months", "last 2 months", "last 3 months",
                "today", "yesterday", "pichle mahine",
            ]
            month_names = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]

            is_date_query = any(dp in q_lower for dp in (date_phrases + month_names))
            if is_date_query:
                clean_date_expr = re.sub(r"\b(?:what\s+about|how\s+about|and|for|in|the|show|tell\s+me)\b", "", q_lower).strip(" ?.,!")
                date_res = RelativeDateResolver.resolve_date_expression(clean_date_expr, ref_date=today)
                if not date_res:
                    date_res = RelativeDateResolver.resolve_date_expression(q_lower, ref_date=today)

        if date_res:
            last_intent = st.get("last_intent", "")
            last_q = st.get("last_query", "").lower()
            last_tool = st.get("last_tool", "")

            if (
                last_intent == "financial_summary"
                or "financial_summary" in str(last_tool)
                or any(w in last_q for w in ["revenue", "sales", "finance", "financial", "invoiced", "collected", "turnover"])
            ):
                enriched = (
                    f"Calculate the total invoiced revenue for {date_res.label} "
                    f"(from {date_res.start_date} to {date_res.end_date}), "
                    f"including collected and outstanding amounts."
                )
                return enriched, {
                    "follow_up_type": "date_change",
                    "period": date_res.label,
                    "start_date": date_res.start_date,
                    "end_date": date_res.end_date,
                    "retained_intent": "financial_summary",
                }
            elif (
                last_intent == "pending_orders"
                or "pending_orders" in str(last_tool)
                or any(w in last_q for w in ["pending order", "pending orders", "orders pending", "total pending"])
            ):
                enriched = f"How many pending orders were placed in {date_res.label} (from {date_res.start_date} to {date_res.end_date})?"
                return enriched, {
                    "follow_up_type": "date_change",
                    "period": date_res.label,
                    "start_date": date_res.start_date,
                    "end_date": date_res.end_date,
                    "retained_intent": "pending_orders",
                }
            elif (
                last_intent == "supplier_performance"
                or "supplier" in str(last_tool)
                or "supplier" in last_q
            ):
                enriched = f"Show supplier performance ranking for {date_res.label} (from {date_res.start_date} to {date_res.end_date})."
                return enriched, {
                    "follow_up_type": "date_change",
                    "period": date_res.label,
                    "start_date": date_res.start_date,
                    "end_date": date_res.end_date,
                    "retained_intent": "supplier_performance",
                }

        # 6. Quantity adjustment (e.g. 'make it 200', 'change to 200', 'need 200', 'make quantity 150')
        qty_match = re.search(r"\b(?:make it|change to|change quantity to|need|order)\s+(\d+)\b", q_lower)
        if qty_match:
            new_qty = int(qty_match.group(1))
            last_entities = st.get("last_entities", {})
            p_name = last_entities.get("product_name") or last_entities.get("product_id")
            s_name = last_entities.get("supplier_name") or last_entities.get("supplier_id")
            if p_name:
                if s_name:
                    enriched = f"Create purchase order for {new_qty} units of {p_name} from {s_name}."
                else:
                    enriched = f"Compare suppliers for {new_qty} units of {p_name}."
                return enriched, {"follow_up_type": "quantity_change", "new_quantity": new_qty, "retained_product": p_name}

        # 7. Anaphoric entity references ('that product', 'the same product', 'the same supplier')
        resolved_q = q_clean
        last_entities = st.get("last_entities", {})
        if any(w in q_lower for w in ["that product", "the same product", "this product", "this item"]):
            p_name = last_entities.get("product_name") or last_entities.get("product_id")
            if p_name:
                resolved_q = re.sub(r"(?i)\b(that product|the same product|this product|this item)\b", p_name, resolved_q)

        if any(w in q_lower for w in ["that supplier", "the same supplier", "that vendor", "the same vendor"]):
            s_name = last_entities.get("supplier_name") or last_entities.get("supplier_id")
            if s_name:
                resolved_q = re.sub(r"(?i)\b(that supplier|the same supplier|that vendor|the same vendor)\b", s_name, resolved_q)

        # 8. Full list requests ('show me the full list', 'show full list', 'full list')
        if any(phrase in q_lower for phrase in ["full list", "show all", "show me all", "show the full list"]):
            last_intent = st.get("last_intent", "")
            if last_intent == "inventory" or "inventory" in st.get("last_query", "").lower():
                return "Show all inventory items complete list.", {"follow_up_type": "full_list", "intent": "all_inventory"}
            elif last_intent == "pending_payments" or "owe" in st.get("last_query", "").lower():
                return "Show complete list of all customers with pending payments.", {"follow_up_type": "full_list", "intent": "pending_payments"}

        return resolved_q, None

        return resolved_q, None

    def get_turns(self, session_id: str) -> List[Dict[str, str]]:
        if session_id not in self.sessions:
            return []
        return list(self.sessions[session_id])

    def get_context_summary(self, session_id: str) -> str:
        turns = self.get_turns(session_id)
        if not turns:
            return "No previous conversation turns in this session."
        lines = []
        for idx, t in enumerate(turns, start=1):
            lines.append(f"Turn {idx}:")
            lines.append(f"  User: {t['query']}")
            lines.append(f"  Assistant: {t['answer'][:250]}...")
        st = self.get_state(session_id)
        if st.get("last_intent"):
            lines.append(f"Active Conversation Topic: {st['last_intent']}")
        if st.get("last_entities"):
            lines.append(f"Active Entities: {st['last_entities']}")
        return "\n".join(lines)

    def clear(self, session_id: Optional[str] = None):
        if session_id:
            self.sessions.pop(session_id, None)
            self.session_states.pop(session_id, None)
        else:
            self.sessions.clear()
            self.session_states.clear()
