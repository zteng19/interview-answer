"""Registry stored as YAML. Production: Amazon DynamoDB table (component 10b)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import yaml

from ..models import Registry


class YamlRegistryStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> Registry:
        with self.path.open(encoding="utf-8") as f:
            return Registry.model_validate(yaml.safe_load(f))

    def save(self, registry: Registry) -> None:
        data = registry.model_dump(mode="json")
        # Write to a temp file then replace, so a crash never leaves a half-written registry.
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
        os.replace(tmp, self.path)
