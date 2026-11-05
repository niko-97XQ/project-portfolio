from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
import unicodedata
from uuid import uuid4


class PaymentDecision(StrEnum):
    ACCEPT = "accept"
    REVIEW = "review"


@dataclass(frozen=True, slots=True)
class PaymentPolicy:
    expected_amount: Decimal = Decimal("200.00")
    payee_aliases: tuple[str, ...] = ()
    minimum_confidence: float = 0.90


@dataclass(frozen=True, slots=True)
class PaymentEvidence:
    amount: Decimal | None
    payee: str
    shows_success: bool
    confidence: float
    source_message_id: str
    transaction_reference: str = ""
    id: str = field(default_factory=lambda: str(uuid4()))


@dataclass(frozen=True, slots=True)
class PaymentEvaluation:
    decision: PaymentDecision
    reasons: tuple[str, ...]


def evaluate_payment_evidence(
    evidence: PaymentEvidence,
    policy: PaymentPolicy,
) -> PaymentEvaluation:
    reasons: list[str] = []

    if evidence.amount != policy.expected_amount:
        reasons.append("amount_mismatch")
    if not evidence.shows_success:
        reasons.append("success_state_missing")
    if evidence.confidence < policy.minimum_confidence:
        reasons.append("confidence_too_low")

    expected_payees = {normalize_payee(item) for item in policy.payee_aliases if item.strip()}
    if not expected_payees:
        reasons.append("payee_policy_not_configured")
    elif normalize_payee(evidence.payee) not in expected_payees:
        reasons.append("payee_mismatch")

    if not evidence.source_message_id.strip():
        reasons.append("source_message_missing")

    decision = PaymentDecision.REVIEW if reasons else PaymentDecision.ACCEPT
    return PaymentEvaluation(decision=decision, reasons=tuple(reasons))


def normalize_payee(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).lower()
    return "".join(character for character in normalized if character.isalnum() or character == "*")
