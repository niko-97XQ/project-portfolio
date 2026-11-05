from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DomainRuleError(ValueError):
    """Raised when a business transition would create an invalid appointment."""


class AppointmentStatus(StrEnum):
    COLLECTING_DETAILS = "collecting_details"
    TIME_PENDING_STAFF = "time_pending_staff"
    AWAITING_PAYMENT = "awaiting_payment"
    PAYMENT_REVIEW = "payment_review"
    CONFIRMED = "confirmed"
    RESCHEDULE_COLLECTING = "reschedule_collecting"
    RESCHEDULE_PENDING_STAFF = "reschedule_pending_staff"
    CANCEL_PENDING_STAFF = "cancel_pending_staff"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


@dataclass(slots=True)
class Appointment:
    conversation_id: str
    id: str = field(default_factory=lambda: str(uuid4()))
    status: AppointmentStatus = AppointmentStatus.COLLECTING_DETAILS
    customer_name: str = ""
    customer_phone: str = ""
    requested_time: str = ""
    confirmed_time: str = ""
    proposed_reschedule_time: str = ""
    free_reschedules_used: int = 0
    payment_evidence_id: str = ""
    late_minutes: int | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def submit_time_request(self, requested_time: str) -> None:
        self._require(self.customer_name, "customer name is required")
        self._require(self.customer_phone, "customer phone is required")
        self._require(requested_time.strip(), "requested time is required")
        self.requested_time = requested_time.strip()
        self.status = AppointmentStatus.TIME_PENDING_STAFF
        self._touch()

    def staff_approve_time(self, confirmed_time: str | None = None) -> None:
        self._expect(AppointmentStatus.TIME_PENDING_STAFF)
        self.confirmed_time = (confirmed_time or self.requested_time).strip()
        self._require(self.confirmed_time, "confirmed time is required")
        self.status = AppointmentStatus.AWAITING_PAYMENT
        self._touch()

    def staff_reject_time(self) -> None:
        self._expect(AppointmentStatus.TIME_PENDING_STAFF)
        self.status = AppointmentStatus.COLLECTING_DETAILS
        self._touch()

    def accept_payment(self, evidence_id: str) -> None:
        self._expect(AppointmentStatus.AWAITING_PAYMENT, AppointmentStatus.PAYMENT_REVIEW)
        self._require(evidence_id.strip(), "payment evidence id is required")
        self.payment_evidence_id = evidence_id.strip()
        self.status = AppointmentStatus.CONFIRMED
        self._touch()

    def require_payment_review(self) -> None:
        self._expect(AppointmentStatus.AWAITING_PAYMENT)
        self.status = AppointmentStatus.PAYMENT_REVIEW
        self._touch()

    def start_reschedule(self) -> None:
        self._expect(AppointmentStatus.CONFIRMED)
        self.proposed_reschedule_time = ""
        self.status = AppointmentStatus.RESCHEDULE_COLLECTING
        self._touch()

    def submit_reschedule_time(self, requested_time: str) -> None:
        self._expect(AppointmentStatus.RESCHEDULE_COLLECTING)
        self._require(requested_time.strip(), "new requested time is required")
        self.proposed_reschedule_time = requested_time.strip()
        self.status = AppointmentStatus.RESCHEDULE_PENDING_STAFF
        self._touch()

    def staff_approve_reschedule(self, confirmed_time: str | None = None) -> None:
        self._expect(AppointmentStatus.RESCHEDULE_PENDING_STAFF)
        next_time = (confirmed_time or self.proposed_reschedule_time).strip()
        self._require(next_time, "new confirmed time is required")
        self.confirmed_time = next_time
        self.requested_time = next_time
        self.proposed_reschedule_time = ""
        self.free_reschedules_used += 1
        self.status = AppointmentStatus.CONFIRMED
        self._touch()

    def staff_reject_reschedule(self) -> None:
        self._expect(AppointmentStatus.RESCHEDULE_PENDING_STAFF)
        self.proposed_reschedule_time = ""
        self.status = AppointmentStatus.RESCHEDULE_COLLECTING
        self._touch()

    def request_cancel(self) -> None:
        self._expect(
            AppointmentStatus.AWAITING_PAYMENT,
            AppointmentStatus.PAYMENT_REVIEW,
            AppointmentStatus.CONFIRMED,
        )
        self.status = AppointmentStatus.CANCEL_PENDING_STAFF
        self._touch()

    def staff_approve_cancel(self) -> None:
        self._expect(AppointmentStatus.CANCEL_PENDING_STAFF)
        self.status = AppointmentStatus.CANCELLED
        self._touch()

    def report_late(self, minutes: int) -> bool:
        self._expect(AppointmentStatus.CONFIRMED)
        if minutes < 0:
            raise DomainRuleError("late minutes cannot be negative")
        self.late_minutes = minutes
        self._touch()
        return minutes > 15

    def complete(self) -> None:
        self._expect(AppointmentStatus.CONFIRMED)
        self.status = AppointmentStatus.COMPLETED
        self._touch()

    def _expect(self, *allowed: AppointmentStatus) -> None:
        if self.status not in allowed:
            names = ", ".join(item.value for item in allowed)
            raise DomainRuleError(
                f"appointment status {self.status.value} must be one of: {names}"
            )

    @staticmethod
    def _require(value: object, message: str) -> None:
        if not value:
            raise DomainRuleError(message)

    def _touch(self) -> None:
        self.updated_at = utc_now()
