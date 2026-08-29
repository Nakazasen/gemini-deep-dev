"""
Custom Harness Pydantic V2 Strict Models
========================================
Pydantic V2 strictly validated and immutable models for deterministic Gemini Flash agent harness.
All models enforce extra="forbid" and frozen=True to guarantee schema conformance.
"""

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictBaseModel(BaseModel):
    """Base model enforcing strict Pydantic V2 schema compliance."""
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_assignment=True,
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class FileAction(str, Enum):
    """File operation action types."""
    CREATE = "create"
    MODIFY = "modify"
    DELETE = "delete"
    NOOP = "noop"


class FileOperation(StrictBaseModel):
    """Discrete file operation proposed by CoderSubAgent."""
    file_path: str = Field(..., description="Target relative file path within permitted boundary")
    action: FileAction = Field(..., description="Action to perform on target file")
    content_or_diff: str = Field(..., description="Full target file content or replacement content")
    description: str = Field(..., description="Purpose and justification of this change")
    start_line: Optional[int] = Field(default=None, description="Optional starting line for partial replacements")
    end_line: Optional[int] = Field(default=None, description="Optional ending line for partial replacements")


class CoderOutput(StrictBaseModel):
    """Strict structured output model for CoderSubAgent."""
    thought_process: str = Field(..., description="Internal engineering reasoning, architectural choices, and trade-offs")
    grounding_references: List[str] = Field(default_factory=list, description="Verified documentation URLs, hashes, or file references")
    plan_steps: List[str] = Field(..., min_length=1, description="Step-by-step execution plan")
    file_operations: List[FileOperation] = Field(default_factory=list, description="List of discrete file operations")
    verification_commands: List[str] = Field(default_factory=list, description="Commands to build, lint, and test changes")
    risk_assessment: str = Field(default="", description="Analysis of potential side-effects or regressions")


class SeverityLevel(str, Enum):
    """Checklist item failure severity."""
    CRITICAL = "CRITICAL"
    MAJOR = "MAJOR"
    MINOR = "MINOR"
    INFO = "INFO"


class OverallVerdict(str, Enum):
    """CriticSubAgent review verdict."""
    APPROVED = "APPROVED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    REJECTED_UNSAFE = "REJECTED_UNSAFE"


class ChecklistItemResult(StrictBaseModel):
    """Evaluation result for a specific checklist item."""
    criterion_id: str = Field(..., description="Unique criterion identifier")
    description: str = Field(..., description="Requirement description")
    passed: bool = Field(..., description="True if requirement is met, False otherwise")
    severity_if_failed: SeverityLevel = Field(default=SeverityLevel.MAJOR, description="Failure impact level")
    evidence: str = Field(..., description="Specific observation or code excerpt supporting verdict")


class CriticOutput(StrictBaseModel):
    """Strict structured output model for CriticSubAgent."""
    thought_process: str = Field(..., description="Analytical review reasoning and defect identification")
    checklist_results: List[ChecklistItemResult] = Field(..., min_length=1, description="Checklist evaluation results")
    guardrail_compliance: bool = Field(..., description="True if no safety policies or boundaries are violated")
    logic_review_summary: str = Field(..., description="Summary of algorithmic correctness, edge cases, and code quality")
    overall_verdict: OverallVerdict = Field(..., description="Final review verdict")
    quality_score: float = Field(..., ge=0.0, le=1.0, description="Normalized quality score between 0.0 and 1.0")
    actionable_feedback: List[str] = Field(default_factory=list, description="Concrete instructions for Coder to fix issues")


class AgentRole(str, Enum):
    """Sub-agent role classification."""
    CODER = "coder"
    CRITIC = "critic"
    SYSTEM = "system"
    USER = "user"


class AgentMessage(StrictBaseModel):
    """Isolated agent message record."""
    role: AgentRole = Field(..., description="Message sender role")
    content: str = Field(..., description="Message text content")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Message UTC timestamp"
    )
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Contextual message metadata")


class StepState(str, Enum):
    """FSM execution step states."""
    INIT = "INIT"
    CODER_GENERATE = "CODER_GENERATE"
    PARSE_CODER_SCHEMA = "PARSE_CODER_SCHEMA"
    GUARDRAIL_CHECK = "GUARDRAIL_CHECK"
    GUARDRAIL_BLOCKED = "GUARDRAIL_BLOCKED"
    CRITIC_REVIEW = "CRITIC_REVIEW"
    PARSE_CRITIC_SCHEMA = "PARSE_CRITIC_SCHEMA"
    EVALUATE_VERDICT = "EVALUATE_VERDICT"
    CONVERGED = "CONVERGED"
    MAX_TURNS = "MAX_TURNS"
    SCHEMA_FAILURE = "SCHEMA_FAILURE"
    ERROR = "ERROR"


class StepHistory(StrictBaseModel):
    """Historical record for an iteration step in the feedback loop."""
    iteration: int = Field(..., ge=1, description="1-indexed iteration number")
    state: StepState = Field(..., description="State during this step")
    coder_output: Optional[CoderOutput] = Field(default=None, description="Coder output for this step")
    critic_output: Optional[CriticOutput] = Field(default=None, description="Critic output for this step")
    guardrail_violations: List[str] = Field(default_factory=list, description="Violations encountered")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Step execution timestamp"
    )
    error_message: Optional[str] = Field(default=None, description="Error message if step failed")


class FeedbackLoopResult(StrictBaseModel):
    """Final outcome of deterministic dual-agent feedback loop."""
    success: bool = Field(..., description="True if loop converged with approved verdict")
    final_verdict: OverallVerdict = Field(..., description="Final overall review verdict")
    iterations_count: int = Field(..., ge=0, description="Total iterations executed")
    terminal_state: StepState = Field(..., description="Final FSM state")
    final_coder_output: Optional[CoderOutput] = Field(default=None, description="Approved coder output")
    final_critic_output: Optional[CriticOutput] = Field(default=None, description="Final critic evaluation")
    history: List[StepHistory] = Field(default_factory=list, description="Complete iteration history")
    guardrail_violations: List[str] = Field(default_factory=list, description="Any safety violations logged")
    error: Optional[str] = Field(default=None, description="Error message if loop terminated abnormally")


class HarnessConfig(StrictBaseModel):
    """Configuration settings for deterministic agent harness."""
    model_name: str = Field(default="gemini-2.5-flash", description="Target Gemini model identifier")
    max_turns: int = Field(default=5, ge=1, le=20, description="Maximum review-fix loop iterations")
    max_retries: int = Field(default=3, ge=0, le=10, description="Maximum schema reflection retries per turn")
    quality_threshold: float = Field(default=0.8, ge=0.0, le=1.0, description="Minimum quality score required for approval")
    temperature: float = Field(default=0.2, ge=0.0, le=2.0, description="LLM generation temperature")
    top_p: float = Field(default=0.95, ge=0.0, le=1.0, description="LLM generation top_p")
    enable_guardrails: bool = Field(default=True, description="Enable declarative safety guardrails")
    enable_grounding: bool = Field(default=True, description="Enable documentation grounding context injection")
    timeout_seconds: float = Field(default=60.0, gt=0.0, description="Per-request execution timeout")
    workspace_root: Optional[Path] = Field(default=None, description="Workspace root directory path")

    @field_validator("workspace_root", mode="before")
    @classmethod
    def resolve_workspace_root(cls, v: Any) -> Optional[Path]:
        if v is not None:
            return Path(v).resolve()
        return None
