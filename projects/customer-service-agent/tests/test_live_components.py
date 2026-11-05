from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC))

from forthatday_agent.application.action_policy import (  # noqa: E402
    ActionContext,
    ActionPolicy,
)
from forthatday_agent.domain.dialogue import ActionType, ProposedAction  # noqa: E402
from forthatday_agent.infrastructure.deepseek_chat_model import (  # noqa: E402
    DeepSeekChatModel,
    ModelTransportError,
)
from forthatday_agent.infrastructure.markdown_knowledge import (  # noqa: E402
    MarkdownKnowledgeProvider,
)


class FakeTransport:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls: list[dict] = []

    def post_json(self, url, headers, payload, timeout_seconds):
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "payload": payload,
                "timeout_seconds": timeout_seconds,
            }
        )
        return self.response


class DeepSeekChatModelTests(unittest.TestCase):
    def test_uses_v4_non_thinking_json_mode(self) -> None:
        transport = FakeTransport(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": '{"reply":"你好呀","memory_updates":[],"proposed_actions":[],"needs_human":false,"reason":""}'
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                },
            }
        )
        model = DeepSeekChatModel("secret-for-test", transport=transport)

        result = model.generate("请输出 JSON", {"customer_message": "你好"})

        self.assertEqual(result["reply"], "你好呀")
        payload = transport.calls[0]["payload"]
        self.assertEqual(payload["model"], "deepseek-v4-flash")
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(model.last_usage.total_tokens, 120)

    def test_deprecated_model_name_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            DeepSeekChatModel("secret-for-test", model="deepseek-chat")

    def test_uses_siliconflow_model_id_and_non_thinking_parameter(self) -> None:
        transport = FakeTransport(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"reply":"ok"}'},
                    }
                ]
            }
        )
        model = DeepSeekChatModel(
            "secret-for-test",
            model="deepseek-ai/DeepSeek-V4-Flash",
            base_url="https://api.siliconflow.cn/v1",
            transport=transport,
        )

        model.generate("output JSON", {})

        payload = transport.calls[0]["payload"]
        self.assertEqual(payload["model"], "deepseek-ai/DeepSeek-V4-Flash")
        self.assertFalse(payload["enable_thinking"])
        self.assertNotIn("thinking", payload)

    def test_truncated_json_response_is_rejected(self) -> None:
        transport = FakeTransport(
            {
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": '{"reply":"incomplete"'},
                    }
                ]
            }
        )
        model = DeepSeekChatModel("secret-for-test", transport=transport)

        with self.assertRaises(ModelTransportError):
            model.generate("输出 JSON", {})


class KnowledgeProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.knowledge = MarkdownKnowledgeProvider(PROJECT_ROOT / "knowledge")

    def test_trial_fee_query_finds_customer_safe_policy(self) -> None:
        results = self.knowledge.search("试纱多少钱，可以试几件")

        self.assertTrue(results)
        self.assertIn("200", "\n".join(item["text"] for item in results))
        self.assertIn("6", "\n".join(item["text"] for item in results))

    def test_reschedule_query_finds_appointment_workflow(self) -> None:
        results = self.knowledge.search("我想改期，换一个时间")

        self.assertTrue(results)
        self.assertTrue(any(item["source"] == "appointment-workflow.md" for item in results))

    def test_style_query_prefers_style_overview(self) -> None:
        results = self.knowledge.search("你们这边有什么款式呀")

        self.assertTrue(results)
        self.assertEqual(results[0]["source"], "style-overview.md")


class ActionPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = ActionPolicy()

    def test_payment_codes_require_staff_approved_time(self) -> None:
        action = ProposedAction(ActionType.SEND_PAYMENT_CODES, {})

        rejected = self.policy.validate(
            action,
            ActionContext(appointment_status="time_pending_staff", has_confirmed_time=False),
        )
        accepted = self.policy.validate(
            action,
            ActionContext(appointment_status="awaiting_payment", has_confirmed_time=True),
        )

        self.assertFalse(rejected.accepted)
        self.assertTrue(accepted.accepted)

    def test_payment_verification_requires_evidence(self) -> None:
        action = ProposedAction(
            ActionType.VERIFY_PAYMENT_EVIDENCE,
            {"source_message_id": "msg-1"},
        )

        result = self.policy.validate(
            action,
            ActionContext(
                appointment_status="awaiting_payment",
                has_confirmed_time=True,
                has_payment_evidence=False,
            ),
        )

        self.assertFalse(result.accepted)
        self.assertIn("evidence", result.reason)


if __name__ == "__main__":
    unittest.main()
