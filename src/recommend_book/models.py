"""Shared data contracts. Unknown facts remain null; source records are immutable."""
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Identifier(Contract):
    source: str
    value: str
    scope: Literal["work", "expression", "edition", "agent", "series", "record"]


class Title(Contract):
    value: str
    language: str | None = None
    kind: Literal["preferred", "original", "translated", "alternative"] = "preferred"


class Contributor(Contract):
    name: str
    role: Literal["author", "translator", "editor", "illustrator", "other"] = "author"
    identifiers: list[Identifier] = Field(default_factory=list)


class Evidence(Contract):
    evidence_id: str
    kind: Literal["bibliographic", "subject", "synopsis", "excerpt", "note", "description"]
    text: str
    source_url: str
    rights_policy_id: str
    llm_input_allowed: bool = False
    language: str | None = None
    locator: str | None = None
    expression_ref: str | None = None
    license: str | None = None
    license_url: str | None = None
    attribution: dict = Field(default_factory=dict)
    inherited_restrictions: list[str] = Field(default_factory=list)


class SourceRecord(Contract):
    source: str
    source_id: str
    source_url: str
    retrieved_at: str
    provider_id: str
    record_kind: Literal["work", "edition", "agent"]
    titles: list[Title] = Field(default_factory=list)
    contributors: list[Contributor] = Field(default_factory=list)
    identifiers: list[Identifier] = Field(default_factory=list)
    work_refs: list[Identifier] = Field(default_factory=list)
    original_language: str | None = None
    language: str | None = None
    first_publication_year: int | None = None
    publication_year: int | None = None
    publisher: str | None = None
    series: str | None = None
    volume: str | None = None
    work_kind: str | None = None
    region: list[str] = Field(default_factory=list)
    subjects: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    contents: list[Identifier] = Field(default_factory=list)
    raw_sha256: str
    raw_path: str
    rights_policy_id: str
    extra: dict[str, Any] = Field(default_factory=dict)


class WorkInput(Contract):
    work_id: str
    expression_id: str | None = None
    title: str
    authors: list[str] = Field(default_factory=list)
    original_language: str | None = None
    first_publication_year: int | None = None
    region: list[str] = Field(default_factory=list)
    series: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    source_record_ids: list[str] = Field(default_factory=list)
