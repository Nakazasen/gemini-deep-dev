"""
Custom Harness - Antigravity Deterministic Python Runtime Harness
=================================================================
Deterministic sub-agent runtime, strict Pydantic V2 schemas, MCP documentation grounding,
and fail-closed declarative guardrail policies for Google Gemini Flash.
"""

from .agents import (
    BaseSubAgent,
    CoderSubAgent,
    CriticSubAgent,
    DeterministicFeedbackLoop,
    DualAgentOrchestrator,
)
from .config import HarnessSettings, load_settings
from .guardrails import (
    GuardrailEngine,
    GuardrailPolicy,
    PathBoundaryValidator,
    PathValidator,
    PolicyEngine,
    RuleLoader,
)
from .harness import (
    AgentMessage,
    AgentRole,
    BaseLLMClient,
    ChecklistItemResult,
    CoderOutput,
    CriticOutput,
    FeedbackLoopResult,
    FileAction,
    FileOperation,
    GenAIClient,
    HarnessConfig,
    HarnessFSM,
    LLMClientFactory,
    MockLLMClient,
    OverallVerdict,
    ReflectionRetryEngine,
    SeverityLevel,
    StepHistory,
    StepState,
    StrictBaseModel,
    extract_json_string,
    parse_and_validate,
    strip_markdown_fences,
)

__version__ = "1.0.0"
__author__ = "Antigravity Engineering"

__all__ = [
    # Version
    "__version__",
    "__author__",
    # Agents
    "BaseSubAgent",
    "CoderSubAgent",
    "CriticSubAgent",
    "DeterministicFeedbackLoop",
    "DualAgentOrchestrator",
    # Config
    "HarnessSettings",
    "load_settings",
    # Guardrails
    "GuardrailEngine",
    "PolicyEngine",
    "GuardrailPolicy",
    "PathValidator",
    "PathBoundaryValidator",
    "RuleLoader",
    # Harness
    "StrictBaseModel",
    "FileAction",
    "FileOperation",
    "CoderOutput",
    "CriticOutput",
    "ChecklistItemResult",
    "OverallVerdict",
    "SeverityLevel",
    "AgentRole",
    "AgentMessage",
    "StepState",
    "StepHistory",
    "FeedbackLoopResult",
    "HarnessConfig",
    "BaseLLMClient",
    "MockLLMClient",
    "GenAIClient",
    "LLMClientFactory",
    "ReflectionRetryEngine",
    "HarnessFSM",
    "extract_json_string",
    "parse_and_validate",
    "strip_markdown_fences",
]
