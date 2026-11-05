from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from forthatday_agent.domain.messages import InboundMessage, MessageActor, ReceiveResult
from forthatday_agent.domain.payments import PaymentDecision


class WeComSyncClient(Protocol):
    def sync_messages(
        self,
        callback_token: str,
        open_kfid: str,
        cursor: str = "",
        *,
        limit: int = 1000,
    ) -> Any: ...

    def download_media(self, media_id: str) -> bytes: ...


class WeComMessageStore(Protocol):
    def sync_cursor(self, channel: str, channel_account_id: str) -> str: ...

    def save_sync_cursor(
        self, channel: str, channel_account_id: str, cursor: str
    ) -> None: ...

    def ingest(self, message: InboundMessage, debounce_seconds: float) -> ReceiveResult: ...

    def activate_native_staff(self, conversation_id: str, staff_id: str) -> None: ...

    def load_appointment_state(self, conversation_id: str) -> dict[str, object]: ...

    def route_customer_image_to_human(self, source_message_id: str) -> None: ...

    def route_customer_message_to_human(
        self, source_message_id: str, reason: str, reply_text: str
    ) -> None: ...


class PaymentVerifier(Protocol):
    def verify(self, source_message_id: str, image_path: str | Path) -> PaymentDecision: ...


class WeComSyncService:
    def __init__(
        self,
        client: WeComSyncClient,
        store: WeComMessageStore,
        media_root: str | Path,
        payment_verifier: PaymentVerifier | None = None,
        *,
        debounce_seconds: float = 1.2,
    ) -> None:
        self.client = client
        self.store = store
        self.media_root = Path(media_root)
        self.payment_verifier = payment_verifier
        self.debounce_seconds = debounce_seconds

    def sync(self, callback_token: str, open_kfid: str) -> int:
        cursor = self.store.sync_cursor("wecom_kf", open_kfid)
        processed = 0
        for _ in range(100):
            page = self.client.sync_messages(callback_token, open_kfid, cursor)
            for raw in page.messages:
                if self._process_message(raw, open_kfid):
                    processed += 1
            cursor = page.next_cursor
            self.store.save_sync_cursor("wecom_kf", open_kfid, cursor)
            if not page.has_more:
                return processed
        raise RuntimeError("WeCom sync exceeded 100 pages")

    def _process_message(self, raw: dict[str, Any], callback_open_kfid: str) -> bool:
        origin = int(raw.get("origin") or 0)
        if origin not in {3, 5}:
            return False
        external_userid = str(raw.get("external_userid") or "")
        msgid = str(raw.get("msgid") or "")
        open_kfid = str(raw.get("open_kfid") or callback_open_kfid)
        msgtype = str(raw.get("msgtype") or "")
        if not external_userid or not msgid or not open_kfid or not msgtype:
            raise ValueError("WeCom message is missing routing fields")

        actor = MessageActor.CUSTOMER if origin == 3 else MessageActor.STAFF
        text_content = ""
        payload = dict(raw)
        media_path: Path | None = None
        if msgtype == "text":
            text = raw.get("text") or {}
            text_content = str(text.get("content") or "") if isinstance(text, dict) else ""
        elif msgtype == "image" and actor is MessageActor.CUSTOMER:
            image = raw.get("image") or {}
            media_id = str(image.get("media_id") or "") if isinstance(image, dict) else ""
            if not media_id:
                raise ValueError("WeCom image message is missing media_id")
            media_path = self._save_media(open_kfid, msgid, self.client.download_media(media_id))
            payload["local_media_path"] = str(media_path)

        received = self.store.ingest(
            InboundMessage(
                channel="wecom_kf",
                channel_account_id=open_kfid,
                external_user_id=external_userid,
                external_message_id=msgid,
                message_type=msgtype,
                text_content=text_content,
                raw_payload=payload,
                occurred_at=datetime.fromtimestamp(
                    int(raw.get("send_time") or 0),
                    tz=timezone.utc,
                ),
                actor=actor,
            ),
            debounce_seconds=self.debounce_seconds,
        )

        if actor is MessageActor.STAFF:
            self.store.activate_native_staff(
                received.conversation_id,
                str(raw.get("servicer_userid") or ""),
            )
            return True
        if msgtype == "image" and media_path is not None:
            appointment = self.store.load_appointment_state(received.conversation_id)
            if (
                self.payment_verifier is not None
                and appointment.get("status") in {"awaiting_payment", "payment_review"}
            ):
                self.payment_verifier.verify(received.message_id, media_path)
            else:
                self.store.route_customer_image_to_human(received.message_id)
            return True
        if msgtype != "text":
            self.store.route_customer_message_to_human(
                received.message_id,
                f"unsupported_customer_message:{msgtype}",
                "这条消息我请店员直接看一下，稍后由店员接着和你沟通。",
            )
        return True

    def _save_media(self, open_kfid: str, msgid: str, content: bytes) -> Path:
        if not content:
            raise ValueError("downloaded WeCom media is empty")
        account_dir = hashlib.sha256(open_kfid.encode("utf-8")).hexdigest()[:16]
        filename = hashlib.sha256(msgid.encode("utf-8")).hexdigest() + ".jpg"
        directory = self.media_root / account_dir
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / filename
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(content)
        temporary.replace(target)
        return target
