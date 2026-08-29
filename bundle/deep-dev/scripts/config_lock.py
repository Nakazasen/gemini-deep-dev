"""
.deep_dev Engine: Config Lock Module (config_lock.py)
=====================================================
Loads, validates, canonicalizes, and hash-locks user-controlled test configuration
from .deep_dev/config.json. Enforces strict relative cwd paths.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ValidationError, field_validator

try:
    from .path_utils import canonicalize_safe_relative_path, PathSecurityError
except ImportError:
    from path_utils import canonicalize_safe_relative_path, PathSecurityError


class AllowlistCommand(BaseModel):
    executable: str = Field(..., min_length=1)
    args: List[str] = Field(default_factory=list)
    cwd: str = Field(default=".")
    timeout_seconds: int = Field(default=60, gt=0, le=600)
    minimum_test_count: Optional[int] = Field(default=None, ge=1, le=100000)

    @field_validator("cwd")
    @classmethod
    def validate_cwd(cls, v: str) -> str:
        try:
            return canonicalize_safe_relative_path(v, allow_root_dot=True)
        except PathSecurityError as pse:
            raise ValueError(f"Invalid cwd path in test command: {pse}") from pse


class DeepDevTestConfig(BaseModel):
    version: str = Field(default="1.0")
    allowlisted_test_commands: Dict[str, AllowlistCommand] = Field(default_factory=dict)


class ConfigLockResult(BaseModel):
    config: DeepDevTestConfig
    config_sha256: str
    raw_json: str
    is_empty: bool = False


class ConfigLockError(Exception):
    """Raised when config is missing, malformed, or tampered with."""
    pass


def load_and_lock_config(config_path: Path) -> ConfigLockResult:
    """
    Load test config, validate against schema, and compute canonical SHA-256 hash.
    """
    if not config_path.exists():
        empty_config = DeepDevTestConfig()
        raw = empty_config.model_dump_json(indent=2)
        h = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return ConfigLockResult(
            config=empty_config,
            config_sha256=h,
            raw_json=raw,
            is_empty=True,
        )

    try:
        content = config_path.read_text(encoding="utf-8")
        data = json.loads(content)
        config = DeepDevTestConfig.model_validate(data)

        canonical_bytes = json.dumps(config.model_dump(mode="json"), sort_keys=True).encode("utf-8")
        config_hash = hashlib.sha256(canonical_bytes).hexdigest()

        return ConfigLockResult(
            config=config,
            config_sha256=config_hash,
            raw_json=content,
            is_empty=len(config.allowlisted_test_commands) == 0,
        )
    except json.JSONDecodeError as jde:
        raise ConfigLockError(f"Invalid JSON in config file {config_path}: {jde}") from jde
    except ValidationError as ve:
        raise ConfigLockError(f"Schema validation failed for {config_path}: {ve}") from ve
    except Exception as exc:
        raise ConfigLockError(f"Failed to load config {config_path}: {exc}") from exc


def verify_config_hash(config_path: Path, expected_hash: str) -> bool:
    """
    Verify whether the on-disk config matches the locked SHA-256 hash.
    """
    try:
        res = load_and_lock_config(config_path)
        return res.config_sha256 == expected_hash
    except Exception:
        return False
