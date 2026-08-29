"""
Custom Harness Reflection Auto-Retry Engine
===========================================
Structured error reflection loop for Gemini Flash models.
Injects exact Pydantic V2 validation error locations, error types, and schema definitions
into reflection prompts to guide the model to self-correct invalid outputs.
"""

import json
from typing import Any, Dict, List, Optional, Tuple, Type, TypeVar
from pydantic import BaseModel
from .client import BaseLLMClient
from .parser import (
    JsonExtractionError,
    SchemaValidationError,
    extract_json_string,
    parse_and_validate,
)

T = TypeVar("T", bound=BaseModel)


class RetryAttemptRecord:
    """Record of a single validation & reflection retry attempt."""

    def __init__(
        self,
        attempt: int,
        raw_output: str,
        extracted_json: Optional[str] = None,
        errors: Optional[List[Dict[str, Any]]] = None,
        success: bool = False,
        error_message: Optional[str] = None
    ):
        self.attempt = attempt
        self.raw_output = raw_output
        self.extracted_json = extracted_json
        self.errors = errors or []
        self.success = success
        self.error_message = error_message

    def to_dict(self) -> Dict[str, Any]:
        return {
            "attempt": self.attempt,
            "success": self.success,
            "errors": self.errors,
            "error_message": self.error_message,
            "raw_output_length": len(self.raw_output) if self.raw_output else 0,
        }


def format_reflection_prompt(
    original_prompt: str,
    raw_output: str,
    errors: Optional[List[Dict[str, Any]]],
    target_schema_cls: Type[BaseModel],
    json_error_msg: Optional[str] = None
) -> str:
    """
    Construct a structured reflection prompt informing the model of exact validation errors.
    """
    schema_json = json.dumps(target_schema_cls.model_json_schema(), indent=2)

    error_bullets = []
    if errors:
        for err in errors:
            loc = err.get("loc", "root")
            msg = err.get("msg", "Invalid value")
            err_type = err.get("type", "")
            inp = err.get("input", "")
            error_bullets.append(f"- Field '{loc}': {msg} (type={err_type}, received={inp!r})")
    elif json_error_msg:
        error_bullets.append(f"- JSON Parsing Error: {json_error_msg}")
    else:
        error_bullets.append("- Unspecified format error.")

    errors_text = "\n".join(error_bullets)
    output_snippet = raw_output[:500] + ("..." if len(raw_output) > 500 else "")

    reflection_prompt = f"""[SYSTEM REFLECTION NOTICE - SCHEMA VALIDATION FAILED]

Your previous response failed validation against the required Pydantic V2 schema '{target_schema_cls.__name__}'.

Specific Errors Encountered:
{errors_text}

Previous Output Snippet:
```
{output_snippet}
```

Required JSON Schema ({target_schema_cls.__name__}):
```json
{schema_json}
```

Instructions for Correction:
1. Fix all listed validation errors. Ensure all required fields are present and data types strictly match.
2. Do NOT add any extra fields not defined in the schema (extra="forbid").
3. Return ONLY a valid JSON object matching the schema above. Do not include introductory or concluding conversational prose.

Original Task Request:
{original_prompt}
"""
    return reflection_prompt


class ReflectionRetryEngine:
    """
    Executes model generation with automatic reflection retry on schema failure.
    """

    def __init__(self, default_max_retries: int = 3):
        self.default_max_retries = default_max_retries

    def execute_with_reflection(
        self,
        client: BaseLLMClient,
        prompt: str,
        model_cls: Type[T],
        system_instruction: Optional[str] = None,
        max_retries: Optional[int] = None,
        temperature: float = 0.2,
        top_p: float = 0.95,
        **kwargs
    ) -> Tuple[T, List[RetryAttemptRecord]]:
        """
        Invoke client and validate against model_cls. On failure, construct a reflection
        prompt and retry up to max_retries times.
        Returns: (parsed_model_instance, attempt_history_records)
        """
        retries = max_retries if max_retries is not None else self.default_max_retries
        history: List[RetryAttemptRecord] = []
        current_prompt = prompt

        for attempt in range(1, retries + 2):  # initial attempt + retries
            try:
                raw_output = client.generate(
                    prompt=current_prompt,
                    system_instruction=system_instruction,
                    response_schema=model_cls,
                    temperature=temperature,
                    top_p=top_p,
                    **kwargs
                )
            except Exception as exc:
                record = RetryAttemptRecord(
                    attempt=attempt,
                    raw_output="",
                    success=False,
                    error_message=f"Client generation exception: {exc}"
                )
                history.append(record)
                if attempt > retries:
                    raise SchemaValidationError(
                        f"LLM call failed after {attempt} attempts: {exc}",
                        errors=[{"loc": "client", "msg": str(exc), "type": "client_error"}],
                        raw_json=""
                    ) from exc
                # Try again
                continue

            # Attempt extraction and validation
            try:
                instance, errors, extracted_json = parse_and_validate(raw_output, model_cls)
                if instance is not None:
                    # Succeeded!
                    record = RetryAttemptRecord(
                        attempt=attempt,
                        raw_output=raw_output,
                        extracted_json=extracted_json,
                        errors=[],
                        success=True
                    )
                    history.append(record)
                    return instance, history
                else:
                    # Pydantic validation error
                    record = RetryAttemptRecord(
                        attempt=attempt,
                        raw_output=raw_output,
                        extracted_json=extracted_json,
                        errors=errors,
                        success=False,
                        error_message="Schema validation error"
                    )
                    history.append(record)

                    if attempt > retries:
                        raise SchemaValidationError(
                            f"Model failed schema validation after {attempt} attempts.",
                            errors=errors or [],
                            raw_json=extracted_json
                        )

                    # Prepare reflection prompt for next attempt
                    current_prompt = format_reflection_prompt(
                        original_prompt=prompt,
                        raw_output=raw_output,
                        errors=errors,
                        target_schema_cls=model_cls
                    )
            except JsonExtractionError as exc:
                record = RetryAttemptRecord(
                    attempt=attempt,
                    raw_output=raw_output,
                    success=False,
                    error_message=str(exc)
                )
                history.append(record)

                if attempt > retries:
                    raise SchemaValidationError(
                        f"Model failed to return valid JSON after {attempt} attempts: {exc}",
                        errors=[{"loc": "json_parser", "msg": str(exc), "type": "json_parse_error"}],
                        raw_json=raw_output
                    ) from exc

                # Prepare reflection prompt for JSON error
                current_prompt = format_reflection_prompt(
                    original_prompt=prompt,
                    raw_output=raw_output,
                    errors=None,
                    target_schema_cls=model_cls,
                    json_error_msg=str(exc)
                )

        raise SchemaValidationError(
            f"Exceeded max retries ({retries}) without valid response.",
            errors=[],
            raw_json=""
        )
