from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol

from service_agent.domain.conversations import ConversationOwner
from service_agent.domain.dialogue import DialogueDecision
from service_agent.domain.messages import (
    InboundMessage,
    InboxBatch,
    OutboxMessage,
    ReceiveResult,
    StoredMessage,
)


class MessageStore(Protocol):
    def ingest(self, message: InboundMessage, debounce_seconds: float) -> ReceiveResult: ...

    def claim_inbox_batch(self, worker_id: str, now: datetime) -> InboxBatch | None: ...

    def conversation_owner(self, conversation_id: str) -> ConversationOwner: ...

    def complete_inbox_batch(self, batch: InboxBatch, reply_text: str | None) -> None: ...

    def complete_dialogue_batch(
        self,
        batch: InboxBatch,
        decision: DialogueDecision,
    ) -> None: ...

    def retry_inbox_batch(
        self,
        batch: InboxBatch,
        error: str,
        available_at: datetime,
        max_attempts: int,
    ) -> None: ...

    def terminal_inbox_failure(
        self,
        batch: InboxBatch,
        error: str,
        fallback_reply: str,
    ) -> None: ...

    def claim_outbox(self, worker_id: str, now: datetime) -> OutboxMessage | None: ...

    def mark_outbox_sent(
        self,
        message: OutboxMessage,
        external_message_id: str,
        sent_at: datetime,
    ) -> None: ...

    def retry_outbox(
        self,
        message: OutboxMessage,
        error: str,
        available_at: datetime,
        max_attempts: int,
    ) -> None: ...

    def cancel_outbox(self, message: OutboxMessage, reason: str) -> None: ...

    def recover_stale(self, stale_before: datetime, now: datetime) -> tuple[int, int]: ...


class ReplyEngine(Protocol):
    def reply(
        self,
        conversation_id: str,
        messages: tuple[StoredMessage, ...],
    ) -> str | DialogueDecision | None: ...


class DeliveryGateway(Protocol):
    def send(self, message: OutboxMessage) -> str: ...


class ReliableMessagePipeline:
    def __init__(
        self,
        store: MessageStore,
        reply_engine: ReplyEngine,
        delivery_gateway: DeliveryGateway,
        *,
        debounce_seconds: float = 1.2,
        max_attempts: int = 5,
    ) -> None:
        self.store = store
        self.reply_engine = reply_engine
        self.delivery_gateway = delivery_gateway
        self.debounce_seconds = debounce_seconds
        self.max_attempts = max_attempts

    def receive(self, message: InboundMessage) -> ReceiveResult:
        return self.store.ingest(message, self.debounce_seconds)

    def process_once(self, worker_id: str, now: datetime | None = None) -> bool:
        current_time = now or datetime.now(timezone.utc)
        batch = self.store.claim_inbox_batch(worker_id, current_time)
        if batch is None:
            return False

        try:
            if self.store.conversation_owner(batch.conversation_id) is not ConversationOwner.AI:
                self.store.complete_inbox_batch(batch, None)
                return True

            result = self.reply_engine.reply(batch.conversation_id, batch.messages)
            if isinstance(result, DialogueDecision):
                self.store.complete_dialogue_batch(batch, result)
            else:
                self.store.complete_inbox_batch(batch, result)
        except Exception as error:
            safe_error = _safe_error(error)
            if batch.max_attempt_count >= self.max_attempts:
                self.store.terminal_inbox_failure(
                    batch,
                    safe_error,
                    "收到呀，这个我帮你确认一下，稍后回复你哈～",
                )
            else:
                delay = _retry_delay(batch.max_attempt_count)
                self.store.retry_inbox_batch(
                    batch,
                    safe_error,
                    current_time + delay,
                    self.max_attempts,
                )
        return True

    def dispatch_once(self, worker_id: str, now: datetime | None = None) -> bool:
        current_time = now or datetime.now(timezone.utc)
        message = self.store.claim_outbox(worker_id, current_time)
        if message is None:
            return False

        try:
            if (
                message.actor.value == "ai"
                and self.store.conversation_owner(message.conversation_id)
                is not ConversationOwner.AI
            ):
                self.store.cancel_outbox(message, "conversation is no longer AI-owned")
                return True
            external_message_id = self.delivery_gateway.send(message)
            self.store.mark_outbox_sent(message, external_message_id, current_time)
        except Exception as error:
            delay = _retry_delay(message.attempt_count)
            self.store.retry_outbox(
                message,
                _safe_error(error),
                current_time + delay,
                self.max_attempts,
            )
        return True

    def recover_stale_work(
        self,
        *,
        stale_after: timedelta = timedelta(minutes=2),
        now: datetime | None = None,
    ) -> tuple[int, int]:
        current_time = now or datetime.now(timezone.utc)
        return self.store.recover_stale(current_time - stale_after, current_time)


def _retry_delay(attempt_count: int) -> timedelta:
    seconds = min(300, 2 ** max(0, attempt_count - 1))
    return timedelta(seconds=seconds)


def _safe_error(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"[:1000]
