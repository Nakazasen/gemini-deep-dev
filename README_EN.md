# Gemini Deep Dev (v0.1.0)

**A Deterministic Execution Harness & Runtime Quality Gate Engine tailored for Google Gemini Flash in Antigravity IDE.**

---

## 🎯 Problems Solved

While **Google Gemini Flash 3.*** delivers industry-leading inference speed and a vast context window, autonomous coding workflows often suffer from predictable pitfalls:
1. **Hallucinated Verification (Fake PASS)**: Claiming fixes or implementations are complete without running actual test commands.
2. **Lazy Code & Placeholders**: Truncating code output with placeholders like `// TODO`, `/* unchanged code */`, `...`, or `pass`.
3. **Attention Dispersion**: Degradation of focus across massive context inputs, leading to missed architectural constraints.
4. **Destructive In-Place Mutations**: Overwriting workspace files directly without isolation or rollback mechanisms.

**Gemini Deep Dev** transforms Gemini Flash into a **deterministic, precise, and verifiable coding engine** via strict runtime execution gates and ephemeral Git worktree isolation.

---

## ⚙️ Architecture & Execution Lifecycle

```text
               User Prompt (/deep-dev)
                         ↓
               Preflight & Snapshot: Disposable Isolated Git Worktree
                         ↓
               Scope Ticket Exchange: Strict target path whitelisting
                         ↓
               Trial Mutation: Atomic Exact-Replace Patch Application
                         ↓
               Verification: Run Allowlisted Test Suite (if configured)
                             or Verify Syntax Baseline (if zero-config)
                         ↓
   ┌─────────────────────┴─────────────────────┐
   ▼                                           ▼
[PASS]: Atomic Merge into Main Workspace     [FAIL]: Instant Worktree Rollback,
        & Sync AgentMemory / Graphify                Audit Log Recorded
```

---

## 🚀 Key Highlights

### 1. True Ephemeral Isolation (Git Worktree Engine)
- All proposed mutations and tests run inside an isolated Git worktree branch (`.deep_dev/worktrees/...`).
- The user's main workspace remains 100% untouched until tests and AST validations pass.
- Fast Windows NTFS performance: Untracked heavy directories (`.venv`, `node_modules`, `local_cases`, `__pycache__`) are skipped during baseline mirroring, ensuring `< 1s` worktree initialization.

### 2. Zero-Config & Flexible Test Enforcement
- **Arbitrary Repositories (No `.deep_dev/config.json`)**: Deep Dev automatically validates AST syntax, merge sanity, and checksums. It never fails a proposal due to missing test suites.
- **Projects with `.deep_dev/config.json`**: Strictly enforces configured test commands (`pytest`, `npm test`, `cargo test`, etc.), requiring verified stdout/stderr proof before merging.

### 3. Anti-Laziness & Strict Exact Replacement
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
