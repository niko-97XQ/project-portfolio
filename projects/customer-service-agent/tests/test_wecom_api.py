from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from forthatday_agent.domain.messages import (  # noqa: E402
    InboundMessage,
    MessageActor,
    OutboxMessage,
)
from forthatday_agent.domain.conversations import ConversationOwner  # noqa: E402
from forthatday_agent.application.wecom_sync import WeComSyncService  # noqa: E402
from forthatday_agent.application.staff_access import StaffActionSigner  # noqa: E402
from forthatday_agent.infrastructure.sqlite_message_store import (  # noqa: E402
    SqliteMessageStore,
)
from forthatday_agent.infrastructure.wecom_api import (  # noqa: E402
    SyncedMessages,
    WeComApiClient,
    WeComApiError,
    WeComCustomerServiceGateway,
    WeComNativeHandoffGateway,
    WeComSessionStateTransitionUnavailable,
)


class FakeTransport:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict, dict | None]] = []
        self.uploads: list[Path] = []
        self.token_calls = 0
        self.service_state_response = {"errcode": 0, "errmsg": "ok"}
        self.service_state_get_response = {"errcode": 0, "service_state": 4}
        self.sync_response = {
            "errcode": 0,
            "next_cursor": "cursor-2",
            "has_more": 1,
            "msg_list": [
                {
                    "msgid": "customer-message-1",
                    "open_kfid": "wk-1",
                    "external_userid": "wm-1",
                    "origin": 3,
                    "msgtype": "text",
                    "text": {"content": "你好"},
                }
            ],
        }

    def request_json(self, method, path, query, body=None):
        self.requests.append((method, path, query, body))
        if path == "/cgi-bin/gettoken":
            self.token_calls += 1
            return {"errcode": 0, "access_token": "token-1", "expires_in": 7200}
        if path == "/cgi-bin/kf/sync_msg":
            return self.sync_response
        if path == "/cgi-bin/kf/send_msg":
            return {"errcode": 0, "errmsg": "ok", "msgid": "wecom-sent-1"}
        if path == "/cgi-bin/kf/service_state/trans":
            return self.service_state_response
        if path == "/cgi-bin/kf/service_state/get":
            return self.service_state_get_response
        if path == "/cgi-bin/message/send":
            return {"errcode": 0, "errmsg": "ok", "msgid": "staff-message-1"}
        raise AssertionError(f"unexpected path: {path}")

    def download(self, path, query):
        return b"image-content"

    def upload(self, path, query, file_path):
        self.uploads.append(file_path)
        return {"errcode": 0, "errmsg": "ok", "media_id": "media-1"}


class StaticAddressStore:
    def channel_address(self, conversation_id: str):
        return ("wecom_kf", "wk-1", "wm-1")


class StaticCatalogImages:
    def __init__(self, image_path: Path) -> None:
        self.image_path = image_path
        self.calls: list[tuple[str, int]] = []

    def catalog_image_path(self, model_code: str, position: int = 0) -> Path:
        self.calls.append((model_code, position))
        return self.image_path


class FakeSyncClient:
    def __init__(self, messages: tuple[dict, ...], *, media: bytes = b"image") -> None:
        self.messages = messages
        self.media = media
        self.cursors: list[str] = []
        self.downloads: list[str] = []

    def sync_messages(self, callback_token, open_kfid, cursor="", *, limit=1000):
        self.cursors.append(cursor)
        return SyncedMessages(self.messages, "cursor-next", False)

    def download_media(self, media_id: str) -> bytes:
        self.downloads.append(media_id)
        if isinstance(self.media, Exception):
            raise self.media
        return self.media


class WeComApiTests(unittest.TestCase):
    def test_access_token_is_cached_before_expiry(self) -> None:
        transport = FakeTransport()
        client = WeComApiClient("corp-1", "secret-1", transport, clock=lambda: 1000)

        self.assertEqual(client.access_token(), "token-1")
        self.assertEqual(client.access_token(), "token-1")

        self.assertEqual(transport.token_calls, 1)

    def test_sync_message_passes_callback_token_cursor_and_account(self) -> None:
        transport = FakeTransport()
        client = WeComApiClient("corp-1", "secret-1", transport, clock=lambda: 1000)

        result = client.sync_messages("callback-token", "wk-1", "cursor-1")

        self.assertTrue(result.has_more)
        self.assertEqual(result.next_cursor, "cursor-2")
        self.assertEqual(result.messages[0]["msgid"], "customer-message-1")
        sync_request = transport.requests[-1]
        self.assertEqual(sync_request[3]["token"], "callback-token")
        self.assertEqual(sync_request[3]["cursor"], "cursor-1")
        self.assertEqual(sync_request[3]["open_kfid"], "wk-1")

    def test_gateway_sends_text_to_conversation_channel_address(self) -> None:
        transport = FakeTransport()
        client = WeComApiClient("corp-1", "secret-1", transport, clock=lambda: 1000)
        gateway = WeComCustomerServiceGateway(client, StaticAddressStore())
        message = OutboxMessage(
            id="outbox-1",
            conversation_id="conversation-1",
            idempotency_key="reply:customer-message-1",
            actor=MessageActor.AI,
            message_type="text",
            payload={"text": "你好呀"},
            attempt_count=1,
        )

        external_id = gateway.send(message)

        self.assertEqual(external_id, "wecom-sent-1")
        send_body = transport.requests[-1][3]
        self.assertEqual(send_body["touser"], "wm-1")
        self.assertEqual(send_body["open_kfid"], "wk-1")
        self.assertEqual(send_body["text"]["content"], "你好呀")
        self.assertTrue(send_body["msgid"].startswith("ftd_"))
        self.assertLessEqual(len(send_body["msgid"]), 32)
        self.assertNotIn(":", send_body["msgid"])

    def test_change_service_state_assigns_native_staff(self) -> None:
        transport = FakeTransport()
        client = WeComApiClient("corp-1", "secret-1", transport, clock=lambda: 1000)

        client.change_service_state("wm-1", "wk-1", 3, servicer_userid="staff-1")

        method, path, _, body = transport.requests[-1]
        self.assertEqual(method, "POST")
        self.assertEqual(path, "/cgi-bin/kf/service_state/trans")
        self.assertEqual(
            body,
            {
                "open_kfid": "wk-1",
                "external_userid": "wm-1",
                "service_state": 3,
                "servicer_userid": "staff-1",
            },
        )

    def test_get_service_state_queries_correct_customer_and_account(self) -> None:
        transport = FakeTransport()
        transport.service_state_get_response = {"errcode": 0, "service_state": 3}
        client = WeComApiClient("corp-1", "secret-1", transport)

        self.assertEqual(client.get_service_state("wm-1", "wk-1"), 3)
        self.assertEqual(transport.requests[-1][0], "POST")
        self.assertEqual(transport.requests[-1][1], "/cgi-bin/kf/service_state/get")
        self.assertEqual(transport.requests[-1][3], {"external_userid": "wm-1", "open_kfid": "wk-1"})

    def test_get_service_state_rejects_missing_invalid_or_boolean_state(self) -> None:
        for state in (None, -1, 5, "4", True):
            with self.subTest(state=state):
                transport = FakeTransport()
                transport.service_state_get_response = {"errcode": 0, "service_state": state}
                client = WeComApiClient("corp-1", "secret-1", transport)
                with self.assertRaises(WeComApiError):
                    client.get_service_state("wm-1", "wk-1")

    def test_native_handoff_confirms_ended_session_before_local_return(self) -> None:
        transport = FakeTransport()
        transport.service_state_response = {
            "errcode": 95016,
            "errmsg": "not allow transition state",
        }
        gateway = WeComNativeHandoffGateway(
            WeComApiClient("corp-1", "secret-1", transport, clock=lambda: 1000),
            StaticAddressStore(),
            "staff-1",
        )

        with self.assertRaises(WeComSessionStateTransitionUnavailable):
            gateway.return_to_ai("conversation-1")
        self.assertEqual(transport.requests[-1][1], "/cgi-bin/kf/service_state/get")

    def test_gateway_uploads_local_image_before_sending(self) -> None:
        transport = FakeTransport()
        client = WeComApiClient("corp-1", "secret-1", transport, clock=lambda: 1000)
        gateway = WeComCustomerServiceGateway(client, StaticAddressStore())
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "wechat.jpg"
            image.write_bytes(b"payment-code")
            message = OutboxMessage(
                id="outbox-2",
                conversation_id="conversation-1",
                idempotency_key="appointment-payment:1:wechat",
                actor=MessageActor.SYSTEM,
                message_type="image",
                payload={"path": str(image), "caption": "微信收款码"},
                attempt_count=1,
            )

            gateway.send(message)

        self.assertEqual(len(transport.uploads), 1)
        send_body = transport.requests[-1][3]
        self.assertEqual(send_body["msgtype"], "image")
        self.assertEqual(send_body["image"]["media_id"], "media-1")

    def test_gateway_resolves_recommended_catalog_image_server_side(self) -> None:
        transport = FakeTransport()
        client = WeComApiClient("corp-1", "secret-1", transport, clock=lambda: 1000)
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "dress.jpg"
            image.write_bytes(b"dress-image")
            resolver = StaticCatalogImages(image)
            gateway = WeComCustomerServiceGateway(
                client,
                StaticAddressStore(),
                catalog_images=resolver,
            )
            message = OutboxMessage(
                id="outbox-catalog-1",
                conversation_id="conversation-1",
                idempotency_key="catalog-recommendation:m1:bau-01",
                actor=MessageActor.AI,
                message_type="image",
                payload={"catalog_ref": "bau-01", "position": 0},
                attempt_count=1,
            )

            gateway.send(message)

        self.assertEqual(resolver.calls, [("bau-01", 0)])
        self.assertEqual(transport.uploads, [image])

    def test_api_error_does_not_include_secret(self) -> None:
        transport = FakeTransport()
        transport.sync_response = {"errcode": 40001, "errmsg": "invalid credential"}
        client = WeComApiClient(
            "corp-1",
            "super-secret-value",
            transport,
            clock=lambda: 1000,
        )

        with self.assertRaises(WeComApiError) as caught:
            client.sync_messages("callback-token", "wk-1")

        self.assertNotIn("super-secret-value", str(caught.exception))

    def test_gateway_sends_staff_task_as_application_textcard(self) -> None:
        transport = FakeTransport()
        client = WeComApiClient("corp-1", "secret-1", transport, clock=lambda: 1000)
        signer = StaffActionSigner("s" * 32, clock=lambda: 1000)
        gateway = WeComCustomerServiceGateway(
            client,
            StaticAddressStore(),
            staff_agent_id=1000002,
            staff_user_ids=("staff-1", "staff-2"),
            staff_base_url="https://example.test",
            staff_signer=signer,
        )
        message = OutboxMessage(
            id="outbox-staff-1",
            conversation_id="conversation-1",
            idempotency_key="staff-time-confirmation:task-1",
            actor=MessageActor.SYSTEM,
            message_type="staff_textcard",
            payload={
                "task_id": "task-1",
                "title": "新的试纱时间待确认",
                "description": "客户希望预约明天下午。",
            },
            attempt_count=1,
        )

        external_id = gateway.send(message)

        self.assertEqual(external_id, "staff-message-1")
        body = transport.requests[-1][3]
        self.assertEqual(body["touser"], "staff-1|staff-2")
        self.assertEqual(body["agentid"], 1000002)
        self.assertIn("/staff/tasks/task-1?", body["textcard"]["url"])
        self.assertNotIn("客户希望", body["textcard"]["url"])

    def test_invalid_staff_recipient_is_retried_instead_of_marked_sent(self) -> None:
        transport = FakeTransport()
        original_request = transport.request_json

        def request_json(method, path, query, body=None):
            if path == "/cgi-bin/message/send":
                return {"errcode": 0, "errmsg": "ok", "invaliduser": "missing-user"}
            return original_request(method, path, query, body)

        transport.request_json = request_json
        client = WeComApiClient("corp-1", "secret-1", transport, clock=lambda: 1000)

        with self.assertRaisesRegex(WeComApiError, "recipients are invalid"):
            client.send_application_textcard(
                ("missing-user",),
                1000002,
                "待确认",
                "请确认时间",
                "https://example.test/staff/tasks/task-1",
            )


class WeComSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.store = SqliteMessageStore(root / "wecom.db")
        self.media_root = root / "media"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def customer_text(msgid: str = "msg-1") -> dict:
        return {
            "msgid": msgid,
            "open_kfid": "wk-1",
            "external_userid": "wm-1",
            "send_time": 1785196800,
            "origin": 3,
            "msgtype": "text",
            "text": {"content": "你好，我想试婚纱"},
        }

    def test_customer_text_is_deduplicated_and_cursor_is_persisted(self) -> None:
        client = FakeSyncClient((self.customer_text(),))
        service = WeComSyncService(client, self.store, self.media_root, debounce_seconds=0)

        self.assertEqual(service.sync("callback-token", "wk-1"), 1)
        self.assertEqual(service.sync("callback-token", "wk-1"), 1)

        self.assertEqual(client.cursors, ["", "cursor-next"])
        self.assertEqual(self.store.sync_cursor("wecom_kf", "wk-1"), "cursor-next")
        self.assertEqual(self.store.status_counts("inbox_jobs"), {"pending": 1})

    def test_native_staff_reply_blocks_ai_without_creating_staff_inbox_job(self) -> None:
        staff = {
            "msgid": "staff-msg-1",
            "open_kfid": "wk-1",
            "external_userid": "wm-1",
            "send_time": 1785196810,
            "origin": 5,
            "servicer_userid": "staff-1",
            "msgtype": "text",
            "text": {"content": "你好，我来帮你确认"},
        }
        client = FakeSyncClient((self.customer_text(), staff))
        WeComSyncService(client, self.store, self.media_root, debounce_seconds=0).sync(
            "callback-token",
            "wk-1",
        )
        conversation_id = self.store.recent_messages(
            self.store.ingest(
                # Duplicate lookup gives us the existing conversation id.
                InboundMessage(
                    channel="wecom_kf",
                    channel_account_id="wk-1",
                    external_user_id="wm-1",
                    external_message_id="msg-1",
                    message_type="text",
                    text_content="你好，我想试婚纱",
                    occurred_at=datetime.now(timezone.utc),
                ),
                0,
            ).conversation_id
        )[0].conversation_id

        self.assertIs(
            self.store.conversation_owner(conversation_id),
            ConversationOwner.HUMAN,
        )
        self.assertEqual(self.store.status_counts("inbox_jobs"), {"pending": 1})
        self.assertEqual(len(self.store.recent_messages(conversation_id)), 2)

    def test_nonpayment_image_is_saved_and_immediately_routed_to_human(self) -> None:
        image_message = {
            "msgid": "image-msg-1",
            "open_kfid": "wk-1",
            "external_userid": "wm-image",
            "send_time": 1785196820,
            "origin": 3,
            "msgtype": "image",
            "image": {"media_id": "media-1"},
        }
        client = FakeSyncClient((image_message,), media=b"jpeg-content")

        WeComSyncService(client, self.store, self.media_root, debounce_seconds=0).sync(
            "callback-token",
            "wk-1",
        )
        files = list(self.media_root.rglob("*.jpg"))
        conversation_id = self.store.ingest(
            InboundMessage(
                channel="wecom_kf",
                channel_account_id="wk-1",
                external_user_id="wm-image",
                external_message_id="image-msg-1",
                message_type="image",
                occurred_at=datetime.now(timezone.utc),
            ),
            0,
        ).conversation_id

        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].read_bytes(), b"jpeg-content")
        self.assertIs(
            self.store.conversation_owner(conversation_id),
            ConversationOwner.HUMAN_PENDING,
        )
        self.assertEqual(self.store.status_counts("inbox_jobs"), {"done": 1})
        self.assertEqual(self.store.status_counts("outbox_messages"), {"pending": 2})

    def test_media_download_failure_does_not_advance_cursor(self) -> None:
        image_message = {
            "msgid": "image-msg-fail",
            "open_kfid": "wk-1",
            "external_userid": "wm-1",
            "send_time": 1785196820,
            "origin": 3,
            "msgtype": "image",
            "image": {"media_id": "media-fail"},
        }
        client = FakeSyncClient((image_message,), media=ConnectionError("download failed"))
        service = WeComSyncService(client, self.store, self.media_root, debounce_seconds=0)

        with self.assertRaises(ConnectionError):
            service.sync("callback-token", "wk-1")

        self.assertEqual(self.store.sync_cursor("wecom_kf", "wk-1"), "")


if __name__ == "__main__":
    unittest.main()
