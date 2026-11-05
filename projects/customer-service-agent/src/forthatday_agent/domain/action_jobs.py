from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .dialogue import ActionType


@dataclass(frozen=True, slots=True)
class ActionJob:
    id: str
    conversation_id: str
    source_message_id: str
    action_type: ActionType
    arguments: dict[str, Any]
    attempt_count: int

