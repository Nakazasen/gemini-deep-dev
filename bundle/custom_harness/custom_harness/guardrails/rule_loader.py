"""
Custom Harness Rule Loader & Enforcer
=====================================
Loads declarative guardrail rules from .antigravity/ configs, YAML/JSON policy files,
and .antigravityrules specifications. Strictly enforces Fail-Closed behavior on any
configuration errors or ambiguities.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

from .exceptions import FailClosedAbortError, PolicyViolationError
from .models import GuardrailPolicy, PolicyRule, RuleSeverity


class RuleLoader:
    """
    Declarative rule loader for security policies.
    Enforces Fail-Closed operation: any malformed, ambiguous, or corrupted rule configuration
    immediately raises a FailClosedAbortError rather than falling back to an unverified state.
    """

    @classmethod
    def load_policy(
        cls,
        config_path: Optional[Union[str, Path]] = None,
        workspace_root: Optional[Union[str, Path]] = None,
        overrides: Optional[Dict[str, Any]] = None
    ) -> GuardrailPolicy:
        """
        Loads and validates a GuardrailPolicy from a configuration file,
        .antigravity configuration, or default fail-closed profile.
        """
        ws_root = Path(workspace_root).resolve() if workspace_root else Path.cwd().resolve()
        policy_data: Dict[str, Any] = {"workspace_root": ws_root}

        # 1. If explicit config path is given
        if config_path:
            cfg_file = Path(config_path)
            if not cfg_file.is_absolute():
                cfg_file = (ws_root / cfg_file).resolve()

            if not cfg_file.exists():
                raise FailClosedAbortError(
                    f"Specified policy configuration file does not exist: '{cfg_file}'. "
                    "Fail-Closed rule requires a valid policy file."
                )

            policy_data = cls._parse_file(cfg_file)
            if "workspace_root" not in policy_data:
                policy_data["workspace_root"] = ws_root

        # 2. Check for standard configuration files in workspace if no config_path given
        else:
            standard_candidates = [
                ws_root / ".antigravity" / "policy.yaml",
                ws_root / ".antigravity" / "policy.yml",
                ws_root / ".antigravity" / "policy.json",
                ws_root / "policy.yaml",
                ws_root / "policy.yml",
                ws_root / "policy.json",
            ]
            found_cfg = None
            for cand in standard_candidates:
                if cand.exists():
                    found_cfg = cand
                    break

            if found_cfg:
                policy_data = cls._parse_file(found_cfg)
                if "workspace_root" not in policy_data:
                    policy_data["workspace_root"] = ws_root
            else:
                # 3. Check for .antigravityrules in workspace root
                antigravityrules_path = ws_root / ".antigravityrules"
                if antigravityrules_path.exists():
                    cls._enrich_from_antigravityrules(antigravityrules_path, policy_data)

        # 4. Check for .antigravity/rules/ directory
        rules_dir = ws_root / ".antigravity" / "rules"
        if rules_dir.is_dir():
            cls._enrich_from_rules_dir(rules_dir, policy_data)

        # 5. Apply programmatic overrides
        if overrides:
            policy_data.update(overrides)

        # 6. Instantiate and validate with Pydantic V2
        try:
            return GuardrailPolicy.model_validate(policy_data)
        except Exception as e:
            raise FailClosedAbortError(
                f"Failed to validate guardrail policy schema: {e}. Aborting in Fail-Closed mode."
            ) from e

    @classmethod
    def _parse_file(cls, path: Path) -> Dict[str, Any]:
        """Parses a YAML or JSON policy file with strict Fail-Closed error handling."""
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            raise FailClosedAbortError(
                f"Failed to read policy configuration from '{path}': {e}. Aborting in Fail-Closed mode."
            ) from e

        suffix = path.suffix.lower()
        if suffix in [".yaml", ".yml"]:
            if yaml is None:
                raise FailClosedAbortError(
                    f"YAML configuration '{path}' found but PyYAML is not installed. Aborting in Fail-Closed mode."
                )
            try:
                parsed = yaml.safe_load(content)
                if parsed is None:
                    return {}
                if not isinstance(parsed, dict):
                    raise ValueError("YAML root must be a mapping/dictionary")
                return parsed
            except Exception as e:
                raise FailClosedAbortError(
                    f"Malformed YAML in policy file '{path}': {e}. Aborting in Fail-Closed mode."
                ) from e
        elif suffix == ".json":
            try:
                parsed = json.loads(content)
                if not isinstance(parsed, dict):
                    raise ValueError("JSON root must be an object/dictionary")
                return parsed
            except Exception as e:
                raise FailClosedAbortError(
                    f"Malformed JSON in policy file '{path}': {e}. Aborting in Fail-Closed mode."
                ) from e
        else:
            raise FailClosedAbortError(
                f"Unsupported policy configuration format '{suffix}' for '{path}'. Supported: .yaml, .yml, .json."
            )

    @classmethod
    def _enrich_from_antigravityrules(cls, rules_path: Path, data: Dict[str, Any]) -> None:
        """Parses meta-rules from .antigravityrules file."""
        try:
            text = rules_path.read_text(encoding="utf-8")
            if "Anti-Laziness" in text or "anti-laziness" in text:
                data.setdefault("anti_laziness_enabled", True)
            if "Fail-Closed" in text or "fail-closed" in text:
                data.setdefault("fail_closed", True)
        except Exception as e:
            raise FailClosedAbortError(
                f"Failed to parse .antigravityrules from '{rules_path}': {e}. Aborting in Fail-Closed mode."
            ) from e

    @classmethod
    def _enrich_from_rules_dir(cls, rules_dir: Path, data: Dict[str, Any]) -> None:
        """Loads custom rule files from .antigravity/rules directory."""
        custom_rules: List[Dict[str, Any]] = data.get("custom_rules", [])
        try:
            for rule_file in rules_dir.glob("*.*"):
                if rule_file.suffix.lower() in [".json", ".yaml", ".yml"]:
                    rule_content = cls._parse_file(rule_file)
                    if "rule_id" in rule_content and "name" in rule_content:
                        custom_rules.append(rule_content)
                    elif "rules" in rule_content and isinstance(rule_content["rules"], list):
                        custom_rules.extend(rule_content["rules"])
            data["custom_rules"] = custom_rules
        except Exception as e:
            raise FailClosedAbortError(
                f"Failed to load rules from directory '{rules_dir}': {e}. Aborting in Fail-Closed mode."
            ) from e

    @classmethod
    def create_default_policy(
        cls,
        workspace_root: Optional[Union[str, Path]] = None,
        fail_closed: bool = True,
        anti_laziness: bool = True
    ) -> GuardrailPolicy:
        """Generates a secure, fail-closed default policy."""
        ws_root = Path(workspace_root).resolve() if workspace_root else Path.cwd().resolve()
        return GuardrailPolicy(
            workspace_root=ws_root,
            fail_closed=fail_closed,
            anti_laziness_enabled=anti_laziness,
        )


# Blueprint and backward-compatibility alias
RuleEnforcer = RuleLoader
