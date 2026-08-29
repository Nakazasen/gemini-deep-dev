"""
Custom Harness LLM Client Adapter Layer
=======================================
Unified client interface supporting Google GenAI SDK (`google.genai`),
`google.generativeai`, and a deterministic `MockLLMClient` for offline verification.
"""

from abc import ABC, abstractmethod
import json
import os
from typing import Any, Callable, Dict, List, Optional, Type, TypeVar
from pydantic import BaseModel
from .parser import extract_json_string, parse_and_validate

T = TypeVar("T", bound=BaseModel)


class LLMClientError(Exception):
    """Base exception for LLM client communication errors."""
    pass


class BaseLLMClient(ABC):
    """Abstract Base Class for LLM Client adapters."""

    def __init__(self, model_name: str = "gemini-2.5-flash", **kwargs):
        self.model_name = model_name
        self.config_kwargs = kwargs

    @abstractmethod
    def generate(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        response_schema: Optional[Type[BaseModel]] = None,
        temperature: float = 0.2,
        top_p: float = 0.95,
        **kwargs
    ) -> str:
        """Generate raw text response from the model."""
        pass

    def generate_structured(
        self,
        prompt: str,
        response_model: Type[T],
        system_instruction: Optional[str] = None,
        temperature: float = 0.2,
        top_p: float = 0.95,
        **kwargs
    ) -> T:
        """Generate and strictly parse structured response into response_model."""
        raw_text = self.generate(
            prompt=prompt,
            system_instruction=system_instruction,
            response_schema=response_model,
            temperature=temperature,
            top_p=top_p,
            **kwargs
        )
        instance, errors, _ = parse_and_validate(raw_text, response_model)
        if instance is None:
            err_msg = "; ".join(f"{e['loc']}: {e['msg']}" for e in (errors or []))
            raise LLMClientError(f"Structured response failed validation: {err_msg}")
        return instance


class MockLLMClient(BaseLLMClient):
    """
    Deterministic Mock LLM Client for offline verification, simulation, and testing.
    Allows queueing specific responses or using custom response generator functions.
    """

    def __init__(
        self,
        model_name: str = "mock-gemini-flash",
        default_responses: Optional[List[str]] = None,
        response_map: Optional[Dict[str, str]] = None,
        custom_generator: Optional[Callable[[str, Optional[str]], str]] = None,
        **kwargs
    ):
        super().__init__(model_name=model_name, **kwargs)
        self._response_queue: List[str] = list(default_responses) if default_responses else []
        self._response_map: Dict[str, str] = dict(response_map) if response_map else {}
        self._custom_generator = custom_generator
        self.call_history: List[Dict[str, Any]] = []

    def queue_response(self, response: str) -> None:
        """Add a canned response to the front of the queue."""
        self._response_queue.append(response)

    def set_mapping(self, prompt_substring: str, response: str) -> None:
        """Map a prompt substring to a specific canned response."""
        self._response_map[prompt_substring] = response

    def generate(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        response_schema: Optional[Type[BaseModel]] = None,
        temperature: float = 0.2,
        top_p: float = 0.95,
        **kwargs
    ) -> str:
        """Return scripted response or deterministic schema-conforming mock output."""
        self.call_history.append({
            "prompt": prompt,
            "system_instruction": system_instruction,
            "response_schema": response_schema.__name__ if response_schema else None,
            "temperature": temperature,
            "top_p": top_p,
            "kwargs": kwargs
        })

        # 1. Check custom generator
        if self._custom_generator:
            return self._custom_generator(prompt, system_instruction)

        # 2. Check queued responses
        if self._response_queue:
            return self._response_queue.pop(0)

        # 3. Check mapped prompt substrings
        for key, resp in self._response_map.items():
            if key in prompt:
                return resp

        # 4. Generate deterministic default conforming JSON if schema is given
        if response_schema is not None:
            return self._generate_default_schema_json(response_schema)

        return json.dumps({"status": "ok", "message": "Mock default response."})

    def _generate_default_schema_json(self, schema_cls: Type[BaseModel]) -> str:
        """Generate valid JSON matching schema_cls field by field."""
        schema_name = schema_cls.__name__
        if schema_name == "CoderOutput":
            return json.dumps({
                "thought_process": "Mock deterministic analysis for code implementation.",
                "grounding_references": ["doc://antigravity/guide"],
                "plan_steps": ["1. Review specifications", "2. Implement changes", "3. Verify"],
                "file_operations": [
                    {
                        "file_path": "src/module.py",
                        "action": "create",
                        "content_or_diff": "# Deterministic implementation\ndef main():\n    return True\n",
                        "description": "Create primary implementation module"
                    }
                ],
                "verification_commands": ["pytest tests/"],
                "risk_assessment": "Low risk, isolated module."
            })
        elif schema_name == "CriticOutput":
            return json.dumps({
                "thought_process": "Mock deterministic review checking guardrails and requirements.",
                "checklist_results": [
                    {
                        "criterion_id": "REQ-01",
                        "description": "Functional correctness",
                        "passed": True,
                        "severity_if_failed": "CRITICAL",
                        "evidence": "Implementation meets specifications."
                    }
                ],
                "guardrail_compliance": True,
                "logic_review_summary": "Clean and correct implementation with no detected defects.",
                "overall_verdict": "APPROVED",
                "quality_score": 0.95,
                "actionable_feedback": []
            })

        # Generic minimal model construction
        sample_dict = {}
        for name, field_info in schema_cls.model_fields.items():
            if field_info.is_required():
                # Provide minimal default based on annotation
                sample_dict[name] = "mock_value"
        return json.dumps(sample_dict)


class GenAIClient(BaseLLMClient):
    """
    Live LLM client adapter for Google GenAI SDK (`google.genai` / `google.generativeai`).
    Automatically configures JSON schema output mode.
    """

    def __init__(
        self,
        model_name: str = "gemini-2.5-flash",
        api_key: Optional[str] = None,
        **kwargs
    ):
        super().__init__(model_name=model_name, **kwargs)
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self._client = None
        self._init_sdk()

    def _init_sdk(self) -> None:
        """Initialize underlying Google GenAI SDK client."""
        if not self.api_key:
            return  # Will raise error on actual generate call if key missing

        try:
            from google import genai
            self._client = genai.Client(api_key=self.api_key)
            self._sdk_type = "google.genai"
            return
        except (ImportError, Exception):
            pass

        try:
            import google.generativeai as legacy_genai
            legacy_genai.configure(api_key=self.api_key)
            self._client = legacy_genai
            self._sdk_type = "google.generativeai"
            return
        except (ImportError, Exception):
            pass

        self._sdk_type = "none"

    def generate(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        response_schema: Optional[Type[BaseModel]] = None,
        temperature: float = 0.2,
        top_p: float = 0.95,
        **kwargs
    ) -> str:
        """Generate response via live Google GenAI API."""
        if not self.api_key:
            raise LLMClientError(
                "No API key provided. Set GEMINI_API_KEY environment variable or pass api_key."
            )

        if self._sdk_type == "google.genai" and self._client:
            from google.genai import types
            config_args = {
                "temperature": temperature,
                "top_p": top_p,
            }
            if system_instruction:
                config_args["system_instruction"] = system_instruction
            if response_schema is not None:
                config_args["response_mime_type"] = "application/json"
                # Passing a Pydantic class through response_schema makes the SDK
                # serialize JSON Schema's additionalProperties as the unsupported
                # proto field additional_properties.  Send the generated JSON
                # Schema through the dedicated API field instead; local Pydantic
                # validation remains strict after generation.
                config_args["response_json_schema"] = response_schema.model_json_schema()

            config = types.GenerateContentConfig(**config_args)
            try:
                response = self._client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=config
                )
                return response.text or ""
            except Exception as exc:
                raise LLMClientError(f"GenAI API call failed: {exc}") from exc

        elif self._sdk_type == "google.generativeai" and self._client:
            gen_config = {
                "temperature": temperature,
                "top_p": top_p,
            }
            if response_schema is not None:
                gen_config["response_mime_type"] = "application/json"
                gen_config["response_schema"] = response_schema

            model = self._client.GenerativeModel(
                model_name=self.model_name,
                system_instruction=system_instruction,
                generation_config=gen_config
            )
            try:
                response = model.generate_content(prompt)
                return response.text or ""
            except Exception as exc:
                raise LLMClientError(f"GenerativeAI API call failed: {exc}") from exc

        raise LLMClientError("Google GenAI SDK is not available or properly configured.")


class LLMClientFactory:
    """Factory for creating configured LLM client instances."""

    @staticmethod
    def create_client(
        model_name: str = "gemini-2.5-flash",
        api_key: Optional[str] = None,
        use_mock: bool = False,
        mock_responses: Optional[List[str]] = None,
        **kwargs
    ) -> BaseLLMClient:
        """Create and return an LLM client instance based on settings."""
        if use_mock:
            return MockLLMClient(model_name=model_name, default_responses=mock_responses, **kwargs)

        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            # Fallback to mock client if no API key is available
            return MockLLMClient(model_name=model_name, default_responses=mock_responses, **kwargs)

        try:
            return GenAIClient(model_name=model_name, api_key=key, **kwargs)
        except Exception:
            return MockLLMClient(model_name=model_name, default_responses=mock_responses, **kwargs)
