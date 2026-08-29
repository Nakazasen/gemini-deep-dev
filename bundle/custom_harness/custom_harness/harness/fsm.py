"""
Custom Harness Finite State Machine (FSM)
=========================================
State machine managing transitions across the dual-agent feedback lifecycle:
INIT -> CODER_GENERATE -> PARSE_CODER_SCHEMA -> GUARDRAIL_CHECK -> CRITIC_REVIEW -> PARSE_CRITIC_SCHEMA -> EVALUATE_VERDICT -> CONVERGED / MAX_TURNS / GUARDRAIL_BLOCKED
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set
from .models import StepState


class FSMTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""
    pass


class HarnessFSM:
    """
    Finite State Machine orchestrating and validating harness execution phases.
    """

    VALID_TRANSITIONS: Dict[StepState, Set[StepState]] = {
        StepState.INIT: {
            StepState.CODER_GENERATE,
            StepState.ERROR,
        },
        StepState.CODER_GENERATE: {
            StepState.PARSE_CODER_SCHEMA,
            StepState.SCHEMA_FAILURE,
            StepState.ERROR,
        },
        StepState.PARSE_CODER_SCHEMA: {
            StepState.GUARDRAIL_CHECK,
            StepState.CRITIC_REVIEW,  # When guardrails are disabled
            StepState.SCHEMA_FAILURE,
            StepState.ERROR,
        },
        StepState.GUARDRAIL_CHECK: {
            StepState.CRITIC_REVIEW,
            StepState.GUARDRAIL_BLOCKED,
            StepState.ERROR,
        },
        StepState.CRITIC_REVIEW: {
            StepState.PARSE_CRITIC_SCHEMA,
            StepState.SCHEMA_FAILURE,
            StepState.ERROR,
        },
        StepState.PARSE_CRITIC_SCHEMA: {
            StepState.EVALUATE_VERDICT,
            StepState.SCHEMA_FAILURE,
            StepState.ERROR,
        },
        StepState.EVALUATE_VERDICT: {
            StepState.CONVERGED,
            StepState.CODER_GENERATE,
            StepState.MAX_TURNS,
            StepState.GUARDRAIL_BLOCKED,
            StepState.ERROR,
        },
        # Terminal states have no outbound transitions except reset
        StepState.CONVERGED: set(),
        StepState.MAX_TURNS: set(),
        StepState.GUARDRAIL_BLOCKED: set(),
        StepState.SCHEMA_FAILURE: set(),
        StepState.ERROR: set(),
    }

    TERMINAL_STATES: Set[StepState] = {
        StepState.CONVERGED,
        StepState.MAX_TURNS,
        StepState.GUARDRAIL_BLOCKED,
        StepState.SCHEMA_FAILURE,
        StepState.ERROR,
    }

    def __init__(self, initial_state: StepState = StepState.INIT):
        self._current_state: StepState = initial_state
        self._history: List[Dict[str, Any]] = [
            {
                "from_state": None,
                "to_state": initial_state,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "details": "FSM initialized",
            }
        ]

    @property
    def current_state(self) -> StepState:
        """Get current state."""
        return self._current_state

    @property
    def is_terminal(self) -> bool:
        """Check if FSM reached a terminal state."""
        return self._current_state in self.TERMINAL_STATES

    def can_transition_to(self, next_state: StepState) -> bool:
        """Check if transition from current state to next_state is valid."""
        allowed = self.VALID_TRANSITIONS.get(self._current_state, set())
        return next_state in allowed

    def transition_to(self, next_state: StepState, details: Optional[str] = None) -> StepState:
        """
        Transition to next state if valid, else raise FSMTransitionError.
        """
        if not self.can_transition_to(next_state):
            raise FSMTransitionError(
                f"Invalid state transition from '{self._current_state}' to '{next_state}'. "
                f"Allowed target states: {[s.value for s in self.VALID_TRANSITIONS.get(self._current_state, set())]}"
            )

        from_state = self._current_state
        self._current_state = next_state
        self._history.append({
            "from_state": from_state,
            "to_state": next_state,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "details": details or "",
        })
        return self._current_state

    def force_error(self, error_message: str) -> StepState:
        """Force transition to ERROR state from any non-terminal state."""
        from_state = self._current_state
        self._current_state = StepState.ERROR
        self._history.append({
            "from_state": from_state,
            "to_state": StepState.ERROR,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "details": f"Error forced: {error_message}",
        })
        return self._current_state

    def get_history(self) -> List[Dict[str, Any]]:
        """Return shallow copy of transition history."""
        return list(self._history)

    def reset(self, initial_state: StepState = StepState.INIT) -> None:
        """Reset FSM to initial state."""
        self._current_state = initial_state
        self._history = [
            {
                "from_state": None,
                "to_state": initial_state,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "details": "FSM reset",
            }
        ]
