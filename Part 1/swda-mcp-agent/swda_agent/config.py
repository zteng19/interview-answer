"""Settings from environment variables. No secrets live in code or config files.

Client tokens are passed in at run time (SWDA_CLIENT_TOKEN); the registry stores only
their SHA-256 hashes. In production, clients use OAuth2 client credentials from Cognito (10c).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    registry_path: Path
    catalogue_path: Path
    rules_path: Path
    trace_path: Path
    audit_path: Path
    max_payload_bytes: int
    tool_timeout_s: float

    @classmethod
    def from_env(cls) -> Settings:
        def path(var: str, default: str) -> Path:
            return Path(os.environ.get(var, str(ROOT / default)))

        return cls(
            registry_path=path("SWDA_REGISTRY_PATH", "config/registry.yaml"),
            catalogue_path=path("SWDA_CATALOGUE_PATH", "data/programmes.csv"),
            rules_path=path("SWDA_RULES_PATH", "config/classifier_rules.yaml"),
            trace_path=path("SWDA_TRACE_PATH", "out/traces.jsonl"),
            audit_path=path("SWDA_AUDIT_PATH", "out/audit.jsonl"),
            max_payload_bytes=int(os.environ.get("SWDA_MAX_PAYLOAD_BYTES", "16384")),
            tool_timeout_s=float(os.environ.get("SWDA_TOOL_TIMEOUT_S", "2.0")),
        )
