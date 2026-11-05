from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Protocol

from forthatday_agent.domain.payments import (
    PaymentDecision,
    PaymentEvaluation,
    PaymentEvidence,
    PaymentPolicy,
    evaluate_payment_evidence,
    normalize_payee,
)


AMOUNT_PATTERN = re.compile(
    r"(?:付款金额|支付金额|订单金额|实付金额|金额|[¥￥])\s*[:：]?\s*[¥￥]?\s*"
    r"[-−]?\s*([0-9]{1,6}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)"
)
PROMINENT_TRANSFER_AMOUNT = re.compile(
    r"(?:[-−]\s*[¥￥]?\s*|[¥￥]\s*)"
    r"([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]{1,2})?)"
)
SUCCESS_MARKERS = ("支付成功", "交易成功", "付款成功")
REFERENCE_PATTERN = re.compile(
    r"(?:交易单号|订单号|商户单号)\s*[:：]?\s*([A-Za-z0-9-]{6,})"
)


@dataclass(frozen=True, slots=True)
class OcrLine:
    text: str
    confidence: float


@dataclass(frozen=True, slots=True)
class OcrDocument:
    lines: tuple[OcrLine, ...]


class OcrProvider(Protocol):
    def extract(self, image_path: Path) -> OcrDocument: ...


class PaymentEvidenceStore(Protocol):
    def payment_evidence_decision(
        self, source_message_id: str
    ) -> PaymentDecision | None: ...

    def apply_payment_evaluation(
        self,
        evidence: PaymentEvidence,
        evaluation: PaymentEvaluation,
        image_sha256: str,
        media_storage_key: str,
    ) -> PaymentDecision: ...


class PaymentVerificationService:
    def __init__(
        self,
        store: PaymentEvidenceStore,
        ocr: OcrProvider,
        policy: PaymentPolicy,
    ) -> None:
        self.store = store
        self.ocr = ocr
        self.policy = policy

    def verify(self, source_message_id: str, image_path: str | Path) -> PaymentDecision:
        path = Path(image_path)
        if not source_message_id.strip():
            raise ValueError("source message id is required")
        if not path.is_file():
            raise ValueError("payment screenshot is missing")
        existing_decision = self.store.payment_evidence_decision(source_message_id)
        if existing_decision is not None:
            return existing_decision

        document = self.ocr.extract(path)
        evidence = extract_payment_evidence(document, source_message_id, self.policy)
        evaluation = evaluate_payment_evidence(evidence, self.policy)
        image_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        return self.store.apply_payment_evaluation(
            evidence,
            evaluation,
            image_sha256,
            str(path),
        )


def extract_payment_evidence(
    document: OcrDocument,
    source_message_id: str,
    policy: PaymentPolicy,
) -> PaymentEvidence:
    amount_matches: list[tuple[Decimal, float]] = []
    payee = ""
    payee_confidence = 0.0
    shows_success = False
    success_confidence = 0.0
    transaction_reference = ""

    aliases = {normalize_payee(alias): alias for alias in policy.payee_aliases}
    for line in document.lines:
        text = line.text.strip()
        for raw_amount in AMOUNT_PATTERN.findall(text):
            try:
                amount_matches.append(
                    (Decimal(raw_amount.replace(",", "")).quantize(Decimal("0.01")), line.confidence)
                )
            except InvalidOperation:
                continue
        for raw_amount in PROMINENT_TRANSFER_AMOUNT.findall(text):
            try:
                amount_matches.append(
                    (Decimal(raw_amount.replace(",", "")).quantize(Decimal("0.01")), line.confidence)
                )
            except InvalidOperation:
                continue

        normalized_line = normalize_payee(text)
        for normalized_alias, canonical_alias in aliases.items():
            if normalized_alias and normalized_alias in normalized_line:
                if line.confidence > payee_confidence:
                    payee = canonical_alias
                    payee_confidence = line.confidence

        if any(marker in text for marker in SUCCESS_MARKERS):
            shows_success = True
            success_confidence = max(success_confidence, line.confidence)
        reference = REFERENCE_PATTERN.search(text)
        if reference:
            transaction_reference = reference.group(1)

    unique_amounts = {amount for amount, _ in amount_matches}
    amount = next(iter(unique_amounts)) if len(unique_amounts) == 1 else None
    amount_confidence = min(
        (confidence for matched, confidence in amount_matches if matched == amount),
        default=0.0,
    )
    required_confidences = (amount_confidence, payee_confidence, success_confidence)
    confidence = min(required_confidences) if all(required_confidences) else 0.0

    return PaymentEvidence(
        amount=amount,
        payee=payee,
        shows_success=shows_success,
        confidence=confidence,
        source_message_id=source_message_id,
        transaction_reference=transaction_reference,
    )
