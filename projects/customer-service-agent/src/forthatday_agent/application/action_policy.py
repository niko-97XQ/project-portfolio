from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from forthatday_agent.domain.dialogue import ActionType, ProposedAction


@dataclass(frozen=True, slots=True)
class ActionContext:
    appointment_status: str = ""
    has_confirmed_time: bool = False
    has_payment_evidence: bool = False


@dataclass(frozen=True, slots=True)
class ActionValidation:
    accepted: bool
    reason: str = ""


class ActionPolicy:
    def validate(self, action: ProposedAction, context: ActionContext) -> ActionValidation:
        validator = getattr(self, f"_validate_{action.type.value}")
        return validator(action.arguments, context)

    def _validate_request_time_confirmation(
        self, arguments: dict[str, Any], context: ActionContext
    ) -> ActionValidation:
        if context.appointment_status not in {"", "collecting_details"}:
            return _reject("appointment is not collecting a new time")
        return _require_text(arguments, "requested_time")

    def _validate_send_payment_codes(
        self, arguments: dict[str, Any], context: ActionContext
    ) -> ActionValidation:
        if context.appointment_status != "awaiting_payment" or not context.has_confirmed_time:
            return _reject("staff must approve the time before payment codes are sent")
        return _accept()

    def _validate_verify_payment_evidence(
        self, arguments: dict[str, Any], context: ActionContext
    ) -> ActionValidation:
        if context.appointment_status != "awaiting_payment":
            return _reject("appointment is not awaiting payment")
        if not context.has_payment_evidence:
            return _reject("payment evidence is missing")
        return _require_text(arguments, "source_message_id")

    def _validate_request_reschedule_confirmation(
        self, arguments: dict[str, Any], context: ActionContext
    ) -> ActionValidation:
        if context.appointment_status not in {"confirmed", "reschedule_collecting"}:
            return _reject("appointment cannot be rescheduled from its current state")
        return _require_text(arguments, "requested_time")

    def _validate_request_cancellation_confirmation(
        self, arguments: dict[str, Any], context: ActionContext
    ) -> ActionValidation:
        if context.appointment_status not in {"awaiting_payment", "payment_review", "confirmed"}:
            return _reject("appointment cannot be cancelled from its current state")
        return _accept()

    def _validate_notify_late_arrival(
        self, arguments: dict[str, Any], context: ActionContext
    ) -> ActionValidation:
        if context.appointment_status != "confirmed":
            return _reject("late arrival requires a confirmed appointment")
        minutes = arguments.get("minutes")
        if not isinstance(minutes, int) or minutes < 0:
            return _reject("late minutes must be a non-negative integer")
        return _accept()

    def _validate_handoff_to_human(
        self, arguments: dict[str, Any], context: ActionContext
    ) -> ActionValidation:
        return _require_text(arguments, "reason")


def _require_text(arguments: dict[str, Any], key: str) -> ActionValidation:
    if not str(arguments.get(key) or "").strip():
        return _reject(f"{key} is required")
    return _accept()


def _accept() -> ActionValidation:
    return ActionValidation(True)


def _reject(reason: str) -> ActionValidation:
    return ActionValidation(False, reason)

