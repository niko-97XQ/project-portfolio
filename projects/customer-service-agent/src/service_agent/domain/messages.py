from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from .appointments import utc_now


class MessageActor(StrEnum):
    CUSTOMER = "customer"
    AI = "ai"
    STAFF = "staff"
    SYSTEM = "system"


class InboxStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    RETRY = "retry"
    DEAD = "dead"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    CANCELLED = "cancelled"
    RETRY = "retry"
    DEAD = "dead"


@dataclass(frozen=True, slots=True)
class InboundMessage:
    channel: str
    channel_account_id: str
    external_user_id: str
    external_message_id: str
    message_type: str
    text_content: str = ""
    raw_payload: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=utc_now)
    actor: MessageActor = MessageActor.CUSTOMER


@dataclass(frozen=True, slots=True)
class StoredMessage:
    id: str
    conversation_id: str
    external_message_id: str
    actor: MessageActor
    message_type: str
    text_content: str
    raw_payload: dict[str, Any]
    occurred_at: datetime
    received_at: datetime


@dataclass(frozen=True, slots=True)
class ReceiveResult:
    conversation_id: str
    message_id: str
    duplicate: bool


@dataclass(frozen=True, slots=True)
class InboxBatch:
    conversation_id: str
    job_ids: tuple[str, ...]
    messages: tuple[StoredMessage, ...]
    max_attempt_count: int


@dataclass(frozen=True, slots=True)
class OutboxMessage:
    id: str
    conversation_id: str
    idempotency_key: str
    actor: MessageActor
    message_type: str
    payload: dict[str, Any]
    attempt_count: int


def new_id() -> str:
    return str(uuid4())
