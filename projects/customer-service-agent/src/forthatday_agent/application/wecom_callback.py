from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol


class DecryptedCallback(Protocol):
    callback_token: str
    open_kfid: str
    event_type: str


class CallbackCrypto(Protocol):
    def decrypt_url_verification(
        self, msg_signature: str, timestamp: str, nonce: str, echo_str: str
    ) -> str: ...

    def decrypt_callback(
        self, msg_signature: str, timestamp: str, nonce: str, encrypted_xml: bytes
    ) -> DecryptedCallback: ...


class CallbackJobStore(Protocol):
    def enqueue_wecom_callback(
        self,
        deduplication_key: str,
        callback_token: str,
        open_kfid: str,
    ) -> bool: ...

    def claim_wecom_callback(
        self, worker_id: str, now: datetime
    ) -> WeComCallbackJob | None: ...

    def complete_wecom_callback(self, job_id: str) -> None: ...

    def retry_wecom_callback(
        self,
        job: WeComCallbackJob,
        error: str,
        available_at: datetime,
        max_attempts: int,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class WeComCallbackJob:
    id: str
    callback_token: str
    open_kfid: str
    attempt_count: int


class CallbackSyncService(Protocol):
    def sync(self, callback_token: str, open_kfid: str) -> int: ...


class WeComCallbackReceiver:
    def __init__(self, crypto: CallbackCrypto, store: CallbackJobStore) -> None:
        self.crypto = crypto
        self.store = store

    def verify_url(self, query: dict[str, str]) -> str:
        return self.crypto.decrypt_url_verification(
            _required(query, "msg_signature"),
            _required(query, "timestamp"),
            _required(query, "nonce"),
            _required(query, "echostr"),
        )

    def receive(self, query: dict[str, str], body: bytes) -> bool:
        signature = _required(query, "msg_signature")
        timestamp = _required(query, "timestamp")
        nonce = _required(query, "nonce")
        event = self.crypto.decrypt_callback(signature, timestamp, nonce, body)
        if event.event_type != "kf_msg_or_event":
            return False
        if not event.callback_token or not event.open_kfid:
            raise ValueError("WeCom customer-service event is missing token or open_kfid")
        deduplication_key = hashlib.sha256(
            f"{signature}:{timestamp}:{nonce}".encode("utf-8")
        ).hexdigest()
        return self.store.enqueue_wecom_callback(
            deduplication_key,
            event.callback_token,
            event.open_kfid,
        )


class WeComCallbackWorker:
    def __init__(
        self,
        store: CallbackJobStore,
        sync_service: CallbackSyncService,
        *,
        max_attempts: int = 8,
    ) -> None:
        self.store = store
        self.sync_service = sync_service
        self.max_attempts = max_attempts

    def process_once(self, worker_id: str, now: datetime | None = None) -> bool:
        current_time = now or datetime.now(timezone.utc)
        job = self.store.claim_wecom_callback(worker_id, current_time)
        if job is None:
            return False
        try:
            self.sync_service.sync(job.callback_token, job.open_kfid)
            self.store.complete_wecom_callback(job.id)
        except Exception as error:
            self.store.retry_wecom_callback(
                job,
                f"{type(error).__name__}: {error}"[:1000],
                current_time + _retry_delay(job.attempt_count),
                self.max_attempts,
            )
        return True


def _required(query: dict[str, str], key: str) -> str:
    value = str(query.get(key) or "")
    if not value:
        raise ValueError(f"WeCom callback query parameter is missing: {key}")
    return value


def _retry_delay(attempt_count: int) -> timedelta:
    return timedelta(seconds=min(300, 2 ** max(0, attempt_count - 1)))
