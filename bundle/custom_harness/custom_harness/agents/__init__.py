"""
Custom Harness Deterministic Sub-Agents Subsystem
=================================================
Isolated Coder and Critic sub-agents with separate context windows and feedback loop orchestration.
"""

from .base import BaseSubAgent
from .coder import CoderSubAgent
from .critic import CriticSubAgent
from .feedback_loop import (
    DeterministicFeedbackLoop,
    DualAgentOrchestrator,
)

__all__ = [
    "BaseSubAgent",
    "CoderSubAgent",
    "CriticSubAgent",
    "DeterministicFeedbackLoop",
    "DualAgentOrchestrator",
]
