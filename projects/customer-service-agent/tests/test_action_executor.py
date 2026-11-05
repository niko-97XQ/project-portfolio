from __future__ import annotations

import sys
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from forthatday_agent.application.action_executor import ActionExecutor  # noqa: E402
from forthatday_agent.application.message_pipeline import ReliableMessagePipeline  # noqa: E402
from forthatday_agent.application.staff_appointments import (  # noqa: E402
    PaymentCodes,
    StaffAppointmentService,
)
from forthatday_agent.domain.conversations import ConversationOwner  # noqa: E402
from forthatday_agent.domain.dialogue import (  # noqa: E402
    ActionType,
    DialogueDecision,
    ProposedAction,
)
from forthatday_agent.domain.memory import FactSource, MemoryFact  # noqa: E402
from forthatday_agent.domain.messages import InboundMessage  # noqa: E402
from forthatday_agent.infrastructure.sqlite_message_store import (  # noqa: E402
    SqliteMessageStore,
)


NOW = datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc)


class DecisionEngine:
    def __init__(self, decision: DialogueDecision) -> None:
        self.decision = decision
        self.calls = 0

    def reply(self, conversation_id: str, messages: tuple) -> DialogueDecision:
        self.calls += 1
        return self.decision


class NullGateway:
    def send(self, message) -> str:
        return "unused"


class RecordingGateway:
    def __init__(self) -> None:
        self.sent = []

    def send(self, message) -> str:
        self.sent.append(message)
        return "wecom-message-1"


class ActionExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SqliteMessageStore(Path(self.temp_dir.name) / "actions.db")
        self.process_time = datetime.now(timezone.utc) + timedelta(minutes=1)
        private_dir = Path(self.temp_dir.name) / "payment-codes"
        private_dir.mkdir()
        wechat = private_dir / "wechat.jpg"
        alipay = private_dir / "alipay.jpg"
        wechat.write_bytes(b"wechat-code")
        alipay.write_bytes(b"alipay-code")
        self.payment_codes = PaymentCodes(wechat=wechat, alipay=alipay)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def receive(self, text: str = "我想约7月30日下午3点"):
        return self.store.ingest(
            InboundMessage(
                channel="wecom_kf",
                channel_account_id="open-kfid-1",
                external_user_id="customer-1",
                external_message_id=f"msg-{text}",
                message_type="text",
                text_content=text,
                occurred_at=NOW,
            ),
            debounce_seconds=0,
        )

    def process_decision(self, decision: DialogueDecision) -> DecisionEngine:
        engine = DecisionEngine(decision)
        pipeline = ReliableMessagePipeline(self.store, engine, NullGateway(), debounce_seconds=0)
        self.assertTrue(pipeline.process_once("dialogue-worker", self.process_time))
        return engine

    def appointment_decision(self, message_id: str, *, include_phone: bool = True):
        memory = [
            MemoryFact(
                key="customer_name",
                value="小许",
                source=FactSource.CUSTOMER_EXPLICIT,
                evidence_message_id=message_id,
            )
        ]
        if include_phone:
            memory.append(
                MemoryFact(
                    key="customer_phone",
                    value="13800138000",
                    source=FactSource.CUSTOMER_EXPLICIT,
                    evidence_message_id=message_id,
                )
            )
        return DialogueDecision(
            reply="我先帮你把这个时间交给店员确认。",
            memory_updates=tuple(memory),
            proposed_actions=(
                ProposedAction(
                    ActionType.REQUEST_TIME_CONFIRMATION,
                    {"requested_date": "2026-07-30", "requested_time": "15:00"},
                ),
            ),
            needs_human=False,
            reason="",
        )

    def test_time_request_creates_appointment_and_staff_task(self) -> None:
        received = self.receive()
        self.process_decision(self.appointment_decision(received.message_id))

        executor = ActionExecutor(self.store)
        self.assertTrue(executor.process_once("action-worker", self.process_time))

        state = self.store.load_appointment_state(received.conversation_id)
        self.assertEqual(state["status"], "time_pending_staff")
        self.assertEqual(state["requested_time"], "2026-07-30 15:00")
        self.assertEqual(self.store.task_count("confirm_appointment_time"), 1)
        self.assertEqual(self.store.action_count("done"), 1)
        self.assertEqual(self.store.status_counts("outbox_messages"), {"pending": 2})

    def test_missing_phone_rejects_action_without_creating_appointment(self) -> None:
        received = self.receive()
        self.process_decision(
            self.appointment_decision(received.message_id, include_phone=False)
        )

        executor = ActionExecutor(self.store)
        self.assertTrue(executor.process_once("action-worker", self.process_time))

        self.assertEqual(self.store.load_appointment_state(received.conversation_id), {})
        self.assertEqual(self.store.task_count("confirm_appointment_time"), 0)
        self.assertEqual(self.store.action_count("rejected"), 1)

    def test_handoff_blocks_ai_and_creates_urgent_task_atomically(self) -> None:
        received = self.receive("我要投诉")
        decision = DialogueDecision(
            reply="抱歉让你久等了，我马上转给店员。",
            memory_updates=(),
            proposed_actions=(
                ProposedAction(
                    ActionType.HANDOFF_TO_HUMAN,
                    {"reason": "客户投诉"},
                ),
            ),
            needs_human=True,
            reason="客户投诉",
        )
        engine = DecisionEngine(decision)
        gateway = RecordingGateway()
        pipeline = ReliableMessagePipeline(
            self.store,
            engine,
            gateway,
            debounce_seconds=0,
        )
        self.assertTrue(pipeline.process_once("dialogue-worker", self.process_time))

        self.assertIs(
            self.store.conversation_owner(received.conversation_id),
            ConversationOwner.HUMAN_PENDING,
        )
        self.assertEqual(self.store.task_count("take_over_conversation"), 1)
        self.assertEqual(self.store.action_count("done"), 1)
        self.assertEqual(self.store.action_count("proposed"), 0)
        self.assertTrue(pipeline.dispatch_once("sender", self.process_time))
        self.assertEqual(len(gateway.sent), 1)
        self.assertEqual(gateway.sent[0].actor.value, "system")

    def test_staff_can_take_over_and_return_conversation_with_summary(self) -> None:
        received = self.receive("我要找真人")
        decision = DialogueDecision(
            reply="收到，我马上请店员来和你对接。",
            memory_updates=(),
            proposed_actions=(
                ProposedAction(
                    ActionType.HANDOFF_TO_HUMAN,
                    {"reason": "顾客要求真人回复"},
                ),
            ),
            needs_human=True,
            reason="顾客要求真人回复",
        )
        self.process_decision(decision)
        task_id = self.store.first_open_task_id("take_over_conversation")
        service = StaffAppointmentService(self.store, self.payment_codes)

        task = service.get_handoff_task(task_id)
        service.activate_handoff(task_id, "staff-1")

        self.assertEqual(task.owner, "human_pending")
        self.assertIs(
            self.store.conversation_owner(received.conversation_id),
            ConversationOwner.HUMAN,
        )
        service.return_handoff(task_id, "staff-1", "已解释付款截图要求")

        self.assertIs(
            self.store.conversation_owner(received.conversation_id),
            ConversationOwner.AI,
        )
        self.assertEqual(self.store.task_count("take_over_conversation", "resolved"), 1)
        self.assertIn("人工交接：已解释付款截图要求", self.store.load_memory(received.conversation_id).summary)
        self.assertEqual(self.store.audit_count("handoff_activated"), 1)
        self.assertEqual(self.store.audit_count("handoff_returned_to_ai"), 1)

    def test_staff_approval_queues_payment_message_and_both_codes_once(self) -> None:
        received = self.receive()
        self.process_decision(self.appointment_decision(received.message_id))
        ActionExecutor(self.store).process_once("action-worker", self.process_time)
        task_id = self.store.first_open_task_id("confirm_appointment_time")
        service = StaffAppointmentService(self.store, self.payment_codes)

        service.approve_time(task_id, "staff-1")
        service.approve_time(task_id, "staff-1")

        state = self.store.load_appointment_state(received.conversation_id)
        self.assertEqual(state["status"], "awaiting_payment")
        self.assertTrue(state["staff_time_approved"])
        self.assertEqual(self.store.task_count("confirm_appointment_time", "resolved"), 1)
        self.assertEqual(
            self.store.status_counts("outbox_messages"),
            {"cancelled": 1, "pending": 4},
        )
        self.assertEqual(self.store.audit_count("appointment_time_approved"), 1)

        gateway = RecordingGateway()
        dispatcher = ReliableMessagePipeline(
            self.store,
            DecisionEngine(self.appointment_decision(received.message_id)),
            gateway,
        )
        for index in range(4):
            self.assertTrue(
                dispatcher.dispatch_once(
                    f"sender-{index}",
                    self.process_time + timedelta(minutes=1),
                )
            )
        self.assertEqual(
            [(item.message_type, item.payload.get("caption", "")) for item in gateway.sent],
            [
                ("text", ""),
                ("text", ""),
                ("image", "微信收款码"),
                ("image", "支付宝收款码"),
            ],
        )
        self.assertIn("7月30日下午3点", gateway.sent[1].payload["text"])
        self.assertNotIn("2026-07-30 15:00", gateway.sent[1].payload["text"])

    def test_staff_rejection_returns_to_collecting_details_once(self) -> None:
        received = self.receive()
        self.process_decision(self.appointment_decision(received.message_id))
        ActionExecutor(self.store).process_once("action-worker", self.process_time)
        task_id = self.store.first_open_task_id("confirm_appointment_time")
        service = StaffAppointmentService(self.store, self.payment_codes)

        service.reject_time(task_id, "staff-2")
        service.reject_time(task_id, "staff-2")

        state = self.store.load_appointment_state(received.conversation_id)
        self.assertEqual(state["status"], "collecting_details")
        self.assertEqual(
            self.store.status_counts("outbox_messages"),
            {"cancelled": 1, "pending": 2},
        )
        self.assertEqual(self.store.audit_count("appointment_time_rejected"), 1)


class LocalSchemaUpgradeTests(unittest.TestCase):
    def test_existing_action_table_is_upgraded_without_losing_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "legacy.db"
            connection = sqlite3.connect(database_path)
            connection.executescript(
                """
                CREATE TABLE conversations (
                    id TEXT PRIMARY KEY,
                    channel TEXT NOT NULL,
                    channel_account_id TEXT NOT NULL,
                    external_user_id TEXT NOT NULL,
                    owner TEXT NOT NULL DEFAULT 'ai',
                    handoff_status TEXT NOT NULL DEFAULT 'none',
                    last_message_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    external_message_id TEXT,
                    actor TEXT NOT NULL,
                    message_type TEXT NOT NULL,
                    text_content TEXT NOT NULL DEFAULT '',
                    raw_payload TEXT NOT NULL DEFAULT '{}',
                    occurred_at TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE action_jobs (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    source_message_id TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'proposed',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                INSERT INTO conversations VALUES (
                    'c1', 'wecom_kf', 'kf1', 'u1', 'ai', 'none', NULL,
                    '2026-07-24T00:00:00+00:00', '2026-07-24T00:00:00+00:00'
                );
                INSERT INTO messages VALUES (
                    'm1', 'c1', 'ext1', 'customer', 'text', '你好', '{}',
                    '2026-07-24T00:00:00+00:00', '2026-07-24T00:00:00+00:00',
                    '2026-07-24T00:00:00+00:00'
                );
                INSERT INTO action_jobs VALUES (
                    'a1', 'c1', 'm1', 'request_time_confirmation',
                    '{"requested_time":"15:00"}', 'proposed',
                    '2026-07-24T00:00:00+00:00', '2026-07-24T00:00:00+00:00'
                );
                """
            )
            connection.commit()
            connection.close()

            store = SqliteMessageStore(database_path)

            self.assertEqual(store.action_count("proposed"), 1)
            upgraded = sqlite3.connect(database_path)
            try:
                columns = {
                    row[1]
                    for row in upgraded.execute("PRAGMA table_info(action_jobs)")
                }
            finally:
                upgraded.close()
            self.assertIn("available_at", columns)
            self.assertIn("attempt_count", columns)


if __name__ == "__main__":
    unittest.main()
