"""
Custom Harness Core Subsystem
=============================
Deterministic Gemini Flash agent harness components including Pydantic V2 strict models,
multi-tier JSON parser, LLM client adapters, reflection retry engine, and state machine.
"""

from .models import (
    AgentMessage,
    AgentRole,
    ChecklistItemResult,
    CoderOutput,
    CriticOutput,
    FeedbackLoopResult,
    FileAction,
    FileOperation,
    HarnessConfig,
    OverallVerdict,
    SeverityLevel,
    StepHistory,
    StepState,
    StrictBaseModel,
)
from .parser import (
    JsonExtractionError,
    SchemaValidationError,
    extract_balanced_json,
    extract_json_string,
    format_validation_errors,
    parse_and_validate,
    sanitize_json_text,
    strip_markdown_fences,
)
from .client import (
    BaseLLMClient,
    GenAIClient,
    LLMClientError,
    LLMClientFactory,
    MockLLMClient,
)
from .retry import (
    ReflectionRetryEngine,
    RetryAttemptRecord,
    format_reflection_prompt,
)
from .fsm import (
    FSMTransitionError,
    HarnessFSM,
)

__all__ = [
    # Models
    "StrictBaseModel",
    "FileAction",
    "FileOperation",
    "CoderOutput",
    "SeverityLevel",
    "OverallVerdict",
    "ChecklistItemResult",
    "CriticOutput",
    "AgentRole",
    "AgentMessage",
    "StepState",
    "StepHistory",
    "FeedbackLoopResult",
    "HarnessConfig",
    # Parser
    "JsonExtractionError",
    "SchemaValidationError",
    "strip_markdown_fences",
    "extract_balanced_json",
    "sanitize_json_text",
    "extract_json_string",
    "format_validation_errors",
    "parse_and_validate",
    # Client
    "BaseLLMClient",
    "MockLLMClient",
    "GenAIClient",
    "LLMClientFactory",
    "LLMClientError",
    # Retry
    "RetryAttemptRecord",
    "format_reflection_prompt",
    "ReflectionRetryEngine",
    # FSM
    "HarnessFSM",
    "FSMTransitionError",
]
