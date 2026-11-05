from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from forthatday_agent.application.dialogue_engine import UnifiedDialogueEngine
from forthatday_agent.config import Settings
from forthatday_agent.domain.messages import MessageActor, StoredMessage
from forthatday_agent.infrastructure.deepseek_chat_model import DeepSeekChatModel
from forthatday_agent.infrastructure.markdown_knowledge import MarkdownKnowledgeProvider
from forthatday_agent.infrastructure.sqlite_message_store import SqliteMessageStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one real DeepSeek dialogue turn.")
    parser.add_argument(
        "message",
        nargs="?",
        default="你好，我想周六下午来试婚纱，可以吗？",
    )
    args = parser.parse_args()

    settings = Settings.from_env()
    settings.validate(require_model=True)
    model = DeepSeekChatModel(
        settings.deepseek_api_key,
        model=settings.deepseek_model,
        base_url=settings.deepseek_base_url,
        timeout_seconds=settings.model_timeout_seconds,
    )
    knowledge = MarkdownKnowledgeProvider(settings.knowledge_path)

    with tempfile.TemporaryDirectory() as temp_dir:
        store = SqliteMessageStore(Path(temp_dir) / "smoke.db")
        now = datetime.now(timezone.utc)
        message = StoredMessage(
            id="smoke-message-1",
            conversation_id="smoke-conversation-1",
            external_message_id="smoke-external-1",
            actor=MessageActor.CUSTOMER,
            message_type="text",
            text_content=args.message,
            raw_payload={},
            occurred_at=now,
            received_at=now,
        )
        engine = UnifiedDialogueEngine(model, store, knowledge)
        decision = engine.decide(message.conversation_id, (message,))

    print(
        json.dumps(
            {
                "reply": decision.reply,
                "memory_updates": [
                    {
                        "key": item.key,
                        "value": item.value,
                        "source": item.source.value,
                        "confidence": item.confidence,
                    }
                    for item in decision.memory_updates
                ],
                "proposed_actions": [
                    {"type": item.type.value, "arguments": item.arguments}
                    for item in decision.proposed_actions
                ],
                "needs_human": decision.needs_human,
                "reason": decision.reason,
                "usage": {
                    "prompt_tokens": model.last_usage.prompt_tokens,
                    "completion_tokens": model.last_usage.completion_tokens,
                    "total_tokens": model.last_usage.total_tokens,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

