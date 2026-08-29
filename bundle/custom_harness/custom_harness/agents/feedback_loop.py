"""
Custom Harness Deterministic Feedback Loop
==========================================
Deterministic Dual Sub-Agent Feedback Loop with context window isolation,
FSM state progression, and declarative guardrail integration.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from custom_harness.guardrails.engine import GuardrailEngine
from custom_harness.guardrails.models import (
    ActionType,
    FileOperation as GuardrailFileOp,
    GuardrailContext,
    GuardrailPolicy,
)
from custom_harness.harness.fsm import HarnessFSM
from custom_harness.harness.models import (
    ChecklistItemResult,
    CoderOutput,
    CriticOutput,
    FeedbackLoopResult,
    FileAction,
    HarnessConfig,
    OverallVerdict,
    StepHistory,
    StepState,
)
from .coder import CoderSubAgent
from .critic import CriticSubAgent


class DeterministicFeedbackLoop:
    """
    Orchestrates the iterative feedback loop between CoderSubAgent and CriticSubAgent
    while strictly isolating their context buffers and enforcing declarative guardrails.
    """

    def __init__(
        self,
        coder: CoderSubAgent,
        critic: CriticSubAgent,
        config: Optional[HarnessConfig] = None,
        guardrail_engine: Optional[GuardrailEngine] = None,
    ):
        self.coder = coder
        self.critic = critic
        self.config = config or HarnessConfig()
        self.fsm = HarnessFSM()

        # Initialize or default guardrail engine
        if guardrail_engine is not None:
            self.guardrail_engine = guardrail_engine
        elif self.config.enable_guardrails:
            ws = self.config.workspace_root or Path.cwd()
            policy = GuardrailPolicy(workspace_root=ws, fail_closed=True)
            self.guardrail_engine = GuardrailEngine(policy=policy)
        else:
            self.guardrail_engine = None

    def _convert_to_guardrail_action(self, action: FileAction) -> ActionType:
        """Map Coder FileAction to Guardrail ActionType."""
        mapping = {
            FileAction.CREATE: ActionType.WRITE,
            FileAction.MODIFY: ActionType.WRITE,
            FileAction.DELETE: ActionType.DELETE,
            FileAction.NOOP: ActionType.READ,
        }
        return mapping.get(action, ActionType.WRITE)

    def _check_guardrails(self, coder_output: CoderOutput) -> Tuple[bool, List[str]]:
        """
        Validate all proposed file operations through the GuardrailEngine.
        Returns: (all_valid_bool, list_of_violation_messages)
        """
        if not self.guardrail_engine or not self.config.enable_guardrails:
            return True, []

        violations: List[str] = []
        context = GuardrailContext(doc_grounding_citations=coder_output.grounding_references)
        for op in coder_output.file_operations:
            g_action = self._convert_to_guardrail_action(op.action)
            g_op = GuardrailFileOp(
                target_path=op.file_path,
                action=g_action,
                content=op.content_or_diff if op.action in (FileAction.CREATE, FileAction.MODIFY) else None,
                metadata={"description": op.description}
            )
            try:
                result = self.guardrail_engine.validate_operation(g_op, context=context)
                if not result.is_valid:
                    for v in result.violations:
                        violations.append(f"[{v.violation_type}] {op.file_path}: {v.message}")
            except Exception as ge:
                violations.append(f"[GUARDRAIL_DENIAL] {op.file_path}: {ge}")

        return (len(violations) == 0), violations

    def run(
        self,
        task_description: str,
        grounded_docs: Optional[str] = None,
        checklist_items: Optional[List[Tuple[str, str]]] = None,
        apply_changes_to_disk: bool = False,
    ) -> FeedbackLoopResult:
        """
        Execute the deterministic feedback loop across iterations until convergence or max_turns.
        """
        self.fsm.reset(StepState.INIT)
        history: List[StepHistory] = []

        last_coder_output: Optional[CoderOutput] = None
        last_critic_output: Optional[CriticOutput] = None
        all_violations: List[str] = []

        critic_feedback: Optional[List[str]] = None
        failed_checklist: Optional[List[ChecklistItemResult]] = None

        try:
            for iteration in range(1, self.config.max_turns + 1):
                # 1. Transition to CODER_GENERATE
                self.fsm.transition_to(
                    StepState.CODER_GENERATE,
                    details=f"Iteration {iteration}: Generating solution"
                )

                coder_output, _ = self.coder.generate_solution(
                    task_description=task_description,
                    grounded_docs=grounded_docs,
                    critic_feedback=critic_feedback,
                    failed_checklist=failed_checklist,
                    temperature=self.config.temperature,
                    top_p=self.config.top_p,
                )
                last_coder_output = coder_output

                # 2. Transition to PARSE_CODER_SCHEMA
                self.fsm.transition_to(
                    StepState.PARSE_CODER_SCHEMA,
                    details=f"Iteration {iteration}: Coder schema validated"
                )

                # 3. Guardrail Check
                if self.config.enable_guardrails and self.guardrail_engine:
                    self.fsm.transition_to(
                        StepState.GUARDRAIL_CHECK,
                        details=f"Iteration {iteration}: Checking guardrails"
                    )
                    guardrail_ok, step_violations = self._check_guardrails(coder_output)
                    all_violations.extend(step_violations)
                else:
                    guardrail_ok = True
                    step_violations = []

                # 4. Transition to CRITIC_REVIEW
                self.fsm.transition_to(
                    StepState.CRITIC_REVIEW,
                    details=f"Iteration {iteration}: Critic reviewing solution"
                )

                critic_output, _ = self.critic.review_solution(
                    task_description=task_description,
                    coder_output=coder_output,
                    checklist_items=checklist_items,
                    guardrail_violations=step_violations if not guardrail_ok else None,
                    quality_threshold=self.config.quality_threshold,
                    temperature=self.config.temperature,
                    top_p=self.config.top_p,
                )
                last_critic_output = critic_output

                # 5. Transition to PARSE_CRITIC_SCHEMA
                self.fsm.transition_to(
                    StepState.PARSE_CRITIC_SCHEMA,
                    details=f"Iteration {iteration}: Critic schema validated"
                )

                # Record step history
                step_record = StepHistory(
                    iteration=iteration,
                    state=StepState.EVALUATE_VERDICT,
                    coder_output=coder_output,
                    critic_output=critic_output,
                    guardrail_violations=step_violations,
                )
                history.append(step_record)

                # 6. Transition to EVALUATE_VERDICT
                self.fsm.transition_to(
                    StepState.EVALUATE_VERDICT,
                    details=f"Iteration {iteration}: Verdict is {critic_output.overall_verdict.value}, score {critic_output.quality_score}"
                )

                # Check Approval & Convergence
                is_approved = (
                    critic_output.overall_verdict == OverallVerdict.APPROVED
                    and critic_output.guardrail_compliance
                    and guardrail_ok
                    and critic_output.quality_score >= self.config.quality_threshold
                )

                if is_approved:
                    # Apply changes to disk if requested
                    if apply_changes_to_disk and self.guardrail_engine:
                        guard_ctx = GuardrailContext(doc_grounding_citations=coder_output.grounding_references)
                        for op in coder_output.file_operations:
                            if op.action in (FileAction.CREATE, FileAction.MODIFY):
                                self.guardrail_engine.execute_guarded_write(op.file_path, op.content_or_diff, context=guard_ctx)
                            elif op.action == FileAction.DELETE:
                                self.guardrail_engine.execute_guarded_delete(op.file_path, context=guard_ctx)

                    self.fsm.transition_to(
                        StepState.CONVERGED,
                        details=f"Convergence reached on iteration {iteration}"
                    )
                    return FeedbackLoopResult(
                        success=True,
                        final_verdict=OverallVerdict.APPROVED,
                        iterations_count=iteration,
                        terminal_state=StepState.CONVERGED,
                        final_coder_output=coder_output,
                        final_critic_output=critic_output,
                        history=history,
                        guardrail_violations=all_violations,
                    )

                # Check if unsafe rejection occurred
                if critic_output.overall_verdict == OverallVerdict.REJECTED_UNSAFE or not guardrail_ok:
                    if iteration >= self.config.max_turns:
                        self.fsm.transition_to(
                            StepState.GUARDRAIL_BLOCKED,
                            details="Terminated due to safety violations on final turn."
                        )
                        return FeedbackLoopResult(
                            success=False,
                            final_verdict=OverallVerdict.REJECTED_UNSAFE,
                            iterations_count=iteration,
                            terminal_state=StepState.GUARDRAIL_BLOCKED,
                            final_coder_output=coder_output,
                            final_critic_output=critic_output,
                            history=history,
                            guardrail_violations=all_violations,
                            error="Safety or guardrail policy violation blocked convergence."
                        )

                # Prepare feedback for next turn
                critic_feedback = critic_output.actionable_feedback
                failed_checklist = [r for r in critic_output.checklist_results if not r.passed]

            # Max turns reached without convergence
            self.fsm.transition_to(
                StepState.MAX_TURNS,
                details=f"Max turns ({self.config.max_turns}) exhausted without approval."
            )
            return FeedbackLoopResult(
                success=False,
                final_verdict=last_critic_output.overall_verdict if last_critic_output else OverallVerdict.CHANGES_REQUESTED,
                iterations_count=self.config.max_turns,
                terminal_state=StepState.MAX_TURNS,
                final_coder_output=last_coder_output,
                final_critic_output=last_critic_output,
                history=history,
                guardrail_violations=all_violations,
                error=f"Did not converge after {self.config.max_turns} iterations."
            )

        except Exception as exc:
            self.fsm.force_error(str(exc))
            return FeedbackLoopResult(
                success=False,
                final_verdict=OverallVerdict.REJECTED_UNSAFE,
                iterations_count=len(history),
                terminal_state=StepState.ERROR,
                final_coder_output=last_coder_output,
                final_critic_output=last_critic_output,
                history=history,
                guardrail_violations=all_violations,
                error=f"Execution failed with unexpected exception: {exc}"
            )


# Convenient alias
DualAgentOrchestrator = DeterministicFeedbackLoop
