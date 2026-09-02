# Gemini Deep Dev (v0.3.0)

**A High-Performance Boosted Execution Engine (Dual-Agent Edition) separating Implementation and Verification for Google Gemini Flash in Antigravity IDE.**

---

## 🎯 Problems Solved

While **Google Gemini Flash 3.*** delivers industry-leading inference speed and a vast context window, autonomous coding workflows often suffer from predictable pitfalls:
1. **Hallucinated Verification (Fake PASS)**: Claiming fixes or implementations are complete without running actual test commands.
2. **Lazy Code & Placeholders**: Truncating code output with placeholders like `// TODO`, `/* unchanged code */`, `...`, or `pass`.
3. **Attention Dispersion & Blind Spots**: Self-reviewing one's own code often leads to confirmation bias and overlooked edge cases.

**Gemini Deep Dev (v0.3.0 - Dual-Agent Edition)** introduces strict role separation: **Lead Architect (Planning)** + **Coder Sub-Agent (100% Complete Implementation)** + **Critic Sub-Agent (Independent Test & Adversarial Review)**.

---

## ⚙️ Architecture & Execution Lifecycle (Triad Architecture)

```text
               User Prompt (/deep-dev)
                           ↓
         [Role 1] Lead Architect: AST & Dependency Discovery
                           ↓
         [Role 2] Coder Sub-Agent: Direct Full Implementation (Zero Placeholders)
                           ↓
         [Role 3] Critic Sub-Agent: Independent Test Runner & Adversarial Review
                           ↓
    ┌──────────────────────┴──────────────────────┐
    ▼                                             ▼
[ALL TESTS PASS]: Complete Execution          [TEST FAIL]: Traceback Inspection
  • Evidence-based Output (Terminal stdout)     • Feedback to Coder Sub-Agent
  • Automatic AgentMemory Checkpoints           • Autonomous Self-Healing Iteration
```

---

## 🚀 Key Highlights in v0.3.0 (Dual-Agent Edition)

- **Strict Coder vs Critic Separation**: Coder focuses on flawless implementation while Critic conducts independent testing and adversarial code review.
- **Frictionless & Fast**: Direct execution without rigid ticket handshakes or blocking proposal serializations.
- **Zero-Evidence = Failure**: Never accepts a completion report unless backed by real terminal test receipts.
- **Autonomous Self-Healing**: Automatically reads failure tracebacks and iterates until all tests pass.
- **AgentMemory Checkpoints**: Persists verified milestone state to AgentMemory.

### 2. Zero-Config & Flexible Test Enforcement
- **Arbitrary Repositories (No `.deep_dev/config.json`)**: Deep Dev automatically validates AST syntax, merge sanity, and checksums. It never fails a proposal due to missing test suites.
- **Projects with `.deep_dev/config.json`**: Strictly enforces configured test commands (`pytest`, `npm test`, `cargo test`, etc.), requiring verified stdout/stderr proof before merging.

- Completely eliminates placeholder hallucinations.
- All file edits require 100% complete character-matched replacement chunks.

### 4. Native Evidence-Based Checkpointing
- Automatically syncs project state to **AgentMemory** after successful runs.
- Updates AST relationships via **Graphify** knowledge graph.

---

## 📦 Optional Test Configuration (`.deep_dev/config.json`)

To enforce project-specific automated test suites prior to patch approval, place `.deep_dev/config.json` in your repository root:

```json
{
  "version": "1.0",
  "allowlisted_test_commands": {
    "pytest_suite": {
      "executable": "py",
      "args": ["-3", "-m", "pytest", "-q"],
      "cwd": ".",
      "timeout_seconds": 120
    }
  }
}
```

---

## 🛠 Installation & Usage

### 1. Install to Antigravity IDE
Open PowerShell in the repository root and run:
```powershell
.\tools\Install-DeepDev.ps1
```

### 2. Trigger via Chat
Invoke `/deep-dev` alongside your coding task:
```text
/deep-dev implement user authentication handler with full unit tests
```

---

## 📄 License & Author
- Author: **Nakazasen**
- Version: **v0.1.0**
- License: MIT License
