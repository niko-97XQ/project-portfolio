from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from forthatday_agent.domain.dialogue import DialogueDecision


TIME_APPROVAL_PATTERNS = (
    re.compile(r"(?:周[一二三四五六日天]|今天|明天|后天|上午|下午|晚上|\d+[月号日点]).{0,10}(?:可以|能)(?:的|约|安排|来|到|试纱|试婚纱)"),
    re.compile(r"(?:可以约|能约|有档期|时间没问题|已经?安排|安排好了|已锁档|已经?锁档)"),
)
UNSUPPORTED_FOLLOWUP = re.compile(r"(?:稍后|晚点)(?:再)?(?:回复|联系)(?:你|您)?")
INTERNAL_MODEL_ID = re.compile(r"\b[A-Z]{2,}-\d{1,4}\b")
EXACT_DRESS_PRICE = re.compile(r"(?<!试纱费)(?<!为)\b(?:[1-9]\d{3,4})\s*元\b")
PRECISE_PRICE_RANGE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:[kKwW]|千|万)\+?\s*"
    r"(?:至|到|[-~—–])\s*\d+(?:\.\d+)?\s*(?:[kKwW]|千|万)\+?"
)
LOCATION_DELIVERY = re.compile(
    r"(?:发|发送|分享|推送).{0,8}(?:定位|位置卡片|导航链接|地图链接|地图卡片|导航卡片|地图)|"
    r"(?:定位|位置卡片|导航链接|地图链接|地图卡片|导航卡片|地图).{0,8}(?:发|发送|分享|推送)"
)
LOCATION_DELIVERY_DENIAL = re.compile(r"(?:不能|无法|不支持|不会|不发|不用|无需|没法|发不了|发不出)")


def promises_location_delivery(reply: str) -> bool:
    clauses = re.split(r"[，,。；;！？!?\n]|但是|不过|但|而是", reply)
    return any(LOCATION_DELIVERY.search(clause) and not LOCATION_DELIVERY_DENIAL.search(clause)
               for clause in clauses)


@dataclass(frozen=True, slots=True)
class SafetyResult:
    accepted: bool
    violations: tuple[str, ...] = ()


class DialogueSafetyPolicy:
    def validate(
        self,
        decision: DialogueDecision,
        appointment_state: dict[str, Any] | None = None,
    ) -> SafetyResult:
        state = appointment_state or {}
        violations: list[str] = []
        reply = decision.reply

        if not bool(state.get("staff_time_approved")):
            if any(pattern.search(reply) for pattern in TIME_APPROVAL_PATTERNS):
                violations.append("unapproved_time_commitment")
        if UNSUPPORTED_FOLLOWUP.search(reply) and not decision.proposed_actions:
            violations.append("unsupported_followup_promise")
        if promises_location_delivery(reply):
            violations.append("unsupported_location_delivery")
        if INTERNAL_MODEL_ID.search(reply):
            violations.append("internal_model_id_exposed")
        if EXACT_DRESS_PRICE.search(reply) and "200元" not in reply:
            violations.append("exact_dress_price_exposed")
        if PRECISE_PRICE_RANGE.search(reply):
            violations.append("precise_price_range_exposed")

        return SafetyResult(not violations, tuple(violations))
