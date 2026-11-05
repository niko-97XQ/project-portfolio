from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from forthatday_agent.application.action_executor import ActionExecutor  # noqa: E402
from forthatday_agent.application.message_pipeline import ReliableMessagePipeline  # noqa: E402
from forthatday_agent.application.payment_verification import (  # noqa: E402
    OcrDocument,
    OcrLine,
    PaymentVerificationService,
    extract_payment_evidence,
)
from forthatday_agent.application.staff_appointments import (  # noqa: E402
    PaymentCodes,
    StaffAppointmentService,
)
from forthatday_agent.domain.dialogue import (  # noqa: E402
    ActionType,
    DialogueDecision,
    ProposedAction,
)
from forthatday_agent.domain.memory import FactSource, MemoryFact  # noqa: E402
from forthatday_agent.domain.messages import InboundMessage  # noqa: E402
from forthatday_agent.domain.payments import (  # noqa: E402
    PaymentDecision,
    PaymentPolicy,
    evaluate_payment_evidence,
    normalize_payee,
)
from forthatday_agent.infrastructure.sqlite_message_store import (  # noqa: E402
    SqliteMessageStore,
)
from forthatday_agent.infrastructure.payment_policy_loader import (  # noqa: E402
    load_payment_policy,
)
from forthatday_agent.infrastructure.rapidocr_provider import RapidOcrProvider  # noqa: E402


NOW = datetime(2026, 7, 24, 9, 0, tzinfo=timezone.utc)
POLICY = PaymentPolicy(
    expected_amount=Decimal("200.00"),
    payee_aliases=(
        "*冉(个人)",
        "*冉（个人）",
        "Forthatday(*冉)",
        "Forthatday-Studio",
    ),
)


class StaticOcr:
    def __init__(self, document: OcrDocument) -> None:
        self.document = document
        self.calls = 0

    def extract(self, image_path: Path) -> OcrDocument:
        self.calls += 1
        return self.document


class RapidResult:
    txts = ("付款成功", "支付金额 ￥200.00")
    scores = (0.98, 0.97)


class StaticDecisionEngine:
    def __init__(self, decision: DialogueDecision) -> None:
        self.decision = decision

    def reply(self, conversation_id: str, messages: tuple) -> DialogueDecision:
        return self.decision


class NullGateway:
    def send(self, message) -> str:
        return "unused"


def valid_document(confidence: float = 0.98) -> OcrDocument:
    return OcrDocument(
        (
            OcrLine("付款成功", confidence),
            OcrLine("支付金额 ￥200.00", confidence),
            OcrLine("收款方全称 *冉（个人）", confidence),
            OcrLine("交易单号 2026072400012345", confidence),
        )
    )


class PaymentExtractionTests(unittest.TestCase):
    def test_rapidocr_adapter_preserves_text_and_confidence(self) -> None:
        class Engine:
            def __call__(self, image_path: str):
                return RapidResult()

        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "payment.png"
            image.write_bytes(b"image")

            document = RapidOcrProvider(Engine()).extract(image)

        self.assertEqual(document.lines[0], OcrLine("付款成功", 0.98))
        self.assertEqual(document.lines[1], OcrLine("支付金额 ￥200.00", 0.97))

    def test_private_payment_policy_loader(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "policy.json"
            path.write_text(
                '{"expected_amount":"200.00","payee_aliases":["*冉(个人)"],'
                '"minimum_confidence":0.9}',
                encoding="utf-8",
            )

            policy = load_payment_policy(path)

        self.assertEqual(policy.expected_amount, Decimal("200.00"))
        self.assertEqual(policy.payee_aliases, ("*冉(个人)",))
    def test_partial_payee_crop_cannot_be_accepted_without_amount_and_success(self) -> None:
        evidence = extract_payment_evidence(
            OcrDocument((OcrLine("收款方全称 *冉(个人)", 0.99),)),
            "message-1",
            POLICY,
        )

        result = evaluate_payment_evidence(evidence, POLICY)

        self.assertIsNone(evidence.amount)
        self.assertEqual(normalize_payee(evidence.payee), normalize_payee("*冉(个人)"))
        self.assertEqual(result.decision, PaymentDecision.REVIEW)
        self.assertIn("amount_mismatch", result.reasons)
        self.assertIn("success_state_missing", result.reasons)

    def test_fullwidth_parentheses_and_hyphen_aliases_are_normalized(self) -> None:
        evidence = extract_payment_evidence(valid_document(), "message-2", POLICY)

        result = evaluate_payment_evidence(evidence, POLICY)

        self.assertEqual(evidence.amount, Decimal("200.00"))
        self.assertEqual(result.decision, PaymentDecision.ACCEPT)

    def test_multiple_labeled_amounts_are_treated_as_ambiguous(self) -> None:
        document = OcrDocument(
            valid_document().lines
            + (OcrLine("订单金额 ￥300.00", 0.99),)
        )

        evidence = extract_payment_evidence(document, "message-3", POLICY)

        self.assertIsNone(evidence.amount)
        self.assertEqual(
            evaluate_payment_evidence(evidence, POLICY).decision,
            PaymentDecision.REVIEW,
        )

    def test_low_ocr_confidence_never_auto_accepts(self) -> None:
        evidence = extract_payment_evidence(valid_document(0.72), "message-4", POLICY)

        result = evaluate_payment_evidence(evidence, POLICY)

        self.assertEqual(result.decision, PaymentDecision.REVIEW)
        self.assertIn("confidence_too_low", result.reasons)

    def test_transfer_detail_amount_with_minus_and_thousands_separator_is_read(self) -> None:
        document = OcrDocument(
            (
                OcrLine("Forthatday-Studio", 0.99),
                OcrLine("-1,420.00", 0.98),
                OcrLine("交易成功", 0.99),
            )
        )

        evidence = extract_payment_evidence(document, "message-1", POLICY)
        result = evaluate_payment_evidence(evidence, POLICY)

        self.assertEqual(evidence.amount, Decimal("1420.00"))
        self.assertEqual(result.decision, PaymentDecision.REVIEW)
        self.assertIn("amount_mismatch", result.reasons)


class PaymentVerificationFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.store = SqliteMessageStore(root / "payments.db")
        self.screenshot = root / "payment.png"
        self.screenshot.write_bytes(b"synthetic-payment-screenshot")
        wechat = root / "wechat.jpg"
        alipay = root / "alipay.jpg"
        wechat.write_bytes(b"wechat")
        alipay.write_bytes(b"alipay")
        self.payment_codes = PaymentCodes(wechat, alipay)
        self.process_time = datetime.now(timezone.utc) + timedelta(minutes=1)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def prepare_awaiting_payment(self, suffix: str = "") -> tuple[str, str]:
        received = self.store.ingest(
            InboundMessage(
                channel="wecom_kf",
                channel_account_id="open-kfid-1",
                external_user_id=f"customer-1{suffix}",
                external_message_id=f"appointment-message{suffix}",
                message_type="text",
                text_content="我叫小许，电话13800138000，想约7月30日下午3点",
                occurred_at=NOW,
            ),
            debounce_seconds=0,
        )
        decision = DialogueDecision(
            reply="我先请店员确认这个时间。",
            memory_updates=(
                MemoryFact(
                    "customer_name",
                    "小许",
                    FactSource.CUSTOMER_EXPLICIT,
                    received.message_id,
                ),
                MemoryFact(
                    "customer_phone",
                    "13800138000",
                    FactSource.CUSTOMER_EXPLICIT,
                    received.message_id,
                ),
            ),
            proposed_actions=(
                ProposedAction(
                    ActionType.REQUEST_TIME_CONFIRMATION,
                    {"requested_date": "2026-07-30", "requested_time": "15:00"},
                ),
            ),
            needs_human=False,
            reason="",
        )
        pipeline = ReliableMessagePipeline(
            self.store,
            StaticDecisionEngine(decision),
            NullGateway(),
            debounce_seconds=0,
        )
        pipeline.process_once("dialogue-worker", self.process_time)
        ActionExecutor(self.store).process_once("action-worker", self.process_time)
        task_id = self.store.first_open_task_id("confirm_appointment_time")
        StaffAppointmentService(self.store, self.payment_codes).approve_time(
            task_id,
            "staff-1",
        )
        payment_message = self.store.ingest(
            InboundMessage(
                channel="wecom_kf",
                channel_account_id="open-kfid-1",
                external_user_id=f"customer-1{suffix}",
                external_message_id=f"payment-message{suffix}",
                message_type="image",
                raw_payload={"path": str(self.screenshot)},
                occurred_at=NOW + timedelta(minutes=5),
            ),
            debounce_seconds=0,
        )
        return received.conversation_id, payment_message.message_id

    def test_valid_screenshot_locks_appointment_and_is_idempotent(self) -> None:
        conversation_id, message_id = self.prepare_awaiting_payment()
        ocr = StaticOcr(valid_document())
        service = PaymentVerificationService(self.store, ocr, POLICY)

        first = service.verify(message_id, self.screenshot)
        second = service.verify(message_id, self.screenshot)

        self.assertEqual(first, PaymentDecision.ACCEPT)
        self.assertEqual(second, PaymentDecision.ACCEPT)
        self.assertEqual(ocr.calls, 1)
        self.assertEqual(
            self.store.load_appointment_state(conversation_id)["status"],
            "confirmed",
        )
        self.assertEqual(self.store.audit_count("payment_evidence_accepted"), 1)
        self.assertEqual(self.store.status_counts("inbox_jobs"), {"done": 2})

    def test_incomplete_screenshot_creates_review_task(self) -> None:
        conversation_id, message_id = self.prepare_awaiting_payment()
        document = OcrDocument((OcrLine("收款方全称 Forthatday-Studio", 0.99),))
        service = PaymentVerificationService(self.store, StaticOcr(document), POLICY)

        result = service.verify(message_id, self.screenshot)

        self.assertEqual(result, PaymentDecision.REVIEW)
        self.assertEqual(
            self.store.load_appointment_state(conversation_id)["status"],
            "payment_review",
        )
        self.assertEqual(self.store.task_count("review_payment"), 1)
        self.assertEqual(
            self.store.audit_count("payment_evidence_review_required"),
            1,
        )
        task_id = self.store.first_open_task_id("review_payment")
        task = StaffAppointmentService(self.store, self.payment_codes).get_payment_task(task_id)
        self.assertEqual(task.appointment_status, "payment_review")
        self.assertEqual(task.evidence_path, self.screenshot)

    def test_staff_can_reject_review_and_request_new_screenshot(self) -> None:
        conversation_id, message_id = self.prepare_awaiting_payment()
        document = OcrDocument((OcrLine("收款方全称 Forthatday-Studio", 0.99),))
        PaymentVerificationService(self.store, StaticOcr(document), POLICY).verify(
            message_id,
            self.screenshot,
        )
        task_id = self.store.first_open_task_id("review_payment")

        StaffAppointmentService(self.store, self.payment_codes).reject_payment(
            task_id,
            "staff-1",
        )

        self.assertEqual(
            self.store.load_appointment_state(conversation_id)["status"],
            "awaiting_payment",
        )
        self.assertEqual(self.store.task_count("review_payment", "resolved"), 1)
        self.assertEqual(self.store.audit_count("payment_review_rejected"), 1)

    def test_staff_can_approve_review_and_lock_appointment(self) -> None:
        conversation_id, message_id = self.prepare_awaiting_payment()
        document = OcrDocument((OcrLine("付款成功", 0.99),))
        PaymentVerificationService(self.store, StaticOcr(document), POLICY).verify(
            message_id,
            self.screenshot,
        )
        task_id = self.store.first_open_task_id("review_payment")

        StaffAppointmentService(self.store, self.payment_codes).approve_payment(
            task_id,
            "staff-1",
        )

        state = self.store.load_appointment_state(conversation_id)
        self.assertEqual(state["status"], "confirmed")
        self.assertTrue(state["has_payment_evidence"])
        self.assertEqual(self.store.audit_count("payment_review_approved"), 1)

    def test_reused_screenshot_for_another_customer_requires_review(self) -> None:
        first_conversation, first_message = self.prepare_awaiting_payment("-a")
        service = PaymentVerificationService(self.store, StaticOcr(valid_document()), POLICY)
        self.assertEqual(
            service.verify(first_message, self.screenshot),
            PaymentDecision.ACCEPT,
        )

        second_conversation, second_message = self.prepare_awaiting_payment("-b")
        result = service.verify(second_message, self.screenshot)

        self.assertEqual(result, PaymentDecision.REVIEW)
        self.assertEqual(
            self.store.load_appointment_state(first_conversation)["status"],
            "confirmed",
        )
        self.assertEqual(
            self.store.load_appointment_state(second_conversation)["status"],
            "payment_review",
        )


if __name__ == "__main__":
    unittest.main()
