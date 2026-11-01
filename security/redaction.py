import json
import os
import re
from typing import Any


class SecuritySanitizer:
    @staticmethod
    def wrap_untrusted_observation(tool_name: str, raw_result: Any) -> str:
        """
        Wraps tool outputs in structural XML tags and sanitizes potential system override patterns.
        """
        if isinstance(raw_result, (dict, list)):
            content_str = json.dumps(raw_result, indent=2)
        else:
            content_str = str(raw_result)

        # Sanitize known injection instruction attempts inside text
        sanitized = re.sub(
            r"(?i)(ignore previous instructions|system override|you are now|delete all tables)",
            "[REDACTED INJECTION ATTEMPT]",
            content_str,
        )

        return f"<untrusted_tool_observation tool_name=\"{tool_name}\">\n{sanitized}\n</untrusted_tool_observation>"

    @staticmethod
    def strip_pii_for_cloud(text: str) -> str:
        """
        Strips direct customer PII (emails, phone numbers, addresses) from observations
        before cloud dispatch.
        """
        if not text:
            return ""

        # Redact email addresses
        redacted = re.sub(
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
            "[EMAIL_REDACTED]",
            text,
        )

        # Redact phone numbers
        redacted = re.sub(
            r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
            "[PHONE_REDACTED]",
            redacted,
        )

        # Redact street addresses
        redacted = re.sub(
            r"\b\d+\s+[A-Za-z0-9\s]+(?:St|Street|Way|Blvd|Avenue|Ave|Rd|Road|Lane|Dr|Drive|Court|Ct|Marg|Nagar|Road),\s*[A-Za-z\s]+,\s*[A-Za-z\s0-9]+\b",
            "[ADDRESS_REDACTED]",
            redacted,
        )

        return redacted

    @classmethod
    def mask_pii(cls, text: str) -> str:
        return cls.strip_pii_for_cloud(text)

    @staticmethod
    def mask_secrets(text: str, extra_secret: str = "") -> str:
        """
        Masks secret tokens, API keys, and Bearer headers from text so they never
        leak to logs, transcripts, or exceptions.
        """
        if not text:
            return ""

        masked = text

        # Mask configured keys
        for env_var in ("GROQ_API_KEY", "XAI_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS"):
            key_to_mask = os.getenv(env_var, "").strip()
            if key_to_mask and len(key_to_mask) >= 6:
                masked = masked.replace(key_to_mask, "[REDACTED_API_KEY]")

        if extra_secret and len(extra_secret) >= 6:
            masked = masked.replace(extra_secret, "[REDACTED_API_KEY]")

        # Mask Bearer tokens
        masked = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9_\-\.]{8,}", "Bearer [REDACTED_TOKEN]", masked)

        # Mask API key patterns
        masked = re.sub(
            r"(?i)\b(groq[_-]?key|xai[_-]?key|api[_-]?key|secret)\s*[:=]\s*[\"']?([A-Za-z0-9_\-]{8,})[\"']?",
            r"\1=[REDACTED_KEY]",
            masked,
        )

        return masked

