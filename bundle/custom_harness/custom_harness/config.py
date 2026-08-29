"""
Custom Harness Global Configuration
===================================
Settings and environment configuration loader for Antigravity Deterministic Harness.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Union
from pydantic import BaseModel, Field, field_validator
from .harness.models import HarnessConfig


class HarnessSettings(BaseModel):
    """Global configuration settings for Antigravity custom harness."""
    model_name: str = Field(default="gemini-2.5-flash", description="Gemini Flash model name")
    api_key: Optional[str] = Field(default=None, description="Gemini or Google GenAI API key")
    workspace_root: Path = Field(default_factory=Path.cwd, description="Target workspace root directory")
    max_turns: int = Field(default=5, ge=1, le=20, description="Max feedback loop turns")
    max_retries: int = Field(default=3, ge=0, le=10, description="Max schema reflection retries")
    quality_threshold: float = Field(default=0.8, ge=0.0, le=1.0, description="Approval score threshold")
    temperature: float = Field(default=0.2, ge=0.0, le=2.0, description="Sampling temperature")
    top_p: float = Field(default=0.95, ge=0.0, le=1.0, description="Top-p sampling")
    enable_guardrails: bool = Field(default=True, description="Enable fail-closed declarative guardrails")
    enable_grounding: bool = Field(default=True, description="Enable MCP documentation grounding")
    mcp_docs_url: str = Field(default="https://antigravity.google/docs", description="Official docs URL")
    use_mock: bool = Field(default=False, description="Force deterministic mock mode without API calls")

    @field_validator("workspace_root", mode="after")
    @classmethod
    def canonicalize_workspace(cls, v: Union[str, Path]) -> Path:
        return Path(v).resolve()

    def to_harness_config(self) -> HarnessConfig:
        """Convert settings to strict HarnessConfig model."""
        return HarnessConfig(
            model_name=self.model_name,
            max_turns=self.max_turns,
            max_retries=self.max_retries,
            quality_threshold=self.quality_threshold,
            temperature=self.temperature,
            top_p=self.top_p,
            enable_guardrails=self.enable_guardrails,
            enable_grounding=self.enable_grounding,
            workspace_root=self.workspace_root,
        )


def load_settings(
    config_file: Optional[Union[str, Path]] = None,
    workspace_root: Optional[Union[str, Path]] = None
) -> HarnessSettings:
    """
    Load settings from configuration file, environment variables, and defaults.
    Priority: explicit config_file > environment variables > defaults.
    """
    ws = Path(workspace_root).resolve() if workspace_root else Path.cwd().resolve()
    settings_dict: Dict[str, Any] = {
        "workspace_root": ws,
        "api_key": os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"),
        "model_name": os.environ.get("ANTIGRAVITY_MODEL", "gemini-2.5-flash"),
        "enable_guardrails": os.environ.get("ANTIGRAVITY_GUARDRAILS", "1").lower() not in ("0", "false", "no"),
        "enable_grounding": os.environ.get("ANTIGRAVITY_GROUNDING", "1").lower() not in ("0", "false", "no"),
        "use_mock": os.environ.get("ANTIGRAVITY_MOCK", "0").lower() in ("1", "true", "yes"),
    }

    # Try explicit or discovered config file
    candidate_paths = []
    if config_file:
        candidate_paths.append(Path(config_file))
    else:
        candidate_paths.extend([
            ws / "harness_config.json",
            ws / ".antigravity" / "harness.json",
            ws / "custom_harness.json",
        ])

    for cp in candidate_paths:
        if cp.is_file():
            try:
                with open(cp, "r", encoding="utf-8") as f:
                    file_data = json.load(f)
                    settings_dict.update(file_data)
                break
            except Exception:
                pass

    return HarnessSettings(**settings_dict)
