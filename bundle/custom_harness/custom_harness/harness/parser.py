"""
Custom Harness Multi-Tier JSON Parser & Schema Validator
========================================================
Robust JSON extractor and Pydantic V2 validator with markdown fence stripping,
bracket-matching substring extraction, formatting sanitization, and structured error reporting.
"""

import json
import re
from typing import Any, Dict, List, Optional, Tuple, Type, TypeVar
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class JsonExtractionError(Exception):
    """Raised when no valid JSON structure can be extracted from model text."""
    pass


class SchemaValidationError(Exception):
    """Raised when extracted JSON fails Pydantic schema validation."""
    def __init__(self, message: str, errors: List[Dict[str, Any]], raw_json: str):
        super().__init__(message)
        self.errors = errors
        self.raw_json = raw_json


def strip_markdown_fences(text: str) -> str:
    """Strip markdown code block fences (```json ... ``` or ``` ... ```)."""
    text = text.strip()
    # Match standard markdown block with optional language tag
    pattern = r"^```(?:json|JSON)?\s*\n?(.*?)\n?```$"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()

    # Also handle multiple code blocks by taking the largest valid block
    blocks = re.findall(r"```(?:json|JSON)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if blocks:
        # Return the longest block
        return max(blocks, key=len).strip()

    return text


def extract_balanced_json(text: str) -> str:
    """
    Extract the first balanced JSON object {...} or array [...] from text
    using stack-based bracket counting.
    """
    text = text.strip()

    # Find the first opening brace or bracket
    first_brace = text.find("{")
    first_bracket = text.find("[")

    start_char = None
    end_char = None
    start_pos = -1

    if first_brace != -1 and (first_bracket == -1 or first_brace < first_bracket):
        start_char = "{"
        end_char = "}"
        start_pos = first_brace
    elif first_bracket != -1:
        start_char = "["
        end_char = "]"
        start_pos = first_bracket
    else:
        return text

    depth = 0
    in_string = False
    escape = False

    for i in range(start_pos, len(text)):
        char = text[i]

        if escape:
            escape = False
            continue

        if char == "\\":
            escape = True
            continue

        if char == '"':
            in_string = not in_string
            continue

        if not in_string:
            if char == start_char:
                depth += 1
            elif char == end_char:
                depth -= 1
                if depth == 0:
                    return text[start_pos:i + 1].strip()

    return text[start_pos:].strip()


def sanitize_json_text(text: str) -> str:
    """
    Apply safe heuristic sanitization for common JSON syntax glitches:
    - Remove trailing commas before closing braces/brackets
    - Normalize control characters
    """
    # Remove trailing commas before } or ]
    text = re.sub(r",\s*(\}|\])", r"\1", text)
    return text


def extract_json_string(raw_text: str) -> str:
    """
    Multi-tier extraction of clean JSON string from raw LLM output.
    Tier 1: Strip markdown fences.
    Tier 2: Extract balanced JSON substring.
    Tier 3: Apply syntax sanitization.
    """
    if not raw_text or not raw_text.strip():
        raise JsonExtractionError("Raw output is empty or whitespace only.")

    # Tier 1
    t1 = strip_markdown_fences(raw_text)

    # Try parsing directly
    try:
        json.loads(t1)
        return t1
    except json.JSONDecodeError:
        pass

    # Tier 2: Balanced bracket search on stripped text
    t2 = extract_balanced_json(t1)
    try:
        json.loads(t2)
        return t2
    except json.JSONDecodeError:
        pass

    # Balanced bracket search on original text
    t2_orig = extract_balanced_json(raw_text)
    try:
        json.loads(t2_orig)
        return t2_orig
    except json.JSONDecodeError:
        pass

    # Tier 3: Apply sanitization
    t3 = sanitize_json_text(t2)
    try:
        json.loads(t3)
        return t3
    except json.JSONDecodeError:
        pass

    t3_orig = sanitize_json_text(t2_orig)
    try:
        json.loads(t3_orig)
        return t3_orig
    except json.JSONDecodeError as exc:
        raise JsonExtractionError(
            f"Failed to parse valid JSON from LLM output: {exc}. Extracted preview: {t3[:200]!r}"
        ) from exc


def format_validation_errors(exc: ValidationError) -> List[Dict[str, Any]]:
    """Format Pydantic ValidationError into structured diagnostic list."""
    formatted = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", []))
        formatted.append({
            "loc": loc,
            "msg": err.get("msg", ""),
            "type": err.get("type", ""),
            "input": str(err.get("input", ""))[:100],
        })
    return formatted


def parse_and_validate(
    raw_text: str,
    model_cls: Type[T]
) -> Tuple[Optional[T], Optional[List[Dict[str, Any]]], str]:
    """
    Extract JSON and validate against a target Pydantic V2 model.
    Returns: (parsed_instance, validation_errors_list, extracted_json_string)
    - If valid: (instance, None, raw_json)
    - If invalid schema: (None, errors_list, raw_json)
    - If JSON parse fails: raises JsonExtractionError
    """
    clean_json = extract_json_string(raw_text)

    try:
        instance = model_cls.model_validate_json(clean_json)
        return instance, None, clean_json
    except ValidationError as exc:
        errors = format_validation_errors(exc)
        return None, errors, clean_json
