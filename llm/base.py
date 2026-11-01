import asyncio
from abc import ABC, abstractmethod
import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class LLMClient(ABC):
    """
    Abstract base class for LLM providers.
    Defines availability checks, JSON generation, lifecycle hooks, and diagnostics.
    """
    provider_name: str = "base"
    last_status: str = "UNKNOWN"
    last_error_message: Optional[str] = None
    last_latency_ms: float = 0.0

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Returns the active model identifier."""
        pass

    @abstractmethod
    async def is_available(self) -> bool:
        """Verifies if the LLM provider service and configured model are accessible."""
        pass

    @abstractmethod
    async def generate_json(
        self,
        prompt: str,
        system_prompt: str,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Generates structured JSON matching ReAct action schema."""
        pass

    async def close(self) -> None:
        """Lifecycle hook to close underlying HTTP sessions or client pools."""
        pass

    @staticmethod
    def _clean_and_parse_json(text: str) -> Optional[Dict[str, Any]]:
        """Strips markdown code fences and extracts the first valid JSON object."""
        if not text:
            return None
        cleaned = text.strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE)

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except Exception:
                    pass
            return None

