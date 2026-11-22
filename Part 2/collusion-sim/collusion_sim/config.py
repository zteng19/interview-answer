"""Loads the versioned YAML configuration (profiles, world, annotations, run limits, scenarios)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_yaml(path: str | Path) -> dict:
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


@dataclass
class Config:
    profiles: dict
    world: dict
    annotations: dict
    run: dict
    scenarios: dict

    @property
    def actions(self) -> dict:
        return self.profiles["actions"]

    @property
    def limits(self) -> dict:
        return self.run["limits"]

    def versions(self) -> dict[str, str]:
        return {
            "schema": "card-v1",
            "profiles": self.profiles["version"],
            "world": self.world["version"],
            "annotations": self.annotations["version"],
            "run": self.run["version"],
        }

    def scenario(self, scenario_id: str) -> dict:
        for s in self.scenarios["scenarios"]:
            if s["id"] == scenario_id:
                return s
        raise KeyError(f"unknown scenario {scenario_id}")


def load_config(overrides: dict | None = None) -> Config:
    cfg = Config(
        profiles=load_yaml("config/profiles.yaml"),
        world=load_yaml("config/world.yaml"),
        annotations=load_yaml("config/mo_annotations.yaml"),
        run=load_yaml("config/run.yaml"),
        scenarios=load_yaml("config/scenarios.yaml"),
    )
    if overrides:
        for k, v in overrides.get("limits", {}).items():
            cfg.run["limits"][k] = v
    return cfg
