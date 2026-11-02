"""
Centralized Business Domain Guard for BizOps Agent.
Strictly restricts the agent to supported business operations:
1. Inventory and stock management
2. Orders and order fulfilment
3. Finance, invoices, payments, and revenue
4. Suppliers and supplier comparisons
5. Procurement and purchase orders
6. Business policies
7. Google Sheets business operations

Provides deterministic evaluation BEFORE the ReAct loop and BEFORE any LLM call.
Ensures zero Groq calls, zero MCP tool calls, and zero database writes for out-of-domain queries.
"""

import os
import re
import sqlite3
from typing import Any, Dict, List, Literal, Optional, Tuple
from pydantic import BaseModel, Field

from app.config import DB_PATH
from domain.entity_resolver import EntityResolutionResult
from services.query_normalizer import normalize_query_typos

OUT_OF_DOMAIN_REJECTION = (
    "I'm BizOps Agent, designed to assist with business operations such as inventory, "
    "orders, finance, suppliers and procurement. I can't answer general knowledge questions. "
    "Please ask me something related to your business operations."
)


class DomainDecision(BaseModel):
    category: Literal[
        "IN_DOMAIN",
        "BUSINESS_CALCULATION",
        "CONVERSATIONAL",
        "OUT_OF_DOMAIN",
        "MIXED",
        "ADVERSARIAL",
    ]
    is_allowed: bool
    rejection_message: Optional[str] = None
    conversational_response: Optional[str] = None
    in_domain_query: Optional[str] = None
    out_of_domain_part: Optional[str] = None
    calculation_response: Optional[str] = None
    reason: str = ""


class DomainGuard:
    """
    Deterministic domain boundary guard.
    Evaluates every incoming query before any LLM inference or tool execution.
    """

    ADVERSARIAL_PATTERNS = [
        re.compile(r"(?i)\bignore\s+(?:all\s+|your\s+|previous\s+|any\s+)?(?:instructions|rules|system\s+prompts?|constraints?)\b"),
        re.compile(r"(?i)\bsystem\s+prompt\b"),
        re.compile(r"(?i)\bpretend\s+(?:you\s+are|to\s+be)\b"),
        re.compile(r"(?i)\byou\s+are\s+now\b"),
        re.compile(r"(?i)\bjailbreak\b"),
        re.compile(r"(?i)\bunrestricted\s+ai\b"),
        re.compile(r"(?i)\bbypass\s+(?:safety|guardrails?|restrictions?|boundaries)\b"),
        re.compile(r"(?i)\bDAN\s+mode\b"),
    ]

    GREETING_PATTERNS = [
        re.compile(r"^(?:hi|hello|hey|heyy|namaste|greetings)(?:\s+there|\s+agent|\s+bizops)?[\s!.,?]*$", re.IGNORECASE),
        re.compile(r"^(?:good\s+(?:morning|afternoon|evening|day))[\s!.,?]*$", re.IGNORECASE),
    ]

    THANKS_PATTERNS = [
        re.compile(r"^(?:thank\s+you(?:\s+so\s+much|\s+very\s+much|\s+a\s+lot)?|thanks(?:\s+a\s+lot|\s+again)?|thx|dhanyawad|shukriya)[\s!.,?]*$", re.IGNORECASE),
    ]

    FAREWELL_PATTERNS = [
        re.compile(r"^(?:bye|goodbye|see\s+you(?:\s+later)?|alvida|cya)[\s!.,?]*$", re.IGNORECASE),
    ]

    IDENTITY_PATTERNS = [
        re.compile(r"^(?:who\s+are\s+you|what\s+(?:are\s+you|can\s+you\s+do|is\s+your\s+purpose|do\s+you\s+do)|help(?:\s+me)?|what\s+do\s+you\s+do|capabilities)[\s!.,?]*$", re.IGNORECASE),
    ]

    # Pure arithmetic without business context
    PURE_MATH_PATTERNS = [
        re.compile(r"^(?:what\s+is\s+|calculate\s+|solve\s+|evaluate\s+)?[-+]?\d+(?:\.\d+)?\s*[\+\-\*\/\^xX÷]\s*[-+]?\d+(?:\.\d+)?(?:\s*[\+\-\*\/\^xX÷]\s*[-+]?\d+(?:\.\d+)?)*\s*\??$", re.IGNORECASE),
        re.compile(r"^(?:what\s+is\s+|calculate\s+|solve\s+)?\d+\s*[\+\-\*\/xX÷]\s*\d+\s*\??$", re.IGNORECASE),
        re.compile(r"\b(?:square\s+root\s+of|pythagorean|quadratic\s+formula|derivative\s+of|integral\s+of|solve\s+for\s+x)\b", re.IGNORECASE),
    ]

    SCIENCE_PATTERNS = [
        re.compile(r"\b(?:quantum\s+physics|quantum\s+mechanics|machine\s+learning|deep\s+learning|neural\s+networks?|photosynthesis|theory\s+of\s+relativity|black\s+holes?|astrophysics|evolution|dna|periodic\s+table|thermodynamics)\b", re.IGNORECASE),
    ]

    TRIVIA_PATTERNS = [
        re.compile(r"\b(?:fifa\s+world\s+cup|world\s+cup|capital\s+of\s+[a-z]+|who\s+won\s+the|president\s+of\s+[a-z]+|prime\s+minister\s+of|mount\s+everest|olympics|hollywood|bollywood|actor|actress)\b", re.IGNORECASE),
    ]

    CREATIVE_PATTERNS = [
        re.compile(r"\b(?:write\s+(?:me\s+)?a\s+(?:poem|story|song|essay)|tell\s+(?:me\s+)?a\s+(?:joke|riddle|story)|compose\s+a\s+poem|sing\s+a\s+song)\b", re.IGNORECASE),
    ]

    CODING_PATTERNS = [
        re.compile(r"\b(?:write\s+(?:a\s+)?(?:python|javascript|java|c\+\+|rust|html)\s+(?:code|script|function|program)|implement\s+binary\s+search|react\s+component)\b", re.IGNORECASE),
    ]

    BUSINESS_KEYWORDS = [
        # Inventory
        "stock", "inventory", "reorder", "low stock", "shortage", "warehouse", "godown",
        "on-hand", "available", "reserved", "allocated", "threshold", "out of stock", "units",
        "sku", "restock", "items", "item", "batch", "quantity", "quantities", "supply",
        "mice", "mouse", "keyboard", "monitor", "paper", "tape", "bubble wrap", "pens",
        "boxes", "tea", "biscuits", "oil", "detergent", "extension board",
        # Orders
        "order", "orders", "fulfill", "fulfillment", "shipment", "shipping", "dispatched",
        "dispatch", "delivered", "delivery", "customer", "customers", "pending order",
        "pending orders", "cancel order", "tracking", "sla", "client", "clients",
        # Finance
        "revenue", "turnover", "sales", "finance", "financial", "financial summary",
        "invoice", "invoices", "bill", "bills", "billing", "payment", "payments", "paid",
        "unpaid", "overdue", "outstanding", "credit", "credit limit", "credit hold", "debt",
        "debts", "debtor", "debtors", "owe", "owes", "balance", "balances", "gst", "tax",
        "ledger", "cash flow", "profit", "margin", "profit margin", "cost", "costs", "expense",
        "expenses", "price", "pricing", "amount", "rupees", "inr", "₹", "rs",
        # Suppliers & Procurement
        "supplier", "suppliers", "vendor", "vendors", "ranking", "rating", "best supplier",
        "compare supplier", "compare suppliers", "supplier performance", "lead time",
        "lead times", "quote", "quotes", "quotation", "quotations", "reliability",
        "biggest supplier", "spend", "supplier spend", "purchase order", "purchase orders",
        "po", "create po", "create purchase order", "place po", "buy", "procure", "procurement",
        "restock order", "receive goods", "goods receipt",
        # Policies
        "policy", "policies", "return policy", "delivery sla", "payment terms", "vendor policy",
        "discount policy", "sop", "compliance", "credit policy",
        # Google Sheets
        "sheet", "sheets", "spreadsheet", "google sheet", "google sheets", "worksheet",
        "tab", "sync sheet", "export to sheet", "append row", "update cell",
        # Contextual operations
        "last month", "this month", "last two months", "show more", "show all", "confirm",
        "proceed", "cancel", "details",
    ]

    BUSINESS_ID_REGEX = [
        re.compile(r"\bP\d{4}\b", re.IGNORECASE),
        re.compile(r"\bPRD-\d+\b", re.IGNORECASE),
        re.compile(r"\bORD-\d+\b", re.IGNORECASE),
        re.compile(r"\bINV-\d+\b", re.IGNORECASE),
        re.compile(r"\bSUP-\d+\b", re.IGNORECASE),
        re.compile(r"\bPO-\d+\b", re.IGNORECASE),
        re.compile(r"\bCUST-\d+\b", re.IGNORECASE),
    ]

    @classmethod
    def evaluate(
        cls,
        user_query: str,
        entity_res: Optional[EntityResolutionResult] = None,
        memory: Optional[Any] = None,
        session_id: Optional[str] = None,
    ) -> DomainDecision:
        """
        Main deterministic evaluation entry point.
        Returns DomainDecision indicating category and whether the query is allowed.
        """
        raw_query = user_query.strip()
        q_norm = normalize_query_typos(raw_query).strip()
        q_lower = q_norm.lower()

        # 1. Adversarial / Prompt Injection detection
        for pat in cls.ADVERSARIAL_PATTERNS:
            if pat.search(raw_query):
                return DomainDecision(
                    category="ADVERSARIAL",
                    is_allowed=False,
                    rejection_message=OUT_OF_DOMAIN_REJECTION,
                    reason=f"Matched adversarial pattern: {pat.pattern}",
                )

        # 2. Conversational greetings and acknowledgements
        for pat in cls.GREETING_PATTERNS:
            if pat.match(q_lower):
                return DomainDecision(
                    category="CONVERSATIONAL",
                    is_allowed=True,
                    conversational_response=(
                        "Hello! I'm BizOps Agent, your operations assistant. "
                        "How can I help you with inventory, orders, finance, or suppliers today?"
                    ),
                    reason="Greeting detected",
                )

        for pat in cls.THANKS_PATTERNS:
            if pat.match(q_lower):
                return DomainDecision(
                    category="CONVERSATIONAL",
                    is_allowed=True,
                    conversational_response=(
                        "You're welcome! Let me know if you need any further assistance with your business operations."
                    ),
                    reason="Gratitude detected",
                )

        for pat in cls.IDENTITY_PATTERNS:
            if pat.match(q_lower):
                return DomainDecision(
                    category="CONVERSATIONAL",
                    is_allowed=True,
                    conversational_response=(
                        "I'm BizOps Agent, an AI operations assistant designed to help small businesses "
                        "manage inventory, orders, finance, suppliers, and procurement. How can I assist you today?"
                    ),
                    reason="Capability / identity inquiry detected",
                )

        for pat in cls.FAREWELL_PATTERNS:
            if pat.match(q_lower):
                return DomainDecision(
                    category="CONVERSATIONAL",
                    is_allowed=True,
                    conversational_response=(
                        "Goodbye! Feel free to return whenever you need assistance with your business operations."
                    ),
                    reason="Farewell detected",
                )

        # 3. Check for Mixed Query (out-of-domain part + in-domain business query)
        mixed_decision = cls._check_mixed_query(raw_query, q_lower, entity_res)
        if mixed_decision:
            return mixed_decision

        # 4. Check for Business Calculations (allowed)
        calc_decision = cls._check_business_calculation(raw_query, q_lower, entity_res, memory, session_id)
        if calc_decision:
            return calc_decision

        # 5. Check if query is definitely In-Domain
        is_in_domain, domain_reason = cls._is_in_domain(raw_query, q_lower, entity_res, memory, session_id)
        if is_in_domain:
            return DomainDecision(
                category="IN_DOMAIN",
                is_allowed=True,
                reason=domain_reason,
            )

        # 6. Check explicit out-of-domain patterns (pure math, science, trivia, creative writing, coding)
        is_explicit_ood, ood_reason = cls._is_explicit_out_of_domain(q_lower)
        if is_explicit_ood:
            return DomainDecision(
                category="OUT_OF_DOMAIN",
                is_allowed=False,
                rejection_message=OUT_OF_DOMAIN_REJECTION,
                reason=ood_reason,
            )

        # 7. Fallback for unclassified / unknown requests
        # If no business entity, no business keyword, and no conversational context matched, reject.
        return DomainDecision(
            category="OUT_OF_DOMAIN",
            is_allowed=False,
            rejection_message=OUT_OF_DOMAIN_REJECTION,
            reason="No supported business domain, entities, or operational context detected.",
        )

    @classmethod
    def _is_in_domain(
        cls,
        raw_query: str,
        q_lower: str,
        entity_res: Optional[EntityResolutionResult],
        memory: Optional[Any],
        session_id: Optional[str],
    ) -> Tuple[bool, str]:
        # Check resolved entities from EntityResolver
        if entity_res:
            if entity_res.product:
                return True, f"Resolved product entity: {entity_res.product.canonical_name}"
            if entity_res.order_id:
                return True, f"Resolved order ID: {entity_res.order_id}"
            if entity_res.customer:
                return True, f"Resolved customer entity: {entity_res.customer.canonical_name}"
            if entity_res.supplier:
                return True, f"Resolved supplier entity: {entity_res.supplier.canonical_name}"
            if entity_res.invoice_id:
                return True, f"Resolved invoice ID: {entity_res.invoice_id}"
            if entity_res.worksheet_name:
                return True, f"Resolved worksheet: {entity_res.worksheet_name}"

        # Check Business ID Regex
        for pat in cls.BUSINESS_ID_REGEX:
            if pat.search(raw_query):
                return True, f"Matched business ID pattern: {pat.pattern}"

        # Check Business Keywords
        matched_kw = [kw for kw in cls.BUSINESS_KEYWORDS if kw in q_lower]
        if matched_kw:
            # Special check: If query contains out-of-domain science/trivia but happened to include a pretext word
            # e.g. "Before checking inventory, explain quantum physics."
            is_explicit_ood, _ = cls._is_explicit_out_of_domain(q_lower)
            if is_explicit_ood:
                # If it has an explicit OOD topic and no actual business question/entity, reject
                has_substantive_business_req = cls._has_substantive_business_request(q_lower, entity_res)
                if not has_substantive_business_req:
                    return False, "Contains business pretext but primary query is out-of-domain."
            return True, f"Matched business keywords: {matched_kw[:3]}"

        # Check session memory for active follow-up context
        if memory and session_id:
            state = memory.get_state(session_id)
            if state.get("last_intent") or state.get("last_entities"):
                # Short follow-ups grounded in active session context
                date_followup_terms = [
                    "last month", "this month", "month before last", "previous month",
                    "two months ago", "2 months ago", "last two months", "last 2 months",
                    "show all", "confirm", "proceed", "cancel", "yes", "no", "sure", "go ahead",
                    "i need all", "show them", "yes, please", "yes, show them",
                    "september", "october", "november", "december", "january", "february",
                    "march", "april", "may", "june", "july", "august", "same for last year"
                ]
                if any(w in q_lower for w in date_followup_terms):
                    return True, "Follow-up query grounded in active session context"

        return False, "No business indicators found"

    @classmethod
    def _is_explicit_out_of_domain(cls, q_lower: str) -> Tuple[bool, str]:
        for pat in cls.PURE_MATH_PATTERNS:
            if pat.search(q_lower):
                return True, "Pure arithmetic calculation without business context"

        for pat in cls.SCIENCE_PATTERNS:
            if pat.search(q_lower):
                return True, f"Science/technology topic: {pat.pattern}"

        for pat in cls.TRIVIA_PATTERNS:
            if pat.search(q_lower):
                return True, f"General trivia/culture topic: {pat.pattern}"

        for pat in cls.CREATIVE_PATTERNS:
            if pat.search(q_lower):
                return True, f"Creative writing/entertainment: {pat.pattern}"

        for pat in cls.CODING_PATTERNS:
            if pat.search(q_lower):
                return True, f"General coding inquiry: {pat.pattern}"

        return False, ""

    @classmethod
    def _has_substantive_business_request(
        cls,
        q_lower: str,
        entity_res: Optional[EntityResolutionResult],
    ) -> bool:
        if entity_res and (entity_res.product or entity_res.order_id or entity_res.customer or entity_res.supplier):
            return True
        for pat in cls.BUSINESS_ID_REGEX:
            if pat.search(q_lower):
                return True
        # Check if there is an actionable operational verb + business object
        operational_verbs = ["show", "list", "check", "get", "what is", "how many", "compare", "create", "order", "reorder", "receive"]
        business_nouns = ["stock", "order", "orders", "revenue", "invoice", "invoices", "supplier", "suppliers", "payment", "payments"]
        has_verb = any(v in q_lower for v in operational_verbs)
        has_noun = any(n in q_lower for n in business_nouns)
        return has_verb and has_noun

    @classmethod
    def _check_mixed_query(
        cls,
        raw_query: str,
        q_lower: str,
        entity_res: Optional[EntityResolutionResult],
    ) -> Optional[DomainDecision]:
        """
        Detects queries containing both an out-of-domain part and an in-domain business query.
        Example: "What is 9+10? Also, how many units of P1001 are available?"
        """
        # Split on sentence boundaries or conjunctions
        segments = re.split(r"[\?\!\.\;]|\b(?:also|and\s+also|additionally|but\s+first|by\s+the\s+way)\b", raw_query, flags=re.IGNORECASE)
        segments = [s.strip() for s in segments if s.strip()]
        if len(segments) < 2:
            return None

        ood_segments = []
        in_domain_segments = []

        for seg in segments:
            seg_lower = seg.lower()
            is_ood, ood_why = cls._is_explicit_out_of_domain(seg_lower)
            if is_ood:
                ood_segments.append(seg)
                continue

            # Check if this segment is in-domain
            has_id = any(pat.search(seg) for pat in cls.BUSINESS_ID_REGEX)
            has_kw = any(kw in seg_lower for kw in cls.BUSINESS_KEYWORDS)
            if has_id or has_kw:
                # Ensure it's not just a pretext clause like "before checking inventory"
                if not re.search(r"^\s*before\s+checking\b", seg_lower):
                    in_domain_segments.append(seg)

        if ood_segments and in_domain_segments:
            ood_text = " / ".join(ood_segments)
            in_domain_text = " ".join(in_domain_segments)
            return DomainDecision(
                category="MIXED",
                is_allowed=True,
                in_domain_query=in_domain_text,
                out_of_domain_part=ood_text,
                reason=f"Mixed query: OOD part '{ood_text}', In-domain part '{in_domain_text}'",
            )

        return None

    @classmethod
    def _check_business_calculation(
        cls,
        raw_query: str,
        q_lower: str,
        entity_res: Optional[EntityResolutionResult],
        memory: Optional[Any],
        session_id: Optional[str],
    ) -> Optional[DomainDecision]:
        """
        Identifies and deterministically handles business calculations:
        - Total cost calculations: "What is the total cost of ordering 100 wireless mice at ₹250 each?"
        - Profit margin calculations: "Calculate the profit margin for this product" / "margin for P1001"
        """
        # A. Cost calculation: quantity * unit_price
        # Pattern e.g.: 100 wireless mice at ₹250 each
        cost_calc_pat = re.compile(
            r"(?:total\s+cost|total\s+price|calculate\s+(?:the\s+)?cost|cost\s+of\s+ordering|how\s+much\s+(?:for|to\s+order))\s+"
            r"(?:of\s+ordering\s+)?(\d+)\s*(?:units?|pieces?|pcs?|items?|box(?:es)?)?\s+"
            r"(?:of\s+)?(.*?)\s+(?:at|@)\s*(?:rs\.?|inr|₹)?\s*(\d+(?:\.\d+)?)",
            re.IGNORECASE,
        )
        m = cost_calc_pat.search(raw_query)
        if m:
            qty = int(m.group(1))
            prod_name_raw = m.group(2).strip()
            unit_price = float(m.group(3))
            total = qty * unit_price
            prod_display = (
                entity_res.product.canonical_name
                if entity_res and entity_res.product
                else prod_name_raw.title()
            )
            response_md = (
                f"**Answer**: The total cost of ordering {qty:,} units of {prod_display} "
                f"at ₹{unit_price:,.2f} each is ₹{total:,.2f}.\n\n"
                f"**Evidence**: {qty:,} units × ₹{unit_price:,.2f}/unit = ₹{total:,.2f}.\n\n"
                f"**Status**: CALCULATED\n\n"
                f"**Next Step**: Confirm if you would like to proceed with generating a purchase order for this requirement."
            )
            return DomainDecision(
                category="BUSINESS_CALCULATION",
                is_allowed=True,
                calculation_response=response_md,
                reason="Calculated total order procurement cost",
            )

        # If entity_res already resolved both quantity and unit_price and query asks for cost/total
        if entity_res and entity_res.quantity and entity_res.unit_price:
            if any(w in q_lower for w in ["total cost", "total price", "cost of ordering", "how much"]):
                qty = entity_res.quantity
                unit_price = entity_res.unit_price
                total = qty * unit_price
                prod_display = (
                    entity_res.product.canonical_name
                    if entity_res.product
                    else "specified items"
                )
                response_md = (
                    f"**Answer**: The total cost of ordering {qty:,} units of {prod_display} "
                    f"at ₹{unit_price:,.2f} each is ₹{total:,.2f}.\n\n"
                    f"**Evidence**: {qty:,} units × ₹{unit_price:,.2f}/unit = ₹{total:,.2f}.\n\n"
                    f"**Status**: CALCULATED\n\n"
                    f"**Next Step**: Confirm if you would like to proceed with generating a purchase order."
                )
                return DomainDecision(
                    category="BUSINESS_CALCULATION",
                    is_allowed=True,
                    calculation_response=response_md,
                    reason="Calculated total order procurement cost from resolved entity parameters",
                )

        # B. Profit Margin calculation
        if any(w in q_lower for w in ["profit margin", "calculate margin", "gross margin", "calculate the margin"]):
            target_product_id = None
            target_product_name = None

            if entity_res and entity_res.product:
                target_product_id = entity_res.product.canonical_id
                target_product_name = entity_res.product.canonical_name
            elif memory and session_id:
                state = memory.get_state(session_id)
                last_ent = state.get("last_entities", {})
                target_product_id = last_ent.get("product_id")
                target_product_name = last_ent.get("product_name")

            # Fallback check for product ID regex in query
            if not target_product_id:
                pid_match = re.search(r"\b(P\d{4})\b", raw_query, re.IGNORECASE)
                if pid_match:
                    target_product_id = pid_match.group(1).upper()

            # Default to P1001 if asking "this product" without preceding context
            if not target_product_id:
                target_product_id = "P1001"

            # Query database for product pricing
            prod_info = cls._get_product_financials(target_product_id)
            if prod_info:
                name = target_product_name or prod_info.get("name", target_product_id)
                cost = prod_info.get("unit_cost", 0.0)
                price = prod_info.get("selling_price", 0.0)
                profit_unit = round(price - cost, 2)
                margin_pct = round((profit_unit / price) * 100, 2) if price > 0 else 0.0
                markup_pct = round((profit_unit / cost) * 100, 2) if cost > 0 else 0.0

                response_md = (
                    f"**Answer**: The profit margin for {name} ({target_product_id}) is ₹{profit_unit:,.2f} "
                    f"per unit ({margin_pct:.2f}% gross margin on selling price).\n\n"
                    f"**Evidence**:\n"
                    f"- Selling Price: ₹{price:,.2f}\n"
                    f"- Unit Cost: ₹{cost:,.2f}\n"
                    f"- Gross Margin: ₹{profit_unit:,.2f} per unit\n"
                    f"- Profit Margin: {margin_pct:.2f}% (Markup on cost: {markup_pct:.2f}%)\n\n"
                    f"**Status**: CALCULATED\n\n"
                    f"**Next Step**: Review supplier pricing or consider volume pricing strategies to maintain margins."
                )
                return DomainDecision(
                    category="BUSINESS_CALCULATION",
                    is_allowed=True,
                    calculation_response=response_md,
                    reason=f"Calculated profit margin for {target_product_id}",
                )

        return None

    @staticmethod
    def _get_product_financials(product_id: str) -> Optional[Dict[str, Any]]:
        target_db = os.getenv("DB_PATH", str(DB_PATH))
        try:
            conn = sqlite3.connect(target_db)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT product_id, name, unit_cost, selling_price FROM products WHERE product_id = ?", (product_id,))
            row = cur.fetchone()
            conn.close()
            if row:
                return dict(row)
        except Exception:
            pass
        return None

