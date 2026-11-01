"""Re-export canonical prompts from llm.prompts for backward compatibility."""
from llm.prompts import REACT_SYSTEM_PROMPT, REFLECTION_SYSTEM_PROMPT

__all__ = ["REACT_SYSTEM_PROMPT", "REFLECTION_SYSTEM_PROMPT"]
