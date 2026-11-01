"""
Language Policy and Detection Service for BizOps Agent.

Enforces strict response language consistency:
- Default: English
- Respects explicit Hindi/Hinglish requests without leaking into English conversations.
- Preserves established conversation language across follow-ups and typos.
- Does not infer language from Indian business context, customer/supplier names, or INR (₹).
- Completely decoupled from business logic, database queries, and tool execution.
"""

import re
from typing import Optional, Set

# Strong Hindi/Hinglish vocabulary tokens (excluding ambiguous words or single connectors like 'aur')
STRONG_HINDI_TOKENS: Set[str] = {
    "kya", "kaunsa", "kaunse", "kaunsi", "kitna", "kitne", "kitni",
    "batao", "bataiye", "bata", "kripya", "hai", "hain", "tha", "the",
    "thi", "pichle", "pichla", "pichli", "agle", "agla", "agli", "mahine",
    "mahina", "din", "hoga", "hogi", "hoge", "chahiye", "mujhe", "humara",
    "humare", "humari", "apna", "apne", "apni", "dikhao", "dikhaye", "karo",
    "kariye", "kisko", "kisne", "kab", "kaise", "kahan", "rakha", "bheja",
    "khatam", "baki", "udhar", "dena", "lena", "kam", "matlab", "nahi",
}

# Explicit language switch expressions
SWITCH_TO_ENGLISH_PATTERNS = [
    r"\b(?:in\s+english|english\s+please|switch\s+to\s+english|reply\s+in\s+english|speak\s+in\s+english)\b",
]

SWITCH_TO_HINDI_PATTERNS = [
    r"\b(?:hindi\s+me(?:in)?|hinglish\s+me(?:in)?|switch\s+to\s+hindi|reply\s+in\s+hindi|hindi\s+please)\b",
]


class LanguagePolicy:
    """
    Authoritative language policy manager for BizOps Agent.
    """

    DEFAULT_LANGUAGE = "en"  # "en", "hinglish", or "hi"

    @classmethod
    def is_devanagari(cls, text: str) -> bool:
        """Checks if text contains Devanagari script."""
        return bool(re.search(r"[\u0900-\u097F]", text))

    @classmethod
    def detect_language(cls, text: str, current_session_lang: Optional[str] = None) -> str:
        """
        Detects conversation language with high persistence:
        - Devanagari script -> 'hi'
        - Explicit switch phrases take immediate precedence.
        - In an active 'en' session, minor connectors (like 'aur') or typos do NOT switch language.
        - In a new/unspecified session, presence of strong Hindi tokens activates 'hinglish'.
        """
        if not text or not text.strip():
            return current_session_lang or cls.DEFAULT_LANGUAGE

        text_clean = text.strip()
        text_lower = text_clean.lower()

        # 1. Devanagari script check
        if cls.is_devanagari(text_clean):
            return "hi"

        # 2. Explicit switch commands
        for pat in SWITCH_TO_ENGLISH_PATTERNS:
            if re.search(pat, text_lower):
                return "en"

        for pat in SWITCH_TO_HINDI_PATTERNS:
            if re.search(pat, text_lower):
                return "hinglish"

        # Tokenize alphabetic words
        words = re.findall(r"\b[a-z]+\b", text_lower)
        hindi_word_count = sum(1 for w in words if w in STRONG_HINDI_TOKENS)

        # 3. Established session preservation
        if current_session_lang == "en":
            # Very high threshold to switch away from established English
            # Follow-ups like "Aur low stock products?" have 1 Hindi connector ("aur")
            # and English business terms ("low stock products").
            # Must stay English!
            if hindi_word_count >= 3:
                return "hinglish"
            return "en"

        elif current_session_lang in ("hinglish", "hi"):
            # Check if user explicitly switched to English
            english_words = [w for w in words if w not in STRONG_HINDI_TOKENS and w != "aur"]
            # If the user speaks a complete English sentence with zero Hindi words
            if hindi_word_count == 0 and len(english_words) >= 3:
                return "en"
            return current_session_lang

        # 4. Fresh session initial detection
        if hindi_word_count >= 1:
            return "hinglish"

        return cls.DEFAULT_LANGUAGE

    @classmethod
    def get_system_prompt_instructions(cls, language: str) -> str:
        """
        Generates strict, non-negotiable prompt instructions for the Groq LLM ReAct loop.
        """
        if language == "en":
            return (
                "ACTIVE CONVERSATION LANGUAGE: English\n"
                "- CRITICAL: The user is communicating in English. Formulate all responses, summaries, "
                "evidence bullets, status messages, and clarification questions STRICTLY IN ENGLISH.\n"
                "- NEVER use Hindi or Hinglish words (e.g., do NOT write 'Aapka query', 'baare mein', 'kya jaanna chahte hain').\n"
                "- NEVER infer Hindi preference from Indian business context, customer/supplier names, or INR (₹) currency.\n"
                "- If asking for clarification, ask clearly and concisely in English."
            )
        elif language == "hinglish":
            return (
                "ACTIVE CONVERSATION LANGUAGE: Hinglish (Conversational Hindi in Roman script)\n"
                "- The user is communicating in Hinglish. Formulate your conversational response politely in Hinglish.\n"
                "- Keep exact numbers, currency (INR ₹), dates, product names, and IDs accurate."
            )
        elif language == "hi":
            return (
                "ACTIVE CONVERSATION LANGUAGE: Hindi (Devanagari script)\n"
                "- The user is communicating in Hindi. Formulate your conversational response in Devanagari Hindi.\n"
                "- Keep exact numbers, currency (INR ₹), dates, product names, and IDs accurate."
            )
        return cls.get_system_prompt_instructions("en")
