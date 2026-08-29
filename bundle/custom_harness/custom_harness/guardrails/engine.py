"""
Custom Harness Guardrail Engine
===============================
Central Fail-Closed policy engine that intercepts filesystem and runtime operations,
enforces declarative security boundaries, pre-validates syntax and completeness,
and maintains comprehensive audit logging.
"""

import ast
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .exceptions import (
    AntiLazinessViolationError,
    FailClosedAbortError,
    GuardrailError,
    PathTraversalError,
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
    RuleSeverity,
    SecurityViolationRecord,
    ValidationResult,
)
from .path_validator import PathBoundaryValidator
from .rule_loader import RuleLoader


class GuardrailEngine:
    """
    Central enforcement engine for declarative safety policies.
    Guarantees Fail-Closed security: any unhandled exception or ambiguity
    triggers an immediate denial and audit log entry.
    """

    def __init__(self, policy: Optional[GuardrailPolicy] = None, workspace_root: Optional[Union[str, Path]] = None):
        if policy is not None:
            self.policy = policy
        else:
            self.policy = RuleLoader.load_policy(workspace_root=workspace_root)

        self.path_validator = PathBoundaryValidator(self.policy)
        self._compiled_patterns = [
            re.compile(p) for p in self.policy.forbidden_code_patterns
        ]
        self._audit_log: List[SecurityViolationRecord] = []
        self._operation_history: List[Dict[str, Any]] = []

    @property
    def audit_log(self) -> List[SecurityViolationRecord]:
        """Returns read-only copy of security violation audit records."""
        return list(self._audit_log)

    @property
    def operation_history(self) -> List[Dict[str, Any]]:
        """Returns read-only history of evaluated operations."""
        return list(self._operation_history)

    def log_violation(
        self,
        violation_type: str,
        message: str,
        target_path: Optional[str] = None,
        action: Optional[ActionType] = None,
        rule_id: Optional[str] = None,
        severity: RuleSeverity = RuleSeverity.BLOCK,
        details: Optional[Dict[str, Any]] = None
    ) -> SecurityViolationRecord:
        """Records a security violation into the persistent audit trail."""
        record = SecurityViolationRecord(
            violation_type=violation_type,
            message=message,
            target_path=target_path,
            action=action,
            rule_id=rule_id,
            timestamp=datetime.now(timezone.utc),
            severity=severity,
            details=details or {}
        )
        self._audit_log.append(record)
        return record

    def validate_content(self, file_path: Path, content: str) -> None:
        """
        Validates content payload size, anti-laziness placeholder absence,
        and syntax validity (AST for Python, parse for JSON).
        """
        # 1. Check payload byte size limit
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > self.policy.max_file_size_bytes:
            err_msg = (
                f"File content size ({len(content_bytes)} bytes) exceeds configured maximum limit "
                f"({self.policy.max_file_size_bytes} bytes)."
            )
            self.log_violation(
                violation_type="PAYLOAD_SIZE_LIMIT",
                message=err_msg,
                target_path=str(file_path),
                action=ActionType.WRITE,
                details={"size_bytes": len(content_bytes), "max_allowed": self.policy.max_file_size_bytes}
            )
            raise PayloadSizeLimitError(err_msg)

        # 2. Check for anti-laziness placeholders
        if self.policy.anti_laziness_enabled:
            for pattern in self._compiled_patterns:
                match = pattern.search(content)
                if match:
                    matched_text = match.group(0)
                    err_msg = (
                        f"Anti-laziness violation in '{file_path.name}': Detected forbidden placeholder '{matched_text}'. "
                        "All generated code must be complete and fully implemented."
                    )
                    self.log_violation(
                        violation_type="ANTI_LAZINESS",
                        message=err_msg,
                        target_path=str(file_path),
                        action=ActionType.WRITE,
                        details={"placeholder": matched_text, "pattern": pattern.pattern}
                    )
                    raise AntiLazinessViolationError(err_msg, {"placeholder": matched_text, "path": str(file_path)})

        # 3. Syntax Pre-Validation
        if self.policy.syntax_validation_enabled:
            suffix = file_path.suffix.lower()
            if suffix == ".py":
                try:
                    ast.parse(content, filename=str(file_path))
                except SyntaxError as e:
                    err_msg = (
                        f"Python syntax error in generated code for '{file_path.name}' "
                        f"at line {e.lineno}, col {e.offset}: {e.msg}"
                    )
                    self.log_violation(
                        violation_type="SYNTAX_PRE_VALIDATION",
                        message=err_msg,
                        target_path=str(file_path),
                        action=ActionType.WRITE,
                        details={"line": e.lineno, "offset": e.offset, "syntax_error": e.msg}
                    )
                    raise SyntaxPreValidationError(
                        err_msg,
                        {"line": e.lineno, "offset": e.offset, "syntax_error": e.msg, "path": str(file_path)}
                    ) from e
            elif suffix == ".json":
                try:
                    json.loads(content)
                except json.JSONDecodeError as e:
                    err_msg = (
                        f"JSON syntax error in content for '{file_path.name}' "
                        f"at line {e.lineno}, col {e.colno}: {e.msg}"
                    )
                    self.log_violation(
                        violation_type="SYNTAX_PRE_VALIDATION",
                        message=err_msg,
                        target_path=str(file_path),
                        action=ActionType.WRITE,
                        details={"line": e.lineno, "col": e.colno, "json_error": e.msg}
                    )
                    raise SyntaxPreValidationError(
                        err_msg,
                        {"line": e.lineno, "col": e.colno, "json_error": e.msg, "path": str(file_path)}
                    ) from e

    def validate_operation(
        self,
        operation: FileOperation,
        context: Optional[GuardrailContext] = None
    ) -> ValidationResult:
        """
        Evaluates a complete FileOperation against all policy rules.
        Returns a ValidationResult object.
        """
        violations: List[SecurityViolationRecord] = []
        warnings: List[str] = []
        resolved_path: Optional[Path] = None
        initial_violations_count = len(self._audit_log)

        try:
            # 1. Path validation
            resolved_path = self.path_validator.validate_path(
                operation.target_path,
                mode=operation.action
            )

            # 2. Content validation if write operation with content
            if operation.action == ActionType.WRITE and operation.content is not None:
                self.validate_content(resolved_path, operation.content)

            # 3. Custom rules validation
            for rule in self.policy.custom_rules:
                if not rule.enabled:
                    continue
                if operation.action in rule.action_types and rule.pattern:
                    rel_str = str(resolved_path.as_posix())
                    if re.search(rule.pattern, rel_str):
                        if rule.severity == RuleSeverity.BLOCK:
                            v = self.log_violation(
                                violation_type="CUSTOM_RULE_VIOLATION",
                                message=f"Custom rule '{rule.name}' ({rule.rule_id}) blocked operation.",
                                target_path=str(resolved_path),
                                action=operation.action,
                                rule_id=rule.rule_id,
                                severity=rule.severity
                            )
                            violations.append(v)
                            raise PolicyViolationError(f"Operation blocked by rule: {rule.name}")
                        elif rule.severity == RuleSeverity.WARN:
                            warnings.append(f"Custom rule warning '{rule.name}': {rule.description}")

            # 4. Doc grounding requirement check
            if self.policy.require_doc_grounding and operation.action == ActionType.WRITE:
                if not context or not context.doc_grounding_citations:
                    v = self.log_violation(
                        violation_type="MISSING_DOC_GROUNDING",
                        message="Operation requires verified doc grounding citations before writing code.",
                        target_path=str(resolved_path),
                        action=operation.action
                    )
                    violations.append(v)
                    raise PolicyViolationError("Missing required doc grounding citations.")

            # Record successful evaluation in history
            self._operation_history.append({
                "action": operation.action.value,
                "target_path": str(resolved_path),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "ALLOWED",
                "warnings": warnings
            })

            return ValidationResult(
                is_valid=True,
                resolved_path=resolved_path,
                action=operation.action,
                violations=[],
                warnings=warnings,
                details={"status": "ALLOWED"}
            )

        except GuardrailError as ge:
            # Ensure violation is recorded in audit_log
            if len(self._audit_log) == initial_violations_count:
                if isinstance(ge, ProtectedPathAccessError):
                    v_type = "PROTECTED_PATH"
                elif isinstance(ge, PathTraversalError):
                    v_type = "PATH_TRAVERSAL"
                elif isinstance(ge, AntiLazinessViolationError):
                    v_type = "ANTI_LAZINESS"
                elif isinstance(ge, SyntaxPreValidationError):
                    v_type = "SYNTAX_PRE_VALIDATION"
                elif isinstance(ge, PayloadSizeLimitError):
                    v_type = "PAYLOAD_SIZE_LIMIT"
                else:
                    v_type = getattr(ge, "violation_type", None) or ge.__class__.__name__

                v_rec = self.log_violation(
                    violation_type=v_type,
                    message=ge.message if hasattr(ge, "message") else str(ge),
                    target_path=str(operation.target_path),
                    action=operation.action,
                    details=getattr(ge, "details", {}),
                )
                violations.append(v_rec)

            # Re-raise if fail_closed is True or return failed ValidationResult
            self._operation_history.append({
                "action": operation.action.value,
                "target_path": str(operation.target_path),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "DENIED",
                "error": str(ge)
            })
            if self.policy.fail_closed:
                raise
            return ValidationResult(
                is_valid=False,
                resolved_path=resolved_path,
                action=operation.action,
                violations=violations if violations else (self.audit_log[-1:] if self.audit_log else []),
                warnings=warnings,
                details={"status": "DENIED", "error": str(ge)}
            )
        except Exception as e:
            # Unhandled exceptions trigger Fail-Closed abort
            self.log_violation(
                violation_type="FAIL_CLOSED_ABORT",
                message=f"Unhandled internal error during guardrail validation: {e}",
                target_path=str(operation.target_path),
                action=operation.action,
                details={"exception": str(e)}
            )
            self._operation_history.append({
                "action": operation.action.value,
                "target_path": str(operation.target_path),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "ABORTED_FAIL_CLOSED",
                "error": str(e)
            })
            raise FailClosedAbortError(
                f"Fail-Closed security trigger: Unhandled exception during policy validation: {e}"
            ) from e

    def validate_file_write(
        self,
        path: Union[str, Path],
        content: str,
        context: Optional[GuardrailContext] = None
    ) -> Path:
        """Validates a file write operation and returns the canonical destination Path."""
        op = FileOperation(
            target_path=path,
            action=ActionType.WRITE,
            content=content
        )
        result = self.validate_operation(op, context=context)
        if not result.is_valid or result.resolved_path is None:
            raise SecurityDenialError(f"File write operation to '{path}' was denied.")
        return result.resolved_path

    def validate_file_read(
        self,
        path: Union[str, Path],
        context: Optional[GuardrailContext] = None
    ) -> Path:
        """Validates a file read operation and returns the canonical target Path."""
        op = FileOperation(
            target_path=path,
            action=ActionType.READ
        )
        result = self.validate_operation(op, context=context)
        if not result.is_valid or result.resolved_path is None:
            raise SecurityDenialError(f"File read operation for '{path}' was denied.")
        return result.resolved_path

    def execute_guarded_write(
        self,
        path: Union[str, Path],
        content: str,
        context: Optional[GuardrailContext] = None,
        encoding: str = "utf-8"
    ) -> Path:
        """
        Executes a safe, fully validated atomic file write operation.
        Guarantees that path boundaries, anti-laziness, and syntax rules pass before modifying disk.
        """
        resolved_path = self.validate_file_write(path=path, content=content, context=context)

        # Ensure parent directories exist
        resolved_path.parent.mkdir(parents=True, exist_ok=True)

        # Write content safely
        resolved_path.write_text(content, encoding=encoding)
        return resolved_path

    def execute_guarded_read(
        self,
        path: Union[str, Path],
        context: Optional[GuardrailContext] = None,
        encoding: str = "utf-8"
    ) -> str:
        """Executes a validated file read operation."""
        resolved_path = self.validate_file_read(path=path, context=context)
        return resolved_path.read_text(encoding=encoding)

    def execute_guarded_delete(
        self,
        path: Union[str, Path],
        context: Optional[GuardrailContext] = None
    ) -> bool:
        """Executes a validated file deletion operation."""
        op = FileOperation(target_path=path, action=ActionType.DELETE)
        result = self.validate_operation(op, context=context)
        if not result.is_valid or result.resolved_path is None:
            raise SecurityDenialError(f"Delete operation on '{path}' was denied.")

        if result.resolved_path.is_file():
            result.resolved_path.unlink()
            return True
        elif result.resolved_path.is_dir():
            result.resolved_path.rmdir()
            return True
        return False

    def execute_guarded_mkdir(
        self,
        path: Union[str, Path],
        context: Optional[GuardrailContext] = None,
        exist_ok: bool = True
    ) -> Path:
        """Executes a validated directory creation operation."""
        op = FileOperation(target_path=path, action=ActionType.MKDIR)
        result = self.validate_operation(op, context=context)
        if not result.is_valid or result.resolved_path is None:
            raise SecurityDenialError(f"Mkdir operation on '{path}' was denied.")

        result.resolved_path.mkdir(parents=True, exist_ok=exist_ok)
        return result.resolved_path

    def clear_audit_log(self) -> None:
        """Clears in-memory audit logs and operation history."""
        self._audit_log.clear()
        self._operation_history.clear()

    def export_audit_report(self) -> Dict[str, Any]:
        """Exports a structured report of all violations and evaluated operations."""
        return {
            "total_operations": len(self._operation_history),
            "total_violations": len(self._audit_log),
            "violations": [v.model_dump(mode="json") for v in self._audit_log],
            "operation_history": list(self._operation_history)
        }


# Blueprint and backward-compatibility alias
PolicyEngine = GuardrailEngine
