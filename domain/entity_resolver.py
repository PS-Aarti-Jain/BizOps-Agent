import logging
import re
from typing import Any, Dict, List, Literal, Optional, Set
from pydantic import BaseModel, Field

from database.repositories.repository import BizOpsRepository

logger = logging.getLogger(__name__)


class EntityMatch(BaseModel):
    entity_type: Literal["customer", "order", "product", "supplier", "invoice", "sheet"]
    canonical_id: str
    canonical_name: str
    match_type: Literal["exact_id", "exact_name", "prefix_match", "token_match", "location_match"]
    confidence: float
    raw_query_span: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class EntityResolutionResult(BaseModel):
    status: Literal["RESOLVED", "AMBIGUOUS", "NOT_FOUND", "NO_ENTITY"]
    customer: Optional[EntityMatch] = None
    order_id: Optional[str] = None
    product: Optional[EntityMatch] = None
    supplier: Optional[EntityMatch] = None
    invoice_id: Optional[str] = None
    worksheet_name: Optional[str] = None
    quantity: Optional[int] = None
    unit_price: Optional[float] = None
    candidates: List[Dict[str, Any]] = Field(default_factory=list)
    clarification_prompt: Optional[str] = None
    raw_extracted_text: Optional[str] = None


class EntityResolver:
    """
    Database-backed, robust entity resolution layer for Indian small-business operations.
    Handles Hindi/Hinglish phrasing, contractions, location-based orders (e.g. 'Pune order'),
    product colloquialisms (e.g. 'A4 paper', 'tea', 'cooking oil', 'wireless mice'),
    and Google Sheets integration targets.
    """

    CONTRACTIONS = {
        r"\bhaven't\b": "have not",
        r"\bhasn't\b": "has not",
        r"\bhadn't\b": "had not",
        r"\bdon't\b": "do not",
        r"\bdoesn't\b": "does not",
        r"\bdidn't\b": "did not",
        r"\bcan't\b": "cannot",
        r"\bcouldn't\b": "could not",
        r"\bwon't\b": "will not",
        r"\bwouldn't\b": "would not",
        r"\bshouldn't\b": "should not",
        r"\bisn't\b": "is not",
        r"\baren't\b": "are not",
        r"\bwasn't\b": "was not",
        r"\bweren't\b": "were not",
        r"\bwe're\b": "we are",
        r"\bthey're\b": "they are",
        r"\byou're\b": "you are",
        r"\bi'm\b": "i am",
        r"\bwe've\b": "we have",
        r"\bwhat's\b": "what is",
        r"\bhow's\b": "how is",
        r"\bwhere's\b": "where is",
    }

    PRODUCT_SYNONYMS = {
        "wireless mice": "Wireless Optical Mouse (USB)",
        "wireless mouse": "Wireless Optical Mouse (USB)",
        "mice": "Wireless Optical Mouse (USB)",
        "mouse": "Wireless Optical Mouse (USB)",
        "a4 paper": "A4 Copier Paper 75 GSM (Ream)",
        "copier paper": "A4 Copier Paper 75 GSM (Ream)",
        "paper ream": "A4 Copier Paper 75 GSM (Ream)",
        "paper": "A4 Copier Paper 75 GSM (Ream)",
        "cooking oil": "Refined Sunflower Cooking Oil 1L",
        "sunflower oil": "Refined Sunflower Cooking Oil 1L",
        "oil": "Refined Sunflower Cooking Oil 1L",
        "tea": "Premium CTC Tea Packets 500g",
        "tea packets": "Premium CTC Tea Packets 500g",
        "tea powder": "Premium CTC Tea Packets 500g",
        "biscuits": "Parle-style Glucose Biscuits 800g",
        "glucose biscuits": "Parle-style Glucose Biscuits 800g",
        "detergent": "Washing Detergent Powder 1kg",
        "detergent powder": "Washing Detergent Powder 1kg",
        "pens": "Blue Ballpoint Pens (Pack of 20)",
        "ball pens": "Blue Ballpoint Pens (Pack of 20)",
        "boxes": "3-Ply Corrugated Packing Boxes (Pack of 25)",
        "packing boxes": "3-Ply Corrugated Packing Boxes (Pack of 25)",
        "corrugated boxes": "3-Ply Corrugated Packing Boxes (Pack of 25)",
        "tape": "Brown Packing Tape 65m Roll",
        "packing tape": "Brown Packing Tape 65m Roll",
        "bubble wrap": "Bubble Wrap Roll 50m x 1m",
        "keyboard": "Mechanical Gaming Keyboard",
        "monitor": "24-Inch IPS Full HD Monitor",
        "extension board": "4-Way Power Extension Board",
    }

    def __init__(self, repo: Optional[BizOpsRepository] = None):
        self.repo = repo or BizOpsRepository()

    def normalize_text(self, text: str) -> str:
        cleaned = text
        for pat, repl in self.CONTRACTIONS.items():
            cleaned = re.sub(pat, repl, cleaned, flags=re.IGNORECASE)
        return cleaned.strip()

    def resolve_query(self, query: str) -> EntityResolutionResult:
        normalized = self.normalize_text(query)
        q_lower = normalized.lower()

        # 1. Canonical ID Extractions
        order_match = re.search(r"\b(ORD-\d+)\b", normalized, re.IGNORECASE)
        order_id = order_match.group(1).upper() if order_match else None

        cust_id_match = re.search(r"\b(CUST-\d+)\b", normalized, re.IGNORECASE)
        cust_id = cust_id_match.group(1).upper() if cust_id_match else None

        prod_id_match = re.search(r"\b(P\d{4})\b", normalized, re.IGNORECASE)
        prod_id = prod_id_match.group(1).upper() if prod_id_match else None

        sup_id_match = re.search(r"\b(SUP-\d+)\b", normalized, re.IGNORECASE)
        sup_id = sup_id_match.group(1).upper() if sup_id_match else None

        inv_id_match = re.search(r"\b(INV-\d+)\b", normalized, re.IGNORECASE)
        inv_id = inv_id_match.group(1).upper() if inv_id_match else None

        # Worksheet check
        worksheet_name = None
        if "sheet" in q_lower or "spreadsheet" in q_lower or "worksheet" in q_lower or "tab" in q_lower:
            explicit_ws = re.search(r"(?:worksheet|tab|sheet)\s+['\"]?([A-Za-z0-9_-]+)['\"]?", normalized, re.IGNORECASE)
            if explicit_ws and explicit_ws.group(1).lower() not in ("in", "from", "to", "the", "a", "an", "google", "records", "data"):
                worksheet_name = explicit_ws.group(1)
            elif "customer" in q_lower:
                worksheet_name = "Customers"
            elif "order" in q_lower:
                worksheet_name = "Orders"
            elif "supplier" in q_lower:
                worksheet_name = "Suppliers"
            else:
                worksheet_name = "Inventory"

        # Quantity extraction
        qty_match = re.search(r"\b(\d+)\s*(?:units?|pieces?|mice|items?|reams?|qty|boxes|bottles)?\b", normalized, re.IGNORECASE)
        quantity = int(qty_match.group(1)) if qty_match and int(qty_match.group(1)) < 100000 else None

        # Unit price extraction (e.g., "at ₹245.00 each", "at 245.00 each", "at ₹245")
        price_match = re.search(
            r"(?:at|@)\s*(?:rs\.?|₹)?\s*(\d+(?:\.\d{1,2})?)\s*(?:each|per\s+unit)?|(?:rs\.?|₹)\s*(\d+(?:\.\d{1,2})?)",
            normalized,
            re.IGNORECASE,
        )
        if price_match:
            raw_p = price_match.group(1) or price_match.group(2)
            unit_price = float(raw_p) if raw_p else None
        else:
            unit_price = None

        # 2. Location-based order resolution (e.g. "Pune order", "Nashik order")
        if not order_id and ("order" in q_lower or "shipped" in q_lower or "dispatch" in q_lower):
            for city in ["pune", "mumbai", "nashik", "thane"]:
                if city in q_lower:
                    from database.connection import get_db_connection
                    conn = get_db_connection(self.repo.db_path)
                    try:
                        cur = conn.cursor()
                        cur.execute("""
                            SELECT o.order_id, c.name as customer_name, c.customer_id
                            FROM orders o
                            JOIN customers c ON o.customer_id = c.customer_id
                            WHERE LOWER(c.city) = ? OR LOWER(o.shipping_address) LIKE ?
                            ORDER BY o.order_date DESC LIMIT 1;
                        """, (city, f"%{city}%"))
                        found_order = cur.fetchone()
                        if found_order:
                            order_id = found_order["order_id"]
                            cust_id = found_order["customer_id"]
                            break
                    finally:
                        conn.close()

        # 3. Customer Entity Resolution
        customer_match: Optional[EntityMatch] = None
        customer_ambiguous_candidates: List[Dict[str, Any]] = []
        customer_not_found_name: Optional[str] = None

        if cust_id:
            c_rec = self.repo.get_customer(cust_id)
            if c_rec:
                customer_match = EntityMatch(
                    entity_type="customer",
                    canonical_id=c_rec["customer_id"],
                    canonical_name=c_rec["name"],
                    match_type="exact_id",
                    confidence=1.0,
                    raw_query_span=cust_id,
                    metadata=c_rec,
                )
            else:
                customer_not_found_name = cust_id
        else:
            extracted_customer_names = self._extract_customer_name_candidates(normalized)
            for cand_text in extracted_customer_names:
                matches = self._search_database_customers(cand_text)
                if len(matches) == 1:
                    customer_match = matches[0]
                    break
                elif len(matches) > 1:
                    customer_ambiguous_candidates = [m.metadata for m in matches]
                    break
                else:
                    customer_not_found_name = cand_text

        # 4. Product Entity Resolution
        product_match: Optional[EntityMatch] = None
        if prod_id:
            p_rec = self.repo.get_product(prod_id)
            if p_rec:
                product_match = EntityMatch(
                    entity_type="product",
                    canonical_id=p_rec["product_id"],
                    canonical_name=p_rec["name"],
                    match_type="exact_id",
                    confidence=1.0,
                    raw_query_span=prod_id,
                    metadata=p_rec,
                )
        else:
            # Check direct synonyms first
            for syn_term, canonical_name in self.PRODUCT_SYNONYMS.items():
                if syn_term in q_lower:
                    p_rec = self.repo.get_product(canonical_name)
                    if p_rec:
                        product_match = EntityMatch(
                            entity_type="product",
                            canonical_id=p_rec["product_id"],
                            canonical_name=p_rec["name"],
                            match_type="exact_name",
                            confidence=0.98,
                            raw_query_span=syn_term,
                            metadata=p_rec,
                        )
                        break

            if not product_match:
                extracted_prod_names = self._extract_product_name_candidates(normalized)
                for cand in extracted_prod_names:
                    p_rec = self.repo.get_product(cand)
                    if p_rec:
                        product_match = EntityMatch(
                            entity_type="product",
                            canonical_id=p_rec["product_id"],
                            canonical_name=p_rec["name"],
                            match_type="token_match",
                            confidence=0.9,
                            raw_query_span=cand,
                            metadata=p_rec,
                        )
                        break

        # 5. Supplier Entity Resolution
        supplier_match: Optional[EntityMatch] = None
        if sup_id:
            s_rec = self.repo.get_supplier(sup_id)
            if s_rec:
                supplier_match = EntityMatch(
                    entity_type="supplier",
                    canonical_id=s_rec["supplier_id"],
                    canonical_name=s_rec["name"],
                    match_type="exact_id",
                    confidence=1.0,
                    raw_query_span=sup_id,
                    metadata=s_rec,
                )
        else:
            extracted_sup_names = self._extract_supplier_name_candidates(normalized)
            for cand in extracted_sup_names:
                s_rec = self._search_database_supplier(cand)
                if s_rec:
                    supplier_match = EntityMatch(
                        entity_type="supplier",
                        canonical_id=s_rec["supplier_id"],
                        canonical_name=s_rec["name"],
                        match_type="token_match",
                        confidence=0.9,
                        raw_query_span=cand,
                        metadata=s_rec,
                    )
                    break

        # Ambiguity check
        if customer_ambiguous_candidates:
            options_str = ", ".join(f"'{c['name']}' ({c['customer_id']})" for c in customer_ambiguous_candidates)
            return EntityResolutionResult(
                status="AMBIGUOUS",
                candidates=customer_ambiguous_candidates,
                clarification_prompt=f"Multiple businesses match your request: {options_str}. Which customer did you mean?",
                customer=None,
                order_id=order_id,
                product=product_match,
                supplier=supplier_match,
                invoice_id=inv_id,
                worksheet_name=worksheet_name,
                quantity=quantity,
                unit_price=unit_price,
            )

        overall_status = (
            "RESOLVED"
            if (customer_match or order_id or product_match or supplier_match or inv_id or worksheet_name)
            else "NO_ENTITY"
        )

        return EntityResolutionResult(
            status=overall_status,
            customer=customer_match,
            order_id=order_id,
            product=product_match,
            supplier=supplier_match,
            invoice_id=inv_id,
            worksheet_name=worksheet_name,
            quantity=quantity,
            unit_price=unit_price,
        )

    def _extract_customer_name_candidates(self, query: str) -> List[str]:
        q_lower = query.lower()
        candidates = []

        # Pattern: possessive, e.g. "Om Sai's order", "ABC Retail's balance"
        pos_match = re.search(r"([A-Za-z0-9\s&]+?)'s\s+(?:order|orders|shipment|shipments|invoice|invoices|account|balance)\b", query, re.IGNORECASE)
        if pos_match:
            candidate = pos_match.group(1).strip()
            cleaned_cand = re.sub(r"^(?:why\s+have\s+not\s+we\s+shipped|why\s+has\s+not|why\s+haven't\s+we\s+shipped|shipped|for|about|check|has)\s+", "", candidate, flags=re.IGNORECASE).strip()
            if cleaned_cand:
                candidates.append(cleaned_cand)

        # Pattern: "for <Customer>" or "has <Customer> paid"
        for pat in [
            r"\bhas\s+([A-Za-z0-9\s&]+?)\s+(?:paid|cleared|exceeded)\b",
            r"\b(?:orders?(?:\s+history)?\s+for|invoices?\s+for|balance\s+for|account\s+of|customer|record\s+for)\s+([A-Za-z0-9\s&]+?)(?:\.|\?|\bwith\b|\band\b|$)",
            r"\bshipped\s+([A-Za-z0-9\s&]+?)(?:'s|\s+order|\.|\?|$)",
        ]:
            for m in re.finditer(pat, query, re.IGNORECASE):
                cand = m.group(1).strip()
                cand = re.sub(r"^(?:the|a|an|our)\s+", "", cand, flags=re.IGNORECASE).strip()
                if cand and cand.lower() not in ("order", "orders", "shipment", "balance", "we", "not", "its", "their") and cand not in candidates:
                    candidates.append(cand)

        # Database customers scan
        from database.connection import get_db_connection
        conn = get_db_connection(self.repo.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM customers;")
            for row in cursor.fetchall():
                c_name = row["name"]
                if c_name.lower() in q_lower:
                    if c_name not in candidates:
                        candidates.append(c_name)
                else:
                    words = c_name.split()
                    if len(words) >= 2:
                        prefix = " ".join(words[:2])
                        if prefix.lower() in q_lower and prefix not in candidates:
                            candidates.append(prefix)
        finally:
            conn.close()

        return candidates

    def _search_database_customers(self, candidate_name: str) -> List[EntityMatch]:
        norm_cand = candidate_name.strip().lower()
        if not norm_cand or len(norm_cand) < 2:
            return []

        from database.connection import get_db_connection
        conn = get_db_connection(self.repo.db_path)
        matches: List[EntityMatch] = []
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM customers;")
            all_customers = cursor.fetchall()

            cand_tokens = set(re.findall(r"\b\w+\b", norm_cand))
            clean_cand_tokens = {
                t for t in cand_tokens if t not in ("enterprises", "stores", "solutions", "distributors", "mart", "supermarket", "traders", "electricals", "stationery")
            }

            for c in all_customers:
                c_name = c["name"].lower()
                c_tokens = set(re.findall(r"\b\w+\b", c_name))

                if norm_cand == c_name:
                    matches.append(
                        EntityMatch(
                            entity_type="customer",
                            canonical_id=c["customer_id"],
                            canonical_name=c["name"],
                            match_type="exact_name",
                            confidence=1.0,
                            raw_query_span=candidate_name,
                            metadata=c,
                        )
                    )
                    continue

                if norm_cand in c_name:
                    matches.append(
                        EntityMatch(
                            entity_type="customer",
                            canonical_id=c["customer_id"],
                            canonical_name=c["name"],
                            match_type="prefix_match",
                            confidence=0.95,
                            raw_query_span=candidate_name,
                            metadata=c,
                        )
                    )
                    continue

                overlap = clean_cand_tokens.intersection(c_tokens)
                if clean_cand_tokens and len(overlap) == len(clean_cand_tokens):
                    matches.append(
                        EntityMatch(
                            entity_type="customer",
                            canonical_id=c["customer_id"],
                            canonical_name=c["name"],
                            match_type="token_match",
                            confidence=0.85,
                            raw_query_span=candidate_name,
                            metadata=c,
                        )
                    )

            matches.sort(key=lambda m: m.confidence, reverse=True)
            return matches
        finally:
            conn.close()

    def _extract_product_name_candidates(self, query: str) -> List[str]:
        candidates = []
        query_lower = query.lower()

        qty_match = re.search(r"\b\d+\s+([a-zA-Z\s]+?)(?:\s+from|\s+for|\.|\?|$)", query_lower)
        if qty_match:
            cand = qty_match.group(1).strip()
            if cand not in ("units", "items", "pieces", "supplier", "suppliers"):
                candidates.append(cand)

        for pat in [
            r"(?:low on|reorder|need|about|buy|for|enough)\s+([a-zA-Z\s]+?)(?:\s+from|\s+for|\.|\?|\band\b|$)",
            r"(?:order for)\s+([a-zA-Z\s]+?)(?:\s+from|\.|\?|$)",
        ]:
            for m in re.finditer(pat, query_lower):
                cand = m.group(1).strip()
                cand = re.sub(r"^(?:the|a|an|our)\s+", "", cand).strip()
                if cand and cand not in candidates and cand not in ("supplier", "recommended supplier"):
                    candidates.append(cand)

        return candidates

    def _extract_supplier_name_candidates(self, query: str) -> List[str]:
        candidates = []
        from_match = re.search(r"\bfrom\s+([a-zA-Z0-9\s&]+?)(?:\.|\?|\band\b|$)", query, re.IGNORECASE)
        if from_match:
            cand = from_match.group(1).strip()
            cand = re.sub(r"^(?:the|a|an|our)\s+", "", cand, flags=re.IGNORECASE).strip()
            if cand and cand.lower() not in ("supplier", "recommended supplier", "warehouse", "store"):
                candidates.append(cand)

        return candidates

    def _search_database_supplier(self, candidate_name: str) -> Optional[Dict[str, Any]]:
        cand_lower = candidate_name.strip().lower()
        from database.connection import get_db_connection
        conn = get_db_connection(self.repo.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM suppliers;")
            all_suppliers = cursor.fetchall()
            for s in all_suppliers:
                s_name = s["name"].lower()
                if cand_lower == s_name or cand_lower in s_name or s_name in cand_lower:
                    return s
            return None
        finally:
            conn.close()
