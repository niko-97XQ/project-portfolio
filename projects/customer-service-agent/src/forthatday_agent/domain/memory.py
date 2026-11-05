from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from .appointments import utc_now


class FactSource(StrEnum):
    CUSTOMER_EXPLICIT = "customer_explicit"
    STAFF_CONFIRMED = "staff_confirmed"
    AI_INFERRED = "ai_inferred"


PROFILE_FIELDS = {
    "customer_name",
    "customer_phone",
    "wedding_date",
    "venue",
    "budget_range",
    "style_preferences",
}


@dataclass(frozen=True, slots=True)
class MemoryFact:
    key: str
    value: Any
    source: FactSource
    evidence_message_id: str = ""
    confidence: float = 1.0
    updated_at: datetime = field(default_factory=utc_now)
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.key not in PROFILE_FIELDS:
            raise ValueError(f"unsupported profile field: {self.key}")
        if not 0 <= self.confidence <= 1:
            raise ValueError("memory confidence must be between 0 and 1")
        if self.source is FactSource.AI_INFERRED and self.confidence >= 1:
            raise ValueError("inferred facts cannot have absolute confidence")


@dataclass(frozen=True, slots=True)
class ConversationMemory:
    summary: str = ""
    facts: tuple[MemoryFact, ...] = ()
    pending_tasks: tuple[str, ...] = ()
    summary_through_message_id: str = ""

    def active_facts(self, now: datetime | None = None) -> tuple[MemoryFact, ...]:
        current_time = now or utc_now()
        return tuple(
            fact for fact in self.facts if fact.expires_at is None or fact.expires_at > current_time
        )

    def as_prompt_data(self) -> dict[str, Any]:
        confirmed: dict[str, Any] = {}
        inferred: dict[str, dict[str, Any]] = {}
        for fact in self.active_facts():
            if fact.source is FactSource.AI_INFERRED:
                inferred[fact.key] = {
                    "value": fact.value,
                    "confidence": fact.confidence,
                }
            else:
                confirmed[fact.key] = fact.value
        return {
            "summary": self.summary,
            "confirmed_facts": confirmed,
            "inferred_preferences": inferred,
            "pending_tasks": list(self.pending_tasks),
        }

