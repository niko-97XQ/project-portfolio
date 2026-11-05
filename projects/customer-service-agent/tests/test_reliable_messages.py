from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from forthatday_agent.application.message_pipeline import ReliableMessagePipeline  # noqa: E402
from forthatday_agent.domain.conversations import ConversationOwner  # noqa: E402
from forthatday_agent.domain.dialogue import CatalogSignal, DialogueDecision  # noqa: E402
from forthatday_agent.domain.messages import InboundMessage, OutboxMessage  # noqa: E402
from forthatday_agent.infrastructure.catalog_store import CatalogStore  # noqa: E402
from forthatday_agent.infrastructure.sqlite_message_store import SqliteMessageStore  # noqa: E402


NOW = datetime(2026, 7, 23, 8, 0, tzinfo=timezone.utc)


class EchoReplyEngine:
    def __init__(self, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def reply(self, conversation_id: str, messages: tuple) -> str:
        self.calls.append((conversation_id, tuple(item.text_content for item in messages)))
        if self.fail_times:
            self.fail_times -= 1
            raise TimeoutError("model timeout")
        return "收到：" + " ".join(item.text_content for item in messages)


class FakeDeliveryGateway:
    def __init__(self, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.sent: list[OutboxMessage] = []

    def send(self, message: OutboxMessage) -> str:
        if self.fail_times:
            self.fail_times -= 1
            raise ConnectionError("temporary WeCom failure")
        self.sent.append(message)
        return "wecom-reply-1"


class CatalogSignalEngine:
    def reply(self, conversation_id: str, messages: tuple) -> DialogueDecision:
        return DialogueDecision(
            reply="可以先看看 Verona 无袖这款。",
            memory_updates=(),
            proposed_actions=(),
            needs_human=False,
            reason="",
            catalog_signals=(CatalogSignal("recommended", "bau-01"),),
        )


class TextOnlyDecisionEngine:
    def reply(self, conversation_id: str, messages: tuple) -> DialogueDecision:
        return DialogueDecision(
            reply="好的，我重新发一次。",
            memory_updates=(),
            proposed_actions=(),
            needs_human=False,
            reason="",
            catalog_signals=(),
        )


class ReliableMessageTests(unittest.TestCase):
    def test_catalog_signal_is_recorded_once_with_dialogue_completion(self) -> None:
        catalog_root = Path(self.temp_dir.name) / "catalog"
        catalog_root.mkdir()
        (catalog_root / "bau-01.jpg").write_bytes(b"recommended-dress")
        manifest = catalog_root / "products.csv"
        with manifest.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.DictWriter(
                output,
                fieldnames=(
                    "catalog_type", "brand_internal", "style_name", "model_code",
                    "description", "rental_price_internal", "purchase_price_internal",
                    "rental_available", "purchase_or_custom_available", "image_path",
                    "source_document",
                ),
            )
            writer.writeheader()
            writer.writerow(
                {
                    "catalog_type": "bridal",
                    "brand_internal": "Bride and You",
                    "style_name": "Verona 无袖",
                    "model_code": "bau-01",
                    "description": "无袖缎面婚纱",
                    "rental_price_internal": "6600",
                    "purchase_price_internal": "15800",
                    "rental_available": "yes",
                    "purchase_or_custom_available": "yes",
                    "image_path": "bau-01.jpg",
                    "source_document": "test.csv",
                }
            )
        catalog = CatalogStore(self.database_path, catalog_root=catalog_root)
        catalog.import_verified_manifest(manifest)
        pipeline = self.pipeline(CatalogSignalEngine(), FakeDeliveryGateway())

        pipeline.receive(self.message("msg-catalog", "想看看简洁的缎面婚纱"))
        self.assertTrue(pipeline.process_once("worker-1", self.process_time))
        self.assertFalse(pipeline.process_once("worker-1", self.process_time))
        self.assertEqual(self.store.status_counts("outbox_messages"), {"pending": 2})

        gateway = FakeDeliveryGateway()
        delivery_pipeline = self.pipeline(CatalogSignalEngine(), gateway)
        self.assertTrue(delivery_pipeline.dispatch_once("sender-1", self.process_time))
        self.assertEqual(gateway.sent[0].message_type, "text")
        self.assertTrue(
            delivery_pipeline.dispatch_once(
                "sender-1", self.process_time + timedelta(seconds=1)
            )
        )
        self.assertEqual(gateway.sent[1].message_type, "image")
        self.assertEqual(gateway.sent[1].payload["catalog_ref"], "bau-01")
        self.assertNotIn("path", gateway.sent[1].payload)

        resend_pipeline = self.pipeline(TextOnlyDecisionEngine(), gateway)
        resend_pipeline.receive(self.message("msg-catalog-resend", "图片没看到，重新发一下"))
        self.assertTrue(
            resend_pipeline.process_once(
                "worker-resend", self.process_time + timedelta(seconds=2)
            )
        )
        self.assertTrue(
            resend_pipeline.dispatch_once(
                "sender-resend", self.process_time + timedelta(seconds=2)
            )
        )
        self.assertTrue(
            resend_pipeline.dispatch_once(
                "sender-resend", self.process_time + timedelta(seconds=3)
            )
        )
        self.assertEqual(gateway.sent[-1].message_type, "image")
        self.assertEqual(gateway.sent[-1].payload["catalog_ref"], "bau-01")

        row = next(item for item in catalog.analytics_summary().rows if item.model_code == "bau-01")
        self.assertEqual(row.demand_signals, 1)

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "messages.db"
        self.store = SqliteMessageStore(self.database_path)
        self.process_time = datetime.now(timezone.utc) + timedelta(minutes=1)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def message(self, message_id: str, text: str) -> InboundMessage:
        return InboundMessage(
            channel="wecom_kf",
            channel_account_id="open-kfid-1",
            external_user_id="customer-1",
            external_message_id=message_id,
            message_type="text",
            text_content=text,
            occurred_at=NOW,
        )

    def pipeline(self, engine=None, gateway=None) -> ReliableMessagePipeline:
        return ReliableMessagePipeline(
            self.store,
            engine or EchoReplyEngine(),
            gateway or FakeDeliveryGateway(),
            debounce_seconds=0,
            max_attempts=3,
        )

    def test_duplicate_callback_creates_one_job_and_one_reply(self) -> None:
        engine = EchoReplyEngine()
        gateway = FakeDeliveryGateway()
        pipeline = self.pipeline(engine, gateway)

        first = pipeline.receive(self.message("msg-1", "你好"))
        duplicate = pipeline.receive(self.message("msg-1", "你好"))

        self.assertFalse(first.duplicate)
        self.assertTrue(duplicate.duplicate)
        self.assertTrue(pipeline.process_once("worker-1", self.process_time))
        self.assertTrue(pipeline.dispatch_once("sender-1", self.process_time))
        self.assertEqual(len(engine.calls), 1)
        self.assertEqual(len(gateway.sent), 1)
        history = self.store.recent_messages(first.conversation_id)
        self.assertEqual([item.actor.value for item in history], ["customer", "ai"])
        self.assertEqual(history[-1].text_content, "收到：你好")

    def test_rapid_messages_are_processed_as_one_ordered_batch(self) -> None:
        engine = EchoReplyEngine()
        pipeline = self.pipeline(engine, FakeDeliveryGateway())

        pipeline.receive(self.message("msg-1", "我想试纱"))
        pipeline.receive(self.message("msg-2", "周六下午可以吗"))

        self.assertTrue(pipeline.process_once("worker-1", self.process_time))
        self.assertEqual(
            engine.calls[0][1],
            ("我想试纱", "周六下午可以吗"),
        )
        self.assertEqual(self.store.status_counts("inbox_jobs"), {"done": 2})
        self.assertEqual(self.store.status_counts("outbox_messages"), {"pending": 1})

    def test_human_owned_conversation_never_calls_ai(self) -> None:
        engine = EchoReplyEngine()
        pipeline = self.pipeline(engine, FakeDeliveryGateway())
        received = pipeline.receive(self.message("msg-1", "还在吗"))
        self.store.set_conversation_owner(
            received.conversation_id,
            ConversationOwner.HUMAN,
            "active",
        )

        self.assertTrue(pipeline.process_once("worker-1", self.process_time))
        self.assertEqual(engine.calls, [])
        self.assertEqual(self.store.status_counts("outbox_messages"), {})

    def test_model_failure_is_retried_without_losing_message(self) -> None:
        engine = EchoReplyEngine(fail_times=1)
        pipeline = self.pipeline(engine, FakeDeliveryGateway())
        pipeline.receive(self.message("msg-1", "想预约"))

        self.assertTrue(pipeline.process_once("worker-1", self.process_time))
        self.assertEqual(self.store.status_counts("inbox_jobs"), {"retry": 1})

        self.assertTrue(pipeline.process_once("worker-1", self.process_time + timedelta(seconds=1)))
        self.assertEqual(self.store.status_counts("inbox_jobs"), {"done": 1})

    def test_terminal_model_failure_creates_customer_fallback(self) -> None:
        engine = EchoReplyEngine(fail_times=3)
        gateway = FakeDeliveryGateway()
        pipeline = self.pipeline(engine, gateway)
        pipeline.receive(self.message("msg-1", "有人吗"))

        pipeline.process_once("worker-1", self.process_time)
        pipeline.process_once("worker-1", self.process_time + timedelta(seconds=1))
        pipeline.process_once("worker-1", self.process_time + timedelta(seconds=3))

        self.assertEqual(self.store.status_counts("inbox_jobs"), {"dead": 1})
        self.assertEqual(self.store.status_counts("outbox_messages"), {"pending": 2})
        self.assertEqual(self.store.action_count("done"), 1)
        self.assertEqual(self.store.task_count("take_over_conversation"), 1)
        self.assertTrue(
            pipeline.dispatch_once("sender-1", self.process_time + timedelta(seconds=4))
        )
        self.assertEqual(
            self.store.status_counts("outbox_messages"),
            {"pending": 1, "sent": 1},
        )
        self.assertEqual(len(gateway.sent), 1)

    def test_delivery_failure_retries_same_idempotency_key(self) -> None:
        gateway = FakeDeliveryGateway(fail_times=1)
        pipeline = self.pipeline(EchoReplyEngine(), gateway)
        pipeline.receive(self.message("msg-1", "你好"))
        pipeline.process_once("worker-1", self.process_time)

        self.assertTrue(pipeline.dispatch_once("sender-1", self.process_time))
        self.assertEqual(self.store.status_counts("outbox_messages"), {"retry": 1})

        self.assertTrue(pipeline.dispatch_once("sender-1", self.process_time + timedelta(seconds=1)))
        self.assertEqual(self.store.status_counts("outbox_messages"), {"sent": 1})
        self.assertEqual(len(gateway.sent), 1)

    def test_handoff_cancels_ai_reply_already_waiting_to_send(self) -> None:
        gateway = FakeDeliveryGateway()
        pipeline = self.pipeline(EchoReplyEngine(), gateway)
        received = pipeline.receive(self.message("msg-1", "我想找店员"))
        pipeline.process_once("worker-1", self.process_time)
        self.assertEqual(self.store.status_counts("outbox_messages"), {"pending": 1})

        self.store.set_conversation_owner(
            received.conversation_id,
            ConversationOwner.HUMAN_PENDING,
            "requested",
        )

        self.assertTrue(pipeline.dispatch_once("sender-1", self.process_time))
        self.assertEqual(self.store.status_counts("outbox_messages"), {"cancelled": 1})
        self.assertEqual(gateway.sent, [])


if __name__ == "__main__":
    unittest.main()
