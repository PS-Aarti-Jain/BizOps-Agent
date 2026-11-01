from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class ReActAction(BaseModel):
    action: str  # Tool name or "final"
    args: Dict[str, Any] = Field(default_factory=dict)
    answer: Optional[str] = None


class ReflectionResponse(BaseModel):
    status: str  # "ready" or "needs_tool"
    reason: str
    missing_tool: Optional[str] = None

