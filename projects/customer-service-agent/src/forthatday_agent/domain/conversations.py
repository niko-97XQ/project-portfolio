from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from .appointments import DomainRuleError, utc_now


class ConversationOwner(StrEnum):
    AI = "ai"
    HUMAN_PENDING = "human_pending"
    HUMAN = "human"


class HandoffStatus(StrEnum):
    NONE = "none"
    REQUESTED = "requested"
    ACTIVE = "active"
    FAILED = "failed"
    RETURN_PENDING = "return_pending"


@dataclass(slots=True)
class Conversation:
    channel: str
    external_user_id: str
    channel_account_id: str
    id: str = field(default_factory=lambda: str(uuid4()))
    owner: ConversationOwner = ConversationOwner.AI
    handoff_status: HandoffStatus = HandoffStatus.NONE
    handoff_reason: str = ""
    assigned_staff_id: str = ""
    handoff_summary: str = ""
    last_message_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    @property
    def can_ai_reply(self) -> bool:
        return self.owner is ConversationOwner.AI and self.handoff_status in {
            HandoffStatus.NONE,
            HandoffStatus.FAILED,
        }

    def request_handoff(self, reason: str) -> None:
        if self.owner is not ConversationOwner.AI:
            raise DomainRuleError("only an AI-owned conversation can request handoff")
        if not reason.strip():
            raise DomainRuleError("handoff reason is required")
        # Block AI before calling WeCom so no reply can race the transfer.
        self.owner = ConversationOwner.HUMAN_PENDING
        self.handoff_status = HandoffStatus.REQUESTED
        self.handoff_reason = reason.strip()
        self._touch()

    def activate_human(self, staff_id: str = "") -> None:
        if self.owner is not ConversationOwner.HUMAN_PENDING:
            raise DomainRuleError("handoff must be requested before human activation")
        self.owner = ConversationOwner.HUMAN
        self.handoff_status = HandoffStatus.ACTIVE
        self.assigned_staff_id = staff_id.strip()
        self._touch()

    def fail_handoff(self, resume_ai: bool) -> None:
        if self.owner is not ConversationOwner.HUMAN_PENDING:
            raise DomainRuleError("only a pending handoff can fail")
        self.handoff_status = HandoffStatus.FAILED
        self.owner = ConversationOwner.AI if resume_ai else ConversationOwner.HUMAN_PENDING
        self._touch()

    def request_return_to_ai(self, summary: str) -> None:
        if self.owner is not ConversationOwner.HUMAN:
            raise DomainRuleError("only a human-owned conversation can return to AI")
        if not summary.strip():
            raise DomainRuleError("handoff summary is required before returning to AI")
        self.handoff_summary = summary.strip()
        self.handoff_status = HandoffStatus.RETURN_PENDING
        self._touch()

    def activate_ai(self) -> None:
        if self.handoff_status is not HandoffStatus.RETURN_PENDING:
            raise DomainRuleError("return to AI must be requested first")
        self.owner = ConversationOwner.AI
        self.handoff_status = HandoffStatus.NONE
        self.handoff_reason = ""
        self.assigned_staff_id = ""
        self._touch()

    def record_message(self, sent_at: datetime | None = None) -> None:
        self.last_message_at = sent_at or utc_now()
        self._touch()

    def _touch(self) -> None:
        self.updated_at = utc_now()

