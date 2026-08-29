"""
Custom Harness Guardrails Subsystem
===================================
Declarative Safety & Fail-Closed Guardrail Policies for Antigravity Python Harness.
"""

from .exceptions import (
    AntiLazinessViolationError,
    FailClosedAbortError,
    GuardrailError,
    GuardrailExecutionError,
    PathTraversalError,
    PathTraversalSecurityError,
    PayloadSizeLimitError,
    PolicyViolationError,
    ProtectedPathAccessError,
    SecurityDenialError,
    SyntaxPreValidationError,
)
from .models import (
    ActionType,
    FileOperation,
    GuardrailContext,
    GuardrailPolicy,
    PathRule,
    PolicyRule,
    RuleSeverity,
    SecurityViolationRecord,
    ValidationResult,
)
from .path_validator import (
    PathBoundaryValidator,
    PathValidator,
)
from .rule_loader import (
    RuleEnforcer,
    RuleLoader,
)
from .engine import (
    GuardrailEngine,
    PolicyEngine,
)

__all__ = [
    # Exceptions
    "GuardrailError",
    "GuardrailExecutionError",
    "FailClosedAbortError",
    "PolicyViolationError",
    "SecurityDenialError",
    "PathTraversalError",
    "PathTraversalSecurityError",
    "ProtectedPathAccessError",
    "AntiLazinessViolationError",
    "SyntaxPreValidationError",
    "PayloadSizeLimitError",
    # Models
    "ActionType",
    "RuleSeverity",
    "PolicyRule",
    "PathRule",
    "GuardrailPolicy",
    "FileOperation",
    "GuardrailContext",
    "SecurityViolationRecord",
    "ValidationResult",
    # Validators & Loaders
    "PathBoundaryValidator",
    "PathValidator",
    "RuleLoader",
    "RuleEnforcer",
    # Engines
    "GuardrailEngine",
    "PolicyEngine",
]
