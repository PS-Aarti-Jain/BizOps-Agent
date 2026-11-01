"""
LLM Client module for BizOps Agent.
The canonical implementation is located in the `llm` package (llm.base, llm.groq_client).
This module re-exports the canonical classes and provides a backward-compatible factory.
"""
from typing import Optional
from llm.base import LLMClient
from llm.groq_client import GroqClient


def create_llm_client(provider: Optional[str] = None, *args, **kwargs) -> LLMClient:
    """Factory creating the configured Groq LLM client."""
    return GroqClient()


__all__ = ["LLMClient", "GroqClient", "create_llm_client"]
