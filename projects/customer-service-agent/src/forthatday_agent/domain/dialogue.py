from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .memory import FactSource, MemoryFact, PROFILE_FIELDS


class ActionType(StrEnum):
    REQUEST_TIME_CONFIRMATION = "request_time_confirmation"
    SEND_PAYMENT_CODES = "send_payment_codes"
    VERIFY_PAYMENT_EVIDENCE = "verify_payment_evidence"
    REQUEST_RESCHEDULE_CONFIRMATION = "request_reschedule_confirmation"
    REQUEST_CANCELLATION_CONFIRMATION = "request_cancellation_confirmation"
    NOTIFY_LATE_ARRIVAL = "notify_late_arrival"
    HANDOFF_TO_HUMAN = "handoff_to_human"


@dataclass(frozen=True, slots=True)
class ProposedAction:
    type: ActionType
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CatalogSignal:
    event_type: str
    catalog_ref: str


@dataclass(frozen=True, slots=True)
class DialogueDecision:
    reply: str
    memory_updates: tuple[MemoryFact, ...]
    proposed_actions: tuple[ProposedAction, ...]
    needs_human: bool
    reason: str
    catalog_signals: tuple[CatalogSignal, ...] = ()

    @classmethod
    def from_model_output(cls, data: dict[str, Any]) -> DialogueDecision:
        reply = str(data.get("reply") or "").strip()
        if not reply:
            raise ValueError("model reply cannot be empty")
        if len(reply) > 2000:
            raise ValueError("model reply is too long")

        memory_updates = tuple(_parse_memory_update(item) for item in _list(data, "memory_updates"))
        actions = tuple(_parse_action(item) for item in _list(data, "proposed_actions"))
        catalog_signals = tuple(
            _parse_catalog_signal(item) for item in _list(data, "catalog_signals")
        )
        needs_human = bool(data.get("needs_human", False))
        reason = str(data.get("reason") or "").strip()
        if needs_human and not reason:
            raise ValueError("human escalation requires a reason")
        if needs_human and not any(
            action.type is ActionType.HANDOFF_TO_HUMAN for action in actions
        ):
            raise ValueError("human escalation requires a handoff action")

        return cls(
            reply=reply,
            memory_updates=memory_updates,
            proposed_actions=actions,
            needs_human=needs_human,
            reason=reason,
            catalog_signals=catalog_signals,
        )


def _parse_memory_update(data: Any) -> MemoryFact:
    if not isinstance(data, dict):
        raise ValueError("memory update must be an object")
    key = str(data.get("key") or "")
    if key not in PROFILE_FIELDS:
        raise ValueError(f"unsupported memory field: {key}")
    source = FactSource(str(data.get("source") or ""))
    if source is FactSource.STAFF_CONFIRMED:
        raise ValueError("the model cannot create staff-confirmed facts")
    confidence = float(data.get("confidence", 1.0))
    evidence_message_id = str(data.get("evidence_message_id") or "")
    if source is FactSource.CUSTOMER_EXPLICIT and not evidence_message_id:
        raise ValueError("customer facts require an evidence message id")
    return MemoryFact(
        key=key,
        value=data.get("value"),
        source=source,
        evidence_message_id=evidence_message_id,
        confidence=confidence,
    )


def _parse_action(data: Any) -> ProposedAction:
    if not isinstance(data, dict):
        raise ValueError("proposed action must be an object")
    action_type = ActionType(str(data.get("type") or ""))
    arguments = data.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise ValueError("action arguments must be an object")
    return ProposedAction(type=action_type, arguments=arguments)


def _parse_catalog_signal(data: Any) -> CatalogSignal:
    if not isinstance(data, dict):
        raise ValueError("catalog signal must be an object")
    event_type = str(data.get("event_type") or "").strip()
    if event_type not in {"inquiry", "recommended", "try_on", "lost_demand"}:
        raise ValueError(f"unsupported catalog signal: {event_type}")
    catalog_ref = str(data.get("catalog_ref") or "").strip().lower()
    if not catalog_ref or len(catalog_ref) > 80:
        raise ValueError("catalog signal requires a valid catalog_ref")
    return CatalogSignal(event_type=event_type, catalog_ref=catalog_ref)


def _list(data: dict[str, Any], key: str) -> list[Any]:
    value = data.get(key, [])
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list")
    return value
