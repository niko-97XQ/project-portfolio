from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from forthatday_agent.domain.payments import PaymentPolicy


def load_payment_policy(path: str | Path) -> PaymentPolicy:
    policy_path = Path(path)
    if not policy_path.is_file():
        raise ValueError("payment policy file is missing")
    data = json.loads(policy_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("payment policy must be a JSON object")
    try:
        amount = Decimal(str(data.get("expected_amount") or ""))
    except InvalidOperation as error:
        raise ValueError("payment policy amount is invalid") from error
    aliases = data.get("payee_aliases")
    if not isinstance(aliases, list) or not all(
        isinstance(item, str) and item.strip() for item in aliases
    ):
        raise ValueError("payment policy requires non-empty payee aliases")
    minimum_confidence = float(data.get("minimum_confidence", 0.90))
    if not 0 <= minimum_confidence <= 1:
        raise ValueError("payment confidence must be between zero and one")
    return PaymentPolicy(
        expected_amount=amount.quantize(Decimal("0.01")),
        payee_aliases=tuple(item.strip() for item in aliases),
        minimum_confidence=minimum_confidence,
    )
