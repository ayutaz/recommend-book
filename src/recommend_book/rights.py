"""Purpose-specific rights decisions. Missing policies and fields fail closed."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

UsageState = Literal["allowed", "conditional", "denied", "unresolved"]
USAGES = ("acquire", "local_storage", "external_llm", "commercial", "display", "redistribution")


class RightsPolicy(BaseModel):
    policy_id: str
    source: str
    provider_id: str
    fields: list[str]
    license: str | None = None
    usages: dict[str, UsageState]
    obligations: list[str] = Field(default_factory=list)
    attribution: str | None = None
    retention: str = "No source-imposed deadline verified; retain provenance with data."
    verified_at: str
    evidence_urls: list[str]
    scope: str


class RightsRegistry:
    def __init__(self, policies: list[RightsPolicy]):
        self.policies = {p.policy_id: p for p in policies}

    @classmethod
    def from_file(cls, path: str | Path) -> "RightsRegistry":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls([RightsPolicy.model_validate(p) for p in data["rights_policies"]])

    @classmethod
    def default(cls) -> "RightsRegistry":
        # Installed wheels include a fallback policy copy; repo configuration is authoritative.
        path = Path(__file__).resolve().parents[2] / "config" / "sources.json"
        if not path.exists():
            path = Path(__file__).with_name("ingestion") / "sources.json"
        return cls.from_file(path)

    def permits(self, policy_id: str, field: str, usage: str, fulfilled: set[str] | None = None) -> bool:
        policy = self.policies.get(policy_id)
        if policy is None or field not in policy.fields or usage not in USAGES:
            return False
        state = policy.usages.get(usage, "unresolved")
        if state == "allowed":
            return True
        return state == "conditional" and set(policy.obligations) <= (fulfilled or set())

    def for_provider(self, source: str, provider_id: str, field: str) -> RightsPolicy | None:
        return next((p for p in self.policies.values() if p.source == source and p.provider_id == provider_id and field in p.fields), None)
