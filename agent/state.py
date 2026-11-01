"""
State management module.
Re-exports ConversationMemory from agent.memory for consistent session handling.
"""
from agent.memory import ConversationMemory

__all__ = ["ConversationMemory"]
