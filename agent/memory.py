from collections import deque
import re
from typing import Any, Dict, List, Optional, Tuple

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
        st["last_query"] = clean_q
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

    def resolve_follow_up(self, session_id: str, current_query: str) -> Tuple[str, Optional[Dict[str, Any]]]:
        """
        Resolves follow-up references relative to recent session context:
        - Typo tolerance (normalizes words like 'maonth' -> 'month', 'pendng' -> 'pending')
        - Date follow-ups ('last month', 'and last month', 'this month', 'last two months')
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

        # 2. Date follow-up (e.g. 'last month', 'and last month', 'what about last month?', 'last 2 months')
        date_phrases = ["last month", "this month", "last two months", "last 2 months", "last 3 months", "yesterday", "pichle mahine"]
        is_isolated_date = any(
            q_lower == dp
            or q_lower == f"and {dp}"
            or q_lower == f"what about {dp}"
            or q_lower == f"how about {dp}"
            or q_lower == f"for {dp}"
            or q_lower == f"{dp}?"
            for dp in date_phrases
        )
        if is_isolated_date:
            last_intent = st.get("last_intent", "")
            last_q = st.get("last_query", "").lower()
            last_tool = st.get("last_tool", "")
            target_dp = next((dp for dp in date_phrases if dp in q_lower), "last month")

            if (
                last_intent == "pending_orders"
                or "pending_orders" in str(last_tool)
                or any(w in last_q for w in ["pending order", "pending orders", "orders pending", "total pending"])
            ):
                enriched = f"How many pending orders were placed in {target_dp}?"
                return enriched, {"follow_up_type": "date_change", "period": target_dp, "retained_intent": "pending_orders"}
            elif last_intent == "financial_summary" or any(w in last_q for w in ["revenue", "sales", "finance", "financial", "invoiced", "collected"]):
                enriched = f"What was our revenue and financial summary for {target_dp}?"
                return enriched, {"follow_up_type": "date_change", "period": target_dp, "retained_intent": "financial_summary"}
            elif last_intent == "supplier_performance" or "supplier" in last_q:
                enriched = f"Show supplier performance ranking for {target_dp}."
                return enriched, {"follow_up_type": "date_change", "period": target_dp, "retained_intent": "supplier_performance"}

        # 3. Quantity adjustment (e.g. 'make it 200', 'change to 200', 'need 200', 'make quantity 150')
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

        # 4. Anaphoric entity references ('that product', 'the same product', 'the same supplier')
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

        # 5. Full list requests ('show me the full list', 'show full list', 'full list')
        if any(phrase in q_lower for phrase in ["full list", "show all", "show me all", "show the full list"]):
            last_intent = st.get("last_intent", "")
            if last_intent == "inventory" or "inventory" in st.get("last_query", "").lower():
                return "Show all inventory items complete list.", {"follow_up_type": "full_list", "intent": "all_inventory"}
            elif last_intent == "pending_payments" or "owe" in st.get("last_query", "").lower():
                return "Show complete list of all customers with pending payments.", {"follow_up_type": "full_list", "intent": "pending_payments"}

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
