"""
Custom Harness Critic Sub-Agent
===============================
Deterministic CriticSubAgent for reviewing code changes, validating checklists,
evaluating guardrails, and providing actionable feedback.
Strictly generates pure JSON structured outputs conforming to CriticOutput.
"""

from typing import Any, Dict, List, Optional, Tuple
from custom_harness.harness.client import BaseLLMClient
from custom_harness.harness.models import (
    AgentRole,
    ChecklistItemResult,
    CoderOutput,
    CriticOutput,
    OverallVerdict,
    SeverityLevel,
)
from custom_harness.harness.retry import RetryAttemptRecord
from .base import BaseSubAgent

CRITIC_SYSTEM_INSTRUCTION = """You are the Antigravity Critic Sub-Agent, an adversarial code reviewer and security auditor.

Core Principles:
1. Rigorous Inspection: Carefully verify logic correctness, edge cases, typing, and potential regressions in the Coder's proposed plan and file operations.
2. Guardrail & Safety Enforcement: Verify that operations strictly respect workspace boundaries, do not touch forbidden configuration/secret files, and contain no lazy placeholder patterns.
3. Explicit Checklist Verification: Evaluate every requirement and checklist item with concrete evidence from the code.
4. Objective Scoring: Assign a realistic quality score (0.0 to 1.0) and appropriate failure severity (CRITICAL, MAJOR, MINOR, INFO).
5. Definite Verdict:
   - "APPROVED": ONLY when all criteria pass, guardrail_compliance is true, and quality_score is >= threshold.
   - "CHANGES_REQUESTED": When defects or incomplete criteria can be resolved through revision.
   - "REJECTED_UNSAFE": When severe security or policy violations occur.
6. Strict JSON Output: Output MUST be a valid JSON object strictly conforming to the CriticOutput schema:
   - thought_process (string): Detailed analytical review findings.
   - checklist_results (list of ChecklistItemResult): Each with criterion_id, description, passed (bool), severity_if_failed, and evidence.
   - guardrail_compliance (bool): True if zero safety violations.
   - logic_review_summary (string): Synthesis of logic correctness and code quality.
   - overall_verdict (string): "APPROVED", "CHANGES_REQUESTED", or "REJECTED_UNSAFE".
   - quality_score (float): Value between 0.0 and 1.0.
   - actionable_feedback (list of strings): Clear, concrete instructions for the Coder to fix any deficiencies.
"""

DEFAULT_CHECKLIST = [
    ("REQ-CORRECTNESS", "Code logic satisfies task requirements and handles edge cases properly"),
    ("REQ-GROUNDING", "API usages are grounded in official documentation with verified references"),
    ("REQ-NO-LAZINESS", "All functions and modules are fully implemented with no TODOs or ellipsis placeholders"),
    ("REQ-SAFETY", "File operations are bounded, secure, and touch only permitted target files"),
    ("REQ-VERIFICATION", "Sufficient build, lint, and test verification commands are provided"),
]


class CriticSubAgent(BaseSubAgent):
    """
    Critic Sub-Agent responsible for reviewing Coder outputs, checking criteria, and providing feedback.
    """

    def __init__(
        self,
        client: BaseLLMClient,
        name: str = "CriticSubAgent",
        system_instruction: str = CRITIC_SYSTEM_INSTRUCTION,
        max_retries: int = 3,
    ):
        super().__init__(
            name=name,
            role=AgentRole.CRITIC,
            client=client,
            system_instruction=system_instruction,
            max_retries=max_retries,
        )

    def review_solution(
        self,
        task_description: str,
        coder_output: CoderOutput,
        checklist_items: Optional[List[Tuple[str, str]]] = None,
        guardrail_violations: Optional[List[str]] = None,
        quality_threshold: float = 0.8,
        temperature: float = 0.2,
        top_p: float = 0.95,
        **kwargs
    ) -> Tuple[CriticOutput, List[RetryAttemptRecord]]:
        """
        Review Coder output against requirements, checklist items, and guardrail policies.
        """
        items = checklist_items or DEFAULT_CHECKLIST

        prompt_parts: List[str] = [
            "=== ORIGINAL TASK SPECIFICATION ===",
            task_description,
            "===================================\n",
            "=== PROPOSED CODER SOLUTION ===",
            coder_output.model_dump_json(indent=2),
            "================================\n",
        ]

        if guardrail_violations:
            prompt_parts.append("=== DETECTED GUARDRAIL VIOLATIONS ===")
            for v in guardrail_violations:
                prompt_parts.append(f"- [VIOLATION] {v}")
            prompt_parts.append("=====================================\n")

        prompt_parts.append("=== MANDATORY CHECKLIST ITEMS TO EVALUATE ===")
        for cid, cdesc in items:
            prompt_parts.append(f"- [{cid}] {cdesc}")
        prompt_parts.append("=============================================\n")

        prompt_parts.append(
            f"Please conduct an in-depth code review. The quality approval threshold is {quality_threshold}. "
            "Output your evaluation as pure JSON conforming to CriticOutput."
        )

        full_prompt = "\n".join(prompt_parts)
        context_prompt = self.get_formatted_context_prompt(full_prompt)

        # Record outgoing prompt in isolated message buffer
        self.add_message(
            role=AgentRole.USER,
            content=full_prompt,
            metadata={"items_count": len(items), "violations_count": len(guardrail_violations or [])}
        )

        critic_output, attempt_history = self._retry_engine.execute_with_reflection(
            client=self.client,
            prompt=context_prompt,
            model_cls=CriticOutput,
            system_instruction=self.system_instruction,
            max_retries=self.max_retries,
            temperature=temperature,
            top_p=top_p,
            **kwargs
        )

        # Record Critic output in isolated message buffer
        self.add_message(
            role=AgentRole.CRITIC,
            content=critic_output.model_dump_json(),
            metadata={
                "verdict": critic_output.overall_verdict.value,
                "score": critic_output.quality_score,
                "guardrail_compliance": critic_output.guardrail_compliance
            }
        )

        return critic_output, attempt_history

    def review(
        self,
        coder_output: CoderOutput,
        task: str = "Review Code Solution",
        checklist_items: Optional[List[Tuple[str, str]]] = None,
        **kwargs
    ) -> CriticOutput:
        """Convenience execution method returning CriticOutput."""
        output, _ = self.review_solution(
            task_description=task,
            coder_output=coder_output,
            checklist_items=checklist_items,
            **kwargs
        )
        return output
