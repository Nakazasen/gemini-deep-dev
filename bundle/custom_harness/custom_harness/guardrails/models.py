"""
Custom Harness Guardrails Data Models
=====================================
Pydantic V2 structured models for declarative security policies, operations, and audit records.
"""

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field, field_validator, model_validator


class ActionType(str, Enum):
    """Types of actions intercepted by the guardrail engine."""
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    DELETE = "delete"
    MKDIR = "mkdir"
    LIST = "list"
    MOVE = "move"


class RuleSeverity(str, Enum):
    """Enforcement severity for policy rules."""
    BLOCK = "block"   # Hard denial: aborts operation and raises exception
    WARN = "warn"     # Logs warning, allows operation if non-fatal
    AUDIT = "audit"   # Informational audit log only


class PolicyRule(BaseModel):
    """Declarative definition of a custom guardrail rule."""
    rule_id: str = Field(..., description="Unique rule identifier")
    name: str = Field(..., description="Human-readable rule name")
    description: str = Field(default="", description="Detailed explanation of what the rule enforces")
    pattern: Optional[str] = Field(default=None, description="Glob or regex pattern for matching paths or content")
    action_types: List[ActionType] = Field(
        default_factory=lambda: [ActionType.READ, ActionType.WRITE, ActionType.DELETE, ActionType.EXECUTE],
        description="Action types this rule applies to"
    )
    severity: RuleSeverity = Field(default=RuleSeverity.BLOCK, description="Enforcement severity level")
    enabled: bool = Field(default=True, description="Whether this rule is currently active")
    custom_validator: Optional[str] = Field(default=None, description="Optional name of custom validation hook")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional custom rule metadata")


class PathRule(BaseModel):
    """Specific rule matching paths for allow/deny enforcement."""
    path_pattern: str = Field(..., description="Glob or regex pattern for path matching")
    is_denied: bool = Field(default=True, description="Whether matching paths are denied (True) or allowed (False)")
    allowed_actions: List[ActionType] = Field(
        default_factory=list,
        description="Actions permitted on this path if is_denied is False"
    )
    reason: Optional[str] = Field(default=None, description="Reason for policy constraint")


class GuardrailPolicy(BaseModel):
    """Central declarative security and guardrail policy configuration."""
    policy_version: str = Field(default="1.0.0", description="Policy schema version")
    fail_closed: bool = Field(default=True, description="Strict fail-closed default deny enforcement")
    workspace_root: Path = Field(
        default_factory=Path.cwd,
        description="Primary workspace canonical root directory"
    )
    allowed_read_roots: List[Path] = Field(
        default_factory=list,
        description="Allowed root directory paths for read operations (defaults to [workspace_root])"
    )
    allowed_write_roots: List[Path] = Field(
        default_factory=list,
        description="Allowed root directory paths for write/modify operations (defaults to [workspace_root])"
    )
    denied_path_patterns: List[str] = Field(
        default_factory=lambda: [
            "**/.git/**", "**/.git",
            "**/.antigravity/**", "**/.antigravity",
            "**/.gemini/**", "**/.gemini",
            "**/.env*", "**/credentials.json",
            "**/*.key", "**/*.pem", "**/*.pfx", "**/id_rsa*", "**/id_ed25519*",
            "**/__pycache__/**", "**/*.pyc",
            "**/*.exe", "**/*.dll", "**/*.so", "**/*.bat", "**/*.cmd", "**/*.ps1", "**/*.vbs",
            ".antigravityrules"
        ],
        description="Glob patterns permanently prohibited from modification"
    )
    allowed_extensions: Optional[List[str]] = Field(
        default=None,
        description="Whitelist of permitted file extensions for writing. None allows all except denied patterns."
    )
    allow_symlinks: bool = Field(
        default=False,
        description="Whether symlinks pointing outside workspace root are permitted"
    )
    anti_laziness_enabled: bool = Field(
        default=True,
        description="Strictly block code containing placeholder patterns (TODO, FIXME, ellipsis, etc.)"
    )
    forbidden_code_patterns: List[str] = Field(
        default_factory=lambda: [
            r"(?i)#\s*TODO\b",
            r"(?i)//\s*TODO\b",
            r"(?i)#\s*FIXME\b",
            r"(?i)//\s*FIXME\b",
            r"\.\.\.",
            r"(?i)pass\s*#\s*implement",
            r"(?i)#\s*rest of code",
            r"(?i)//\s*rest of code",
            r"(?i)raise\s+NotImplementedError"
        ],
        description="Regex patterns indicating incomplete or placeholder code"
    )
    syntax_validation_enabled: bool = Field(
        default=True,
        description="Pre-validate code syntax (Python AST parse, JSON parse) before disk writes"
    )
    max_file_size_bytes: int = Field(
        default=10 * 1024 * 1024,
        description="Maximum permitted write payload size in bytes (default 10 MB)"
    )
    require_doc_grounding: bool = Field(
        default=False,
        description="Require code modifications to cite verified doc hash grounding citations"
    )
    custom_rules: List[PolicyRule] = Field(
        default_factory=list,
        description="Custom declarative policy rules"
    )

    @field_validator("workspace_root", mode="after")
    @classmethod
    def canonicalize_workspace(cls, v: Union[str, Path]) -> Path:
        return Path(v).resolve()

    @model_validator(mode="after")
    def canonicalize_and_default_roots(self) -> "GuardrailPolicy":
        # Ensure roots are canonicalized
        ws = self.workspace_root.resolve()
        object.__setattr__(self, "workspace_root", ws)

        if not self.allowed_read_roots:
            object.__setattr__(self, "allowed_read_roots", [ws])
        else:
            object.__setattr__(self, "allowed_read_roots", [Path(p).resolve() for p in self.allowed_read_roots])

        if not self.allowed_write_roots:
            object.__setattr__(self, "allowed_write_roots", [ws])
        else:
            object.__setattr__(self, "allowed_write_roots", [Path(p).resolve() for p in self.allowed_write_roots])

        return self


class FileOperation(BaseModel):
    """Represents a filesystem operation to be evaluated and guarded."""
    target_path: Union[str, Path] = Field(..., description="Target file or directory path")
    action: ActionType = Field(default=ActionType.WRITE, description="Type of operation")
    content: Optional[str] = Field(default=None, description="File content payload for write operations")
    source_path: Optional[Union[str, Path]] = Field(default=None, description="Source path for move or copy operations")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Contextual operation metadata")


class GuardrailContext(BaseModel):
    """Contextual metadata accompanying an operation request."""
    session_id: Optional[str] = Field(default=None, description="Active session ID")
    agent_role: Optional[str] = Field(default=None, description="Role of requesting agent (coder, reviewer, user)")
    request_id: Optional[str] = Field(default=None, description="Unique request tracing ID")
    workspace_root: Optional[Path] = Field(default=None, description="Current workspace root override")
    doc_grounding_citations: List[str] = Field(
        default_factory=list,
        description="Doc hash citations accompanying the change"
    )
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary execution context parameters")


class SecurityViolationRecord(BaseModel):
    """Structured audit record for a detected security or policy violation."""
    violation_type: str = Field(..., description="Classification of violation (e.g. PATH_TRAVERSAL, ANTI_LAZINESS)")
    message: str = Field(..., description="Human-readable violation message")
    target_path: Optional[str] = Field(default=None, description="Target path involved in violation")
    action: Optional[ActionType] = Field(default=None, description="Action attempted")
    rule_id: Optional[str] = Field(default=None, description="ID of rule that triggered violation")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of the violation event"
    )
    severity: RuleSeverity = Field(default=RuleSeverity.BLOCK, description="Violation severity")
    details: Dict[str, Any] = Field(default_factory=dict, description="Detailed diagnostic context")


class ValidationResult(BaseModel):
    """Outcome of guardrail policy evaluation on an operation."""
    is_valid: bool = Field(..., description="Whether operation is permitted to proceed")
    resolved_path: Optional[Path] = Field(default=None, description="Safely resolved and canonicalized target path")
    action: ActionType = Field(..., description="Evaluated action type")
    violations: List[SecurityViolationRecord] = Field(
        default_factory=list,
        description="List of detected policy violations"
    )
    warnings: List[str] = Field(default_factory=list, description="Non-fatal warning messages")
    details: Dict[str, Any] = Field(default_factory=dict, description="Evaluation metadata")
