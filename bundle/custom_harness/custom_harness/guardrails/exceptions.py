"""
Custom Harness Guardrails Exceptions
====================================
Typed security, policy, and guardrail exceptions for deterministic runtime enforcement.
Implements Fail-Closed behavior for all security boundaries.
"""

from typing import Any, Dict, Optional


class GuardrailError(Exception):
    """Base exception for all harness security, guardrail, and policy violations."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        if self.details:
            return f"{self.message} | Details: {self.details}"
        return self.message


class GuardrailExecutionError(GuardrailError):
    """Raised when an internal error or execution fault occurs during policy enforcement."""
    pass


class FailClosedAbortError(GuardrailExecutionError):
    """
    Raised when policy evaluation encounters an ambiguous, corrupted, or unhandled state.
    Strictly aborts execution to prevent any permissive fail-open bypass.
    """
    pass


class PolicyViolationError(GuardrailError):
    """Raised when a declarative policy rule or guideline is violated."""
    pass


class SecurityDenialError(PolicyViolationError):
    """Raised when a core security boundary or sandbox restriction is violated."""
    pass


class PathTraversalError(SecurityDenialError):
    """Raised when an operation attempts to escape the allowed workspace sandbox boundaries."""
    pass


# Backward compatibility and blueprint alias
PathTraversalSecurityError = PathTraversalError


class ProtectedPathAccessError(SecurityDenialError):
    """Raised when an operation targets protected or sensitive files (e.g. .git, .env, .pem, .antigravityrules)."""
    pass


class AntiLazinessViolationError(PolicyViolationError):
    """
    Raised when generated or written code contains placeholder patterns,
    incomplete stubs, or lazy implementations (e.g. TODO, FIXME, ellipsis, pass # implement).
    """
    pass


class SyntaxPreValidationError(PolicyViolationError):
    """Raised when generated code fails syntax validation (e.g. Python AST or JSON parse) prior to writing."""
    pass


class PayloadSizeLimitError(PolicyViolationError):
    """Raised when a file operation content payload exceeds the configured maximum byte limit."""
    pass
