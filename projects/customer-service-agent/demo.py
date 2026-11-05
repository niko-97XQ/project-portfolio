from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from service_agent.application.message_pipeline import ReliableMessagePipeline
from service_agent.domain.conversations import Conversation, ConversationOwner
from service_agent.domain.messages import InboundMessage
from service_agent.infrastructure.markdown_knowledge import MarkdownKnowledgeProvider
from service_agent.infrastructure.sqlite_message_store import SqliteMessageStore


class DemoReplyEngine:
    """Offline deterministic reply; no model or network is involved."""

    def __init__(self, knowledge: MarkdownKnowledgeProvider) -> None:
        self.knowledge = knowledge

    def reply(self, conversation_id: str, messages: tuple) -> str:
        matches = self.knowledge.search(messages[-1].text_content, limit=1)
        return matches[0]["text"] if matches else "没有相关示例知识，请联系人工。"


class RecordingGateway:
    def __init__(self) -> None:
        self.sent: list = []

    def send(self, message) -> str:
        self.sent.append(message)
        print(f"[本地发送记录] {message.payload['text']}")
        return f"demo-send-{len(self.sent)}"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    print("离线演示：全部资料为虚构，不调用大模型或企业微信。")
    root = Path(__file__).resolve().parent
    knowledge = MarkdownKnowledgeProvider(root / "examples" / "knowledge")
    with tempfile.TemporaryDirectory(prefix="service-agent-demo-") as directory:
        store = SqliteMessageStore(Path(directory) / "demo.db")
        gateway = RecordingGateway()
        pipeline = ReliableMessagePipeline(
            store, DemoReplyEngine(knowledge), gateway, debounce_seconds=0,
        )
        message = InboundMessage(
            channel="demo", channel_account_id="demo-account",
            external_user_id="demo-customer", external_message_id="demo-message-1",
            message_type="text", text_content="示例门店地址在哪里？",
        )
        first = pipeline.receive(message)
        repeated = pipeline.receive(message)
        assert not first.duplicate and repeated.duplicate
        print("[通过] 同一消息重复入站被识别，不重复创建处理任务。")
        now = datetime.now(timezone.utc) + timedelta(seconds=1)
        assert pipeline.process_once("demo-worker", now)
        assert pipeline.dispatch_once("demo-sender", now)
        assert len(gateway.sent) == 1
        assert "示例市" in gateway.sent[0].payload["text"]
        print("[通过] 检索虚构 Markdown，并通过真实 SQLite 出站任务处理。")

        pipeline.receive(InboundMessage(
            channel="demo", channel_account_id="demo-account",
            external_user_id="demo-customer", external_message_id="demo-message-2",
            message_type="text", text_content="再问一次示例门店地址。",
        ))
        later = now + timedelta(seconds=2)
        assert pipeline.process_once("demo-worker", later)
        store.activate_native_staff(first.conversation_id, "demo-staff")
        assert store.conversation_owner(first.conversation_id) is ConversationOwner.HUMAN
        assert pipeline.dispatch_once("demo-sender", later)
        assert len(gateway.sent) == 1
        assert store.status_counts("outbox_messages").get("cancelled") == 1
        print("[通过] 生成后、发送前切换人工，排队中的 AI 回复被取消。")

    conversation = Conversation("demo", "demo-customer", "demo-account")
    conversation.request_handoff("示例顾客需要员工协助")
    assert not conversation.can_ai_reply
    conversation.activate_human("demo-staff")
    conversation.request_return_to_ai("示例摘要：问题已说明，后续可继续咨询。")
    assert not conversation.can_ai_reply
    conversation.activate_ai()
    assert conversation.can_ai_reply
    print("[通过] 领域状态：AI → 待人工 → 人工 → 带摘要交还 AI。")
    print("完成。平台状态协调由模拟传输层回归测试覆盖，本演示未连接真实平台。")


if __name__ == "__main__":
    main()
