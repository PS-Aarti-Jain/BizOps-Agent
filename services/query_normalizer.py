"""
Business Query Normalizer and Typo Tolerance Service.

Normalizes common spelling mistakes, phonetic variations, and typos in business operations
queries while preserving proper nouns, entity IDs, and codes (e.g., P1001, ORD-1042, SUP-101).
"""

import re
from typing import Dict

# Common operational query typos mapped to canonical English business terms
COMMON_TYPO_MAP: Dict[str, str] = {
    # Dates / time periods
    r"\bmaonth\b": "month",
    r"\bmont\b": "month",
    r"\bmounth\b": "month",
    r"\bmnth\b": "month",
    r"\byesterdy\b": "yesterday",
    r"\btodaye\b": "today",
    # Orders
    r"\bpendng\b": "pending",
    r"\bpendin\b": "pending",
    r"\bpendg\b": "pending",
    r"\bordrs\b": "orders",
    r"\bordres\b": "orders",
    r"\borderd\b": "ordered",
    r"\bdelivred\b": "delivered",
    r"\bdeliverd\b": "delivered",
    r"\bdispathed\b": "dispatched",
    r"\bdispached\b": "dispatched",
    # Finance / Revenue
    r"\brevinue\b": "revenue",
    r"\brevenu\b": "revenue",
    r"\brevene\b": "revenue",
    r"\binvois\b": "invoice",
    r"\binvoce\b": "invoice",
    r"\binvoces\b": "invoices",
    r"\bbalence\b": "balance",
    r"\bbalnce\b": "balance",
    r"\bpaymnt\b": "payment",
    r"\bpaymnts\b": "payments",
    # Inventory / Stock
    r"\binventry\b": "inventory",
    r"\binvetory\b": "inventory",
    r"\binventorys\b": "inventory",
    r"\bavialable\b": "available",
    r"\bavailble\b": "available",
    r"\bavailabe\b": "available",
    r"\bstok\b": "stock",
    r"\breordr\b": "reorder",
    r"\bshortag\b": "shortage",
    # Suppliers
    r"\bsuplier\b": "supplier",
    r"\bsupliers\b": "suppliers",
    r"\bsuppler\b": "supplier",
    r"\bvendr\b": "vendor",
    r"\bvendrs\b": "vendors",
    r"\bpurchas\b": "purchase",
    # Customers
    r"\bcustmer\b": "customer",
    r"\bcustmr\b": "customer",
    r"\bcustmers\b": "customers",
}


def normalize_query_typos(query: str) -> str:
    """
    Substitutes common business query typos with canonical terms while preserving
    casing, proper nouns, and entity IDs (e.g. CUST-101, ORD-1042).
    """
    if not query:
        return ""

    normalized = query
    for typo_pattern, canonical in COMMON_TYPO_MAP.items():
        normalized = re.sub(typo_pattern, canonical, normalized, flags=re.IGNORECASE)

    return normalized

