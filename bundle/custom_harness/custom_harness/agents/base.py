"""
Custom Harness Base Sub-Agent
=============================
Abstract base class for sub-agents with strict token and context window isolation.
Each agent maintains its own isolated message history to prevent context pollution and sycophancy.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from custom_harness.harness.client import BaseLLMClient
from custom_harness.harness.models import AgentMessage, AgentRole
from custom_harness.harness.retry import ReflectionRetryEngine


class BaseSubAgent(ABC):
    """
    Abstract sub-agent providing isolated message buffers and reflection retry capabilities.
    """

    def __init__(
        self,
        name: str,
        role: AgentRole,
        client: BaseLLMClient,
        system_instruction: str,
        max_retries: int = 3,
    ):
        self.name = name
        self.role = role
        self.client = client
        self._system_instruction = system_instruction
        self.max_retries = max_retries
        self._messages: List[AgentMessage] = []
        self._retry_engine = ReflectionRetryEngine(default_max_retries=max_retries)

    @property
    def system_instruction(self) -> str:
        """Get the immutable system prompt."""
        return self._system_instruction

    def add_message(
        self,
        role: AgentRole,
        content: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> AgentMessage:
        """Add a message to this sub-agent's private context window."""
        msg = AgentMessage(
            role=role,
            content=content,
            metadata=metadata or {}
        )
        self._messages.append(msg)
        return msg

    def get_messages(self) -> List[AgentMessage]:
        """Return a copy of the private message history."""
        return list(self._messages)

    def clear_messages(self) -> None:
        """Reset private context window."""
        self._messages.clear()

    def estimate_context_tokens(self) -> int:
        """
        Heuristic token count estimation (~4 characters per token) across system instruction
        and private message buffer.
        """
        total_chars = len(self._system_instruction)
        for msg in self._messages:
            total_chars += len(msg.content)
        return max(1, total_chars // 4)

    def get_formatted_context_prompt(self, current_prompt: str) -> str:
        """
        Combine message history into a formatted prompt buffer for this sub-agent.
        """
        if not self._messages:
            return current_prompt

        lines = []
        for msg in self._messages:
            role_tag = f"[{msg.role.value.upper()}]"
            lines.append(f"{role_tag}\n{msg.content}\n")
        lines.append(f"[CURRENT TASK]\n{current_prompt}")
        return "\n".join(lines)
