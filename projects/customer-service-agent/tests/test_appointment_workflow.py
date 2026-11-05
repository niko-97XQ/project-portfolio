from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from service_agent.domain.appointments import (  # noqa: E402
    Appointment,
    AppointmentStatus,
    DomainRuleError,
)
from service_agent.domain.payments import (  # noqa: E402
    PaymentDecision,
    PaymentEvidence,
    PaymentPolicy,
    evaluate_payment_evidence,
)
from service_agent.domain.conversations import (  # noqa: E402
    Conversation,
    ConversationOwner,
    HandoffStatus,
)


class AppointmentWorkflowTests(unittest.TestCase):
    def test_staff_approval_then_payment_locks_appointment(self) -> None:
        appointment = Appointment(
            conversation_id="customer-1",
            customer_name="示例顾客",
            customer_phone="00000000000",
        )

        appointment.submit_time_request("7月30日下午3点")
        appointment.staff_approve_time()
        appointment.accept_payment("payment-evidence-1")

        self.assertEqual(appointment.status, AppointmentStatus.CONFIRMED)
        self.assertEqual(appointment.confirmed_time, "7月30日下午3点")

    def test_payment_cannot_lock_before_staff_approves_time(self) -> None:
        appointment = Appointment(conversation_id="customer-2")

        with self.assertRaises(DomainRuleError):
            appointment.accept_payment("payment-evidence-2")

    def test_reschedule_preserves_original_until_staff_approval(self) -> None:
        appointment = Appointment(
            conversation_id="customer-3",
            status=AppointmentStatus.CONFIRMED,
            confirmed_time="7月30日下午3点",
        )

        appointment.start_reschedule()
        appointment.submit_reschedule_time("8月2日下午2点")

        self.assertEqual(appointment.confirmed_time, "7月30日下午3点")
        self.assertEqual(
            appointment.status,
            AppointmentStatus.RESCHEDULE_PENDING_STAFF,
        )

        appointment.staff_approve_reschedule()
        self.assertEqual(appointment.confirmed_time, "8月2日下午2点")
        self.assertEqual(appointment.free_reschedules_used, 1)

    def test_cancel_requires_staff_approval(self) -> None:
        appointment = Appointment(
            conversation_id="customer-4",
            status=AppointmentStatus.CONFIRMED,
            confirmed_time="8月1日下午1点",
        )

        appointment.request_cancel()
        self.assertEqual(appointment.status, AppointmentStatus.CANCEL_PENDING_STAFF)

        appointment.staff_approve_cancel()
        self.assertEqual(appointment.status, AppointmentStatus.CANCELLED)

    def test_late_arrival_over_fifteen_minutes_requires_confirmation(self) -> None:
        appointment = Appointment(
            conversation_id="customer-5",
            status=AppointmentStatus.CONFIRMED,
        )

        self.assertFalse(appointment.report_late(10))
        self.assertTrue(appointment.report_late(20))


class PaymentEvidenceTests(unittest.TestCase):
    def test_matching_evidence_is_accepted(self) -> None:
        evidence = PaymentEvidence(
            amount=Decimal("200.00"),
            payee="Demo Studio",
            shows_success=True,
            confidence=0.97,
            source_message_id="wecom-msg-1",
        )
        policy = PaymentPolicy(payee_aliases=("Demo Studio", "DEMO"))

        result = evaluate_payment_evidence(evidence, policy)

        self.assertEqual(result.decision, PaymentDecision.ACCEPT)
        self.assertEqual(result.reasons, ())

    def test_unknown_payee_requires_review(self) -> None:
        evidence = PaymentEvidence(
            amount=Decimal("200.00"),
            payee="其他收款方",
            shows_success=True,
            confidence=0.98,
            source_message_id="wecom-msg-2",
        )
        policy = PaymentPolicy(payee_aliases=("Demo Studio",))

        result = evaluate_payment_evidence(evidence, policy)

        self.assertEqual(result.decision, PaymentDecision.REVIEW)
        self.assertIn("payee_mismatch", result.reasons)

    def test_missing_payee_configuration_never_auto_locks(self) -> None:
        evidence = PaymentEvidence(
            amount=Decimal("200.00"),
            payee="Demo Studio",
            shows_success=True,
            confidence=0.99,
            source_message_id="wecom-msg-3",
        )

        result = evaluate_payment_evidence(evidence, PaymentPolicy())

        self.assertEqual(result.decision, PaymentDecision.REVIEW)
        self.assertIn("payee_policy_not_configured", result.reasons)


class ConversationOwnershipTests(unittest.TestCase):
    def test_requesting_handoff_immediately_blocks_ai(self) -> None:
        conversation = Conversation(
            channel="wecom_kf",
            external_user_id="external-1",
            channel_account_id="open-kfid-1",
        )

        conversation.request_handoff("customer complaint")

        self.assertFalse(conversation.can_ai_reply)
        self.assertEqual(conversation.owner, ConversationOwner.HUMAN_PENDING)
        self.assertEqual(conversation.handoff_status, HandoffStatus.REQUESTED)

    def test_return_to_ai_requires_summary_and_external_transition(self) -> None:
        conversation = Conversation(
            channel="wecom_kf",
            external_user_id="external-2",
            channel_account_id="open-kfid-1",
        )
        conversation.request_handoff("customer requested staff")
        conversation.activate_human("staff-1")

        with self.assertRaises(DomainRuleError):
            conversation.request_return_to_ai("")

        conversation.request_return_to_ai("Staff confirmed the customer's new time.")
        self.assertFalse(conversation.can_ai_reply)

        conversation.activate_ai()
        self.assertTrue(conversation.can_ai_reply)
        self.assertEqual(conversation.owner, ConversationOwner.AI)


if __name__ == "__main__":
    unittest.main()
