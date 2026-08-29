"""
Custom Harness Coder Sub-Agent
==============================
Deterministic CoderSubAgent for planning, grounding citation, and code generation.
Strictly generates pure JSON structured outputs conforming to CoderOutput.
"""

from typing import Any, Dict, List, Optional, Tuple
from custom_harness.harness.client import BaseLLMClient
from custom_harness.harness.models import (
    AgentRole,
    ChecklistItemResult,
    CoderOutput,
)
from custom_harness.harness.retry import RetryAttemptRecord
from .base import BaseSubAgent

CODER_SYSTEM_INSTRUCTION = """You are the Antigravity Coder Sub-Agent, an expert software engineer generating verified, deterministic code implementations.

Core Principles:
1. Grounded Implementation: Use only verified APIs and conventions provided in the grounded documentation context. Always cite doc hashes using [Doc-Hash: <prefix>] format.
2. Minimal & Atomic Changes: Make only the precise changes required by the task. Do not perform unrelated refactoring.
3. Anti-Laziness: Never output placeholder comments like TODO, FIXME, ellipsis (...), or incomplete dummy functions. Every file operation must contain complete, functional code.
4. Fail-Closed Boundaries: Modify only permitted files within the specified workspace.
5. Strict JSON Output: Output MUST be a valid JSON object strictly conforming to the CoderOutput schema:
   - thought_process (string): Detailed architectural reasoning and trade-off analysis.
   - grounding_references (list of strings): Doc hashes, URLs, or file paths cited.
   - plan_steps (list of strings): Step-by-step implementation sequence.
   - file_operations (list of FileOperation objects): Each with file_path, action ("create", "modify", "delete", "noop"), content_or_diff, and description.
   - verification_commands (list of strings): Commands to test and verify the changes.
   - risk_assessment (string): Potential side effects or compatibility considerations.
"""


class CoderSubAgent(BaseSubAgent):
    """
    Coder Sub-Agent responsible for generating plans, file operations, and verification commands.
    """

    def __init__(
        self,
        client: BaseLLMClient,
        name: str = "CoderSubAgent",
        system_instruction: str = CODER_SYSTEM_INSTRUCTION,
        max_retries: int = 3,
    ):
        super().__init__(
            name=name,
            role=AgentRole.CODER,
            client=client,
            system_instruction=system_instruction,
            max_retries=max_retries,
        )

    def generate_solution(
        self,
        task_description: str,
        grounded_docs: Optional[str] = None,
        critic_feedback: Optional[List[str]] = None,
        failed_checklist: Optional[List[ChecklistItemResult]] = None,
        temperature: float = 0.2,
        top_p: float = 0.95,
        **kwargs
    ) -> Tuple[CoderOutput, List[RetryAttemptRecord]]:
        """
        Generate or revise a structured code implementation plan.
        """
        prompt_parts: List[str] = []

        if grounded_docs:
            prompt_parts.append("=== GROUNDED DOCUMENTATION CONTEXT ===")
            prompt_parts.append(grounded_docs)
            prompt_parts.append("======================================\n")

        if critic_feedback or failed_checklist:
            prompt_parts.append("=== REVISION FEEDBACK FROM CRITIC ===")
            if critic_feedback:
                prompt_parts.append("Actionable Review Feedback:")
                for item in critic_feedback:
                    prompt_parts.append(f"- {item}")
            if failed_checklist:
                prompt_parts.append("Failed Checklist Items:")
                for item in failed_checklist:
                    prompt_parts.append(
                        f"- [{item.severity_if_failed.value}] {item.criterion_id}: {item.description} -> Reason: {item.evidence}"
                    )
            prompt_parts.append("=====================================\n")

        prompt_parts.append("=== IMPLEMENTATION TASK ===")
        prompt_parts.append(task_description)
        prompt_parts.append("===========================\n")
        prompt_parts.append("Please analyze the task, consult the grounded docs, and generate the complete CoderOutput JSON.")

        full_prompt = "\n".join(prompt_parts)
        context_prompt = self.get_formatted_context_prompt(full_prompt)

        # Record outgoing prompt in isolated message buffer
        self.add_message(
            role=AgentRole.USER,
            content=full_prompt,
            metadata={"has_grounding": bool(grounded_docs), "is_revision": bool(critic_feedback)}
        )

        coder_output, attempt_history = self._retry_engine.execute_with_reflection(
            client=self.client,
            prompt=context_prompt,
            model_cls=CoderOutput,
            system_instruction=self.system_instruction,
            max_retries=self.max_retries,
            temperature=temperature,
            top_p=top_p,
            **kwargs
        )

        # Record model response in isolated buffer
        self.add_message(
            role=AgentRole.CODER,
            content=coder_output.model_dump_json(indent=2),
            metadata={"plan_steps_count": len(coder_output.plan_steps), "file_ops_count": len(coder_output.file_operations)}
        )

        return coder_output, attempt_history

    def generate(
        self,
        task: str,
        grounded_docs: Optional[List[Any]] = None,
        feedback: Optional[List[str]] = None,
        **kwargs
    ) -> CoderOutput:
        """Convenience execution method returning CoderOutput."""
        g_text = None
        if grounded_docs:
            doc_lines = []
            for d in grounded_docs:
                sha = getattr(d, "sha256", str(d))
                doc_lines.append(f"[Doc-Hash: {sha}] {getattr(d, 'title', '')}")
            g_text = "\n".join(doc_lines)
        output, _ = self.generate_solution(
            task_description=task,
            grounded_docs=g_text,
            critic_feedback=feedback,
            **kwargs
        )
        return output
