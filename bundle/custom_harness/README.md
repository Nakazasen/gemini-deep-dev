# Antigravity Deterministic Gemini Flash Agent Harness

A high-assurance, deterministic agent runtime and MCP documentation grounding system designed specifically for **Google Gemini Flash** models (`gemini-2.5-flash`, `gemini-1.5-flash`).

This package eliminates hallucinations, outdated API assumptions, and instruction skipping through **Pydantic V2 Strict Schemas**, **Isolated Dual Sub-Agents (Coder + Critic)**, **Reflection Auto-Retry**, and **Fail-Closed Declarative Guardrails**.

---

## Key Architecture Pillars

```
                     ┌──────────────────────────────────────────────┐
                     │          FastMCP Grounding Server            │
                     │  - Live HTML-to-Markdown Docs Fetcher        │
                     │  - Canonical Unicode NFKC SHA-256 Hasher     │
                     │  - BM25 + RapidFuzz Workspace Retriever      │
                     │  - Context Injector with [Doc-Hash: <id>]    │
                     └──────────────────────┬───────────────────────┘
                                            │ Grounded References
                                            ▼
                     ┌──────────────────────────────────────────────┐
                     │         Coder Sub-Agent (Isolated)           │
                     │  - Role: Code planning & atomic file ops     │
                     │  - Strict Output: CoderOutput (Pydantic V2)  │
                     │  - Reflection Auto-Retry Engine              │
                     └──────────────────────┬───────────────────────┘
                                            │ Proposed Operations
                                            ▼
                     ┌──────────────────────────────────────────────┐
                     │    Fail-Closed Policy & Guardrail Engine     │
                     │  - Canonical Path Boundary Validator         │
                     │  - Windows NTFS / ADS / UNC / Device Checks  │
                     │  - Anti-Laziness Placeholder Interceptor     │
                     │  - Python AST & JSON Syntax Pre-Validator    │
                     └──────────────────────┬───────────────────────┘
                                            │ Validated Payload
                                            ▼
                     ┌──────────────────────────────────────────────┐
                     │         Critic Sub-Agent (Isolated)          │
                     │  - Role: Adversarial review & logic audits   │
                     │  - Strict Output: CriticOutput (Pydantic V2) │
                     │  - Mandatory Checklist & Severity Scoring    │
                     └──────────────────────┬───────────────────────┘
                                            │
                     ┌──────────────────────┴───────────────────────┐
                     │          Feedback Loop State Machine         │
                     │  - CHANGES_REQUESTED -> Revise Coder Context │
                     │  - APPROVED          -> Converge & Finalize  │
                     │  - REJECTED_UNSAFE   -> Terminate Safely     │
                     └──────────────────────────────────────────────┘
```

---

## Directory Structure

```text
custom_harness/
├── custom_harness/
│   ├── __init__.py                 # Unified package exports
│   ├── config.py                   # HarnessSettings & environment loader
│   ├── cli.py                      # Unified CLI entrypoint (`antigravity-harness`)
│   ├── harness/                    # Core Deterministic Harness
│   │   ├── __init__.py
│   │   ├── models.py               # Pydantic V2 strict models (extra="forbid", frozen=True)
│   │   ├── parser.py               # Multi-tier JSON parser & markdown fence stripper
│   │   ├── client.py               # Google GenAI / Mock LLM Client adapters
│   │   ├── retry.py                # Reflection Auto-Retry with error feedback
│   │   └── fsm.py                  # Finite State Machine transitions
│   ├── agents/                     # Isolated Sub-Agents
│   │   ├── __init__.py
│   │   ├── base.py                 # Abstract BaseSubAgent with isolated context buffers
│   │   ├── coder.py                # CoderSubAgent for planning and implementation
│   │   ├── critic.py               # CriticSubAgent for checklist and safety reviews
│   │   └── feedback_loop.py        # DeterministicFeedbackLoop orchestrator
│   └── guardrails/                 # Declarative Safety Policies
│       ├── __init__.py
│       ├── exceptions.py           # Guardrail error hierarchy
│       ├── models.py               # GuardrailPolicy, FileOperation, ValidationResult
│       ├── path_validator.py       # NTFS, ADS, traversal, device name validator
│       ├── rule_loader.py          # YAML, JSON, .antigravityrules loader
│       └── engine.py               # Interceptor, content scanner, and disk modifier
├── mcp_grounding/                  # FastMCP Documentation Grounding Provider
│   ├── __init__.py
│   ├── schemas.py                  # Grounding schemas & tool input models
│   ├── hasher.py                   # Canonical Unicode NFKC SHA-256 digest engine
│   ├── cache.py                    # Memory LRU + Disk cache layer
│   ├── live_fetcher.py             # Live docs fetcher from antigravity.google/docs
│   ├── retriever.py                # Hybrid BM25 / Fuzzy workspace scanner
│   ├── prompt_injector.py          # Grounded delimiter context injector
│   └── server.py                   # FastMCP stdio server definition
├── mcp_server.py                   # Standalone entrypoint for FastMCP stdio server
├── pyproject.toml                  # Modern Python packaging configuration
└── tests/                          # Comprehensive pytest test suite
```

---

## Installation & Setup

### 1. Basic Installation

```bash
cd custom_harness
pip install -e .
```

### 2. Registering the MCP server

Do not register this server manually. Use the bundle's top-level `install-deep-dev.ps1`; it reads the signed `mcp_contract.json`, removes historical aliases, and registers the single canonical `deep_dev_harness` identity with the required Python paths.

---

## CLI Usage Guide

The package provides the `antigravity-harness` command:

### 1. Run Deterministic Dual-Agent Feedback Loop

```bash
# Execute with live Gemini model
antigravity-harness run --task "Implement user authentication middleware with JWT verification" --workspace .

# Execute in deterministic offline mock mode (no API key required)
antigravity-harness run --task "Add health check endpoint" --mock

# Output execution result as structured JSON
antigravity-harness run --task "Create logging utility" --mock --json
```

### 2. Launch FastMCP Documentation Server

```bash
antigravity-harness mcp
```

### 3. Evaluate Guardrails on a Path or File

```bash
# Check if a write operation to a given path is permitted
antigravity-harness guardrail --path "src/utils/helper.py" --action write

# Test guardrail block on sensitive files
antigravity-harness guardrail --path ".git/config" --action write
```

### 4. Query Documentation Grounding

```bash
antigravity-harness grounding --search "skills and agents configuration"
```

---

## Python API Usage

```python
from custom_harness import (
    CoderSubAgent,
    CriticSubAgent,
    DeterministicFeedbackLoop,
    HarnessConfig,
    MockLLMClient,
    GuardrailEngine,
    GuardrailPolicy,
)

# 1. Initialize client
client = MockLLMClient()

# 2. Initialize isolated sub-agents
coder = CoderSubAgent(client=client)
critic = CriticSubAgent(client=client)

# 3. Configure guardrails and execution parameters
policy = GuardrailPolicy(fail_closed=True)
guardrail_engine = GuardrailEngine(policy=policy)
config = HarnessConfig(max_turns=3, quality_threshold=0.85)

# 4. Orchestrate feedback loop
loop = DeterministicFeedbackLoop(
    coder=coder,
    critic=critic,
    config=config,
    guardrail_engine=guardrail_engine,
)

result = loop.run(
    task_description="Implement rate limiting decorator for FastAPI endpoints."
)

print(f"Success: {result.success}")
print(f"Final Verdict: {result.final_verdict}")
print(f"Iterations: {result.iterations_count}")
```

---

## Running the Automated Test Suite

Run the full pytest suite:

```bash
pytest -v tests/
```
