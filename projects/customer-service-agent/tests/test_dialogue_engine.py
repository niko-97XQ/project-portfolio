from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from forthatday_agent.application.dialogue_engine import (  # noqa: E402
    UnifiedDialogueEngine,
    _catalog_violations,
)
from forthatday_agent.application.message_pipeline import ReliableMessagePipeline  # noqa: E402
from forthatday_agent.domain.dialogue import DialogueDecision  # noqa: E402
from forthatday_agent.domain.memory import ConversationMemory, FactSource, MemoryFact  # noqa: E402
from forthatday_agent.domain.messages import (  # noqa: E402
    InboundMessage,
    MessageActor,
    StoredMessage,
)
from forthatday_agent.infrastructure.sqlite_message_store import SqliteMessageStore  # noqa: E402


NOW = datetime(2026, 7, 23, 9, 0, tzinfo=timezone.utc)


class FakeModel:
    def __init__(self, output: dict | list[dict]) -> None:
        self.outputs = output if isinstance(output, list) else [output]
        self.inputs: list[dict] = []

    def generate(self, system_prompt: str, input_data: dict) -> dict:
        self.inputs.append(input_data)
        index = min(len(self.inputs) - 1, len(self.outputs) - 1)
        return self.outputs[index]


class FakeKnowledge:
    def search(self, query: str, limit: int = 5) -> list[dict[str, str]]:
        return [{"source": "trial-policy", "text": "试纱费200元/6件，约2小时。"}]


class FakeCatalogKnowledge:
    def search(self, query: str, limit: int = 5) -> list[dict[str, str]]:
        return [
            {
                "source": "customer_safe_catalog",
                "title": "Verona 无袖",
                "text": "无袖缎面婚纱；库存需要店员确认",
                "catalog_ref": "bau-01",
            }
        ]


class RecordingCatalogKnowledge(FakeCatalogKnowledge):
    def __init__(self) -> None:
        self.queries: list[str] = []

    def search(self, query: str, limit: int = 5) -> list[dict[str, str]]:
        self.queries.append(query)
        return super().search(query, limit)


class ThreeCatalogKnowledge:
    def search(self, query: str, limit: int = 5) -> list[dict[str, str]]:
        return [
            {
                "source": "customer_safe_catalog",
                "title": f"Dress {index}",
                "text": "matching dress with an available image",
                "catalog_ref": f"dress-{index}",
                "image_available": "true",
            }
            for index in range(1, 4)
        ]


class StaticContext:
    def __init__(
        self,
        memory: ConversationMemory,
        history: tuple[StoredMessage, ...],
    ) -> None:
        self.memory = memory
        self.history = history

    def load_memory(self, conversation_id: str) -> ConversationMemory:
        return self.memory

    def recent_messages(self, conversation_id: str, limit: int = 20) -> tuple[StoredMessage, ...]:
        return self.history[-limit:]


class NullGateway:
    def send(self, message) -> str:
        return "sent-1"


def stored_message(message_id: str, text: str) -> StoredMessage:
    return StoredMessage(
        id=message_id,
        conversation_id="conversation-1",
        external_message_id=f"external-{message_id}",
        actor=MessageActor.CUSTOMER,
        message_type="text",
        text_content=text,
        raw_payload={},
        occurred_at=NOW,
        received_at=NOW,
    )


class DialogueEngineTests(unittest.TestCase):
    def test_image_recommendation_is_materialized_to_three_catalog_images(self) -> None:
        model = FakeModel(
            {
                "reply": "我发一款图片给你看看。",
                "memory_updates": [],
                "proposed_actions": [],
                "catalog_signals": [],
                "needs_human": False,
                "reason": "",
            }
        )
        engine = UnifiedDialogueEngine(
            model,
            StaticContext(ConversationMemory(), ()),
            ThreeCatalogKnowledge(),
        )

        decision = engine.decide(
            "conversation-1",
            (stored_message("m-three", "推荐婚纱图片"),),
        )

        self.assertEqual(
            [signal.catalog_ref for signal in decision.catalog_signals],
            ["dress-1", "dress-2", "dress-3"],
        )
        self.assertIn("这三款", decision.reply)

    def test_image_send_promise_requires_recommended_catalog_signal(self) -> None:
        model = FakeModel(
            [
                {
                    "reply": "我发一张短袖款的图片给你看看。",
                    "memory_updates": [],
                    "proposed_actions": [],
                    "catalog_signals": [
                        {"event_type": "inquiry", "catalog_ref": "bau-01"}
                    ],
                    "needs_human": False,
                    "reason": "",
                },
                {
                    "reply": "我发一张短袖款的图片给你看看。",
                    "memory_updates": [],
                    "proposed_actions": [],
                    "catalog_signals": [
                        {"event_type": "recommended", "catalog_ref": "bau-01"}
                    ],
                    "needs_human": False,
                    "reason": "",
                },
            ]
        )
        engine = UnifiedDialogueEngine(
            model,
            StaticContext(ConversationMemory(), ()),
            FakeCatalogKnowledge(),
        )

        decision = engine.decide(
            "conversation-1",
            (stored_message("m-image", "发图片看看"),),
        )

        self.assertEqual(len(model.inputs), 1)
        self.assertTrue(
            any(signal.event_type == "recommended" for signal in decision.catalog_signals)
        )

    def test_catalog_search_keeps_recent_customer_preference_for_short_followup(self) -> None:
        history = (stored_message("m-greeting", "你好"),)
        memory = ConversationMemory(
            facts=(
                MemoryFact(
                    key="style_preferences",
                    value=["短袖", "蕾丝"],
                    source=FactSource.CUSTOMER_EXPLICIT,
                    evidence_message_id="m-old-pref",
                ),
            )
        )
        knowledge = RecordingCatalogKnowledge()
        model = FakeModel(
            {
                "reply": "好，我先给你介绍一款。",
                "memory_updates": [],
                "proposed_actions": [],
                "catalog_signals": [],
                "needs_human": False,
                "reason": "",
            }
        )
        engine = UnifiedDialogueEngine(
            model,
            StaticContext(memory, history),
            knowledge,
        )

        engine.decide(
            "conversation-1",
            (stored_message("m-ok", "好的"),),
        )

        self.assertIn("短袖", knowledge.queries[0])
        self.assertIn("蕾丝", knowledge.queries[0])
        self.assertIn("好的", knowledge.queries[0])

    def test_catalog_recommendations_are_limited_to_three_per_reply(self) -> None:
        decision = DialogueDecision.from_model_output(
            {
                "reply": "先给你看四款风格接近的。",
                "memory_updates": [],
                "proposed_actions": [],
                "catalog_signals": [
                    {"event_type": "recommended", "catalog_ref": "dress-1"},
                    {"event_type": "recommended", "catalog_ref": "dress-2"},
                    {"event_type": "recommended", "catalog_ref": "dress-3"},
                    {"event_type": "recommended", "catalog_ref": "dress-4"},
                ],
                "needs_human": False,
                "reason": "",
            }
        )
        knowledge = tuple(
            {
                "source": "customer_safe_catalog",
                "catalog_ref": f"dress-{index}",
                "text": "customer-safe product",
            }
            for index in range(1, 5)
        )

        self.assertIn(
            "too_many_catalog_recommendations",
            _catalog_violations(decision, knowledge),
        )

    def test_catalog_signal_must_reference_retrieved_product_and_ref_stays_private(self) -> None:
        model = FakeModel(
            [
                {
                    "reply": "可以看看 bau-99。",
                    "memory_updates": [],
                    "proposed_actions": [],
                    "catalog_signals": [
                        {"event_type": "recommended", "catalog_ref": "bau-99"}
                    ],
                    "needs_human": False,
                    "reason": "",
                },
                {
                    "reply": "可以先看看 Verona 无袖这款，缎面会比较简洁利落。",
                    "memory_updates": [],
                    "proposed_actions": [],
                    "catalog_signals": [
                        {"event_type": "recommended", "catalog_ref": "bau-01"}
                    ],
                    "needs_human": False,
                    "reason": "",
                },
            ]
        )
        engine = UnifiedDialogueEngine(
            model,
            StaticContext(ConversationMemory(), ()),
            FakeCatalogKnowledge(),
        )

        decision = engine.decide(
            "conversation-1",
            (stored_message("m-catalog", "想看简洁的缎面婚纱"),),
        )

        self.assertEqual(len(model.inputs), 2)
        self.assertEqual(decision.catalog_signals[0].catalog_ref, "bau-01")
        self.assertNotIn("bau-01", decision.reply.lower())

    def test_model_receives_history_memory_and_knowledge(self) -> None:
        history = (stored_message("m1", "我十月办婚礼"),)
        current = (stored_message("m2", "周六想来试一下"),)
        memory = ConversationMemory(
            facts=(
                MemoryFact(
                    key="wedding_date",
                    value="十月",
                    source=FactSource.CUSTOMER_EXPLICIT,
                    evidence_message_id="m1",
                ),
            )
        )
        model = FakeModel(
            {
                "reply": "可以呀，周六想几点左右过来？我先帮你确认一下时间～",
                "memory_updates": [],
                "proposed_actions": [],
                "needs_human": False,
                "reason": "",
            }
        )
        engine = UnifiedDialogueEngine(model, StaticContext(memory, history), FakeKnowledge())

        decision = engine.decide("conversation-1", current)

        self.assertIn("周六", decision.reply)
        prompt = model.inputs[0]
        self.assertEqual(prompt["conversation_memory"]["confirmed_facts"]["wedding_date"], "十月")
        self.assertEqual(
            [item["content"] for item in prompt["recent_messages"]],
            ["我十月办婚礼", "周六想来试一下"],
        )
        self.assertEqual(prompt["retrieved_knowledge"][0]["source"], "trial-policy")

    def test_model_cannot_forge_staff_confirmed_memory(self) -> None:
        with self.assertRaises(ValueError):
            DialogueDecision.from_model_output(
                {
                    "reply": "这个时间可以约。",
                    "memory_updates": [
                        {
                            "key": "customer_name",
                            "value": "小许",
                            "source": "staff_confirmed",
                            "confidence": 1,
                        }
                    ],
                    "proposed_actions": [],
                    "needs_human": False,
                    "reason": "",
                }
            )

    def test_unknown_action_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            DialogueDecision.from_model_output(
                {
                    "reply": "已经帮你退款。",
                    "memory_updates": [],
                    "proposed_actions": [
                        {"type": "issue_refund", "arguments": {"amount": 200}}
                    ],
                    "needs_human": False,
                    "reason": "",
                }
            )

    def test_unapproved_time_commitment_is_repaired_by_ai(self) -> None:
        model = FakeModel(
            [
                {
                    "reply": "周六下午可以的，几点来？",
                    "memory_updates": [],
                    "proposed_actions": [],
                    "needs_human": False,
                    "reason": "",
                },
                {
                    "reply": "周六下午我先帮你记下啦，想几点左右到呢？我再跟店员确认一下～",
                    "memory_updates": [],
                    "proposed_actions": [],
                    "needs_human": False,
                    "reason": "",
                },
            ]
        )
        engine = UnifiedDialogueEngine(
            model,
            StaticContext(ConversationMemory(), ()),
            FakeKnowledge(),
        )

        decision = engine.decide(
            "conversation-1",
            (stored_message("m1", "周六下午可以吗"),),
        )

        self.assertIn("确认", decision.reply)
        self.assertNotIn("可以的", decision.reply)
        self.assertEqual(len(model.inputs), 2)
        self.assertIn("unapproved_time_commitment", model.inputs[1]["repair_request"]["violations"])

    def test_unapproved_can_come_wording_is_repaired_by_ai(self) -> None:
        model = FakeModel(
            [
                {
                    "reply": "你好！周六下午可以来试纱，我先帮你确认一下具体时间。",
                    "memory_updates": [],
                    "proposed_actions": [],
                    "needs_human": False,
                    "reason": "",
                },
                {
                    "reply": "你好！周六下午我先帮你记下，想几点左右到呢？我再跟店员确认时间。",
                    "memory_updates": [],
                    "proposed_actions": [],
                    "needs_human": False,
                    "reason": "",
                },
            ]
        )
        engine = UnifiedDialogueEngine(
            model,
            StaticContext(ConversationMemory(), ()),
            FakeKnowledge(),
        )

        decision = engine.decide(
            "conversation-1",
            (stored_message("m1", "你好，我想周六下午来试婚纱，可以吗？"),),
        )

        self.assertNotIn("可以来", decision.reply)
        self.assertEqual(len(model.inputs), 2)
        self.assertIn("unapproved_time_commitment", model.inputs[1]["repair_request"]["violations"])

    def test_price_discussion_can_suggest_store_without_false_time_violation(self) -> None:
        model = FakeModel(
            {
                "reply": "具体价格要结合款式和方案，可以到店后由店员和你详细沟通。",
                "memory_updates": [],
                "proposed_actions": [],
                "needs_human": False,
                "reason": "",
            }
        )
        engine = UnifiedDialogueEngine(
            model,
            StaticContext(ConversationMemory(), ()),
            FakeKnowledge(),
        )

        engine.decide("conversation-1", (stored_message("m1", "想了解一下价格范围"),))

        self.assertEqual(len(model.inputs), 1)

    def test_style_question_does_not_bundle_unasked_price_or_trial_details(self) -> None:
        model = FakeModel(
            [
                {
                    "reply": "我们有租赁和定制，租赁1.2k至1.1w，试纱费200元。",
                    "memory_updates": [],
                    "proposed_actions": [],
                    "needs_human": False,
                    "reason": "",
                },
                {
                    "reply": "我们整体偏简约、小众和轻盈，有缎面、轻纱、鱼尾这些。你喜欢哪种感觉？",
                    "memory_updates": [],
                    "proposed_actions": [],
                    "needs_human": False,
                    "reason": "",
                },
            ]
        )
        engine = UnifiedDialogueEngine(
            model,
            StaticContext(ConversationMemory(), ()),
            FakeKnowledge(),
        )

        decision = engine.decide(
            "conversation-1",
            (stored_message("m1", "你们这边有什么款式呀"),),
        )

        self.assertNotIn("1.2k", decision.reply)
        self.assertIn("unasked_pricing", model.inputs[1]["repair_request"]["violations"])

    def test_public_price_range_is_repaired_to_single_fuzzy_anchor(self) -> None:
        model = FakeModel(
            [
                {
                    "reply": "租赁整体约1.2k至1.1w。",
                    "memory_updates": [],
                    "proposed_actions": [],
                    "needs_human": False,
                    "reason": "",
                },
                {
                    "reply": "租赁常见款在5k左右，具体要结合款式沟通。",
                    "memory_updates": [],
                    "proposed_actions": [],
                    "needs_human": False,
                    "reason": "",
                },
            ]
        )
        engine = UnifiedDialogueEngine(
            model,
            StaticContext(ConversationMemory(), ()),
            FakeKnowledge(),
        )

        decision = engine.decide(
            "conversation-1",
            (stored_message("m1", "想了解一下价格范围"),),
        )

        self.assertIn("5k左右", decision.reply)
        self.assertIn(
            "precise_price_range_exposed",
            model.inputs[1]["repair_request"]["violations"],
        )

    def test_date_without_time_asks_time_before_name_and_phone(self) -> None:
        model = FakeModel(
            [
                {
                    "reply": "八月六号我先记下来，请告诉我姓名和电话，我帮你确认档期。",
                    "memory_updates": [],
                    "proposed_actions": [],
                    "needs_human": False,
                    "reason": "",
                },
                {
                    "reply": "八月六号先帮你记下啦，你希望几点左右到呢？",
                    "memory_updates": [],
                    "proposed_actions": [],
                    "needs_human": False,
                    "reason": "",
                },
            ]
        )
        engine = UnifiedDialogueEngine(
            model,
            StaticContext(ConversationMemory(), ()),
            FakeKnowledge(),
        )

        decision = engine.decide(
            "conversation-1",
            (stored_message("m1", "八月六号可以吗"),),
        )

        self.assertIn("几点", decision.reply)
        self.assertNotIn("电话", decision.reply)
        violations = model.inputs[1]["repair_request"]["violations"]
        self.assertIn("missing_preferred_time_question", violations)
        self.assertIn("premature_contact_collection", violations)

    def test_customer_phone_is_not_repeated_in_reply(self) -> None:
        model = FakeModel(
            [
                {
                    "reply": "收到，测试顾客，13800000000，我先帮你确认时间。",
                    "memory_updates": [],
                    "proposed_actions": [],
                    "needs_human": False,
                    "reason": "",
                },
                {
                    "reply": "收到，测试顾客，我先帮你确认一下时间。",
                    "memory_updates": [],
                    "proposed_actions": [],
                    "needs_human": False,
                    "reason": "",
                },
            ]
        )
        engine = UnifiedDialogueEngine(
            model,
            StaticContext(ConversationMemory(), ()),
            FakeKnowledge(),
        )

        decision = engine.decide(
            "conversation-1",
            (stored_message("m1", "测试顾客 13800000000"),),
        )

        self.assertNotIn("13800000000", decision.reply)
        self.assertIn(
            "customer_phone_repeated",
            model.inputs[1]["repair_request"]["violations"],
        )

    def test_handoff_action_without_reason_is_repaired_before_persistence(self) -> None:
        model = FakeModel(
            [
                {
                    "reply": "抱歉让你久等了，我马上转给店员处理。",
                    "memory_updates": [],
                    "proposed_actions": [{"type": "handoff_to_human", "arguments": {}}],
                    "needs_human": True,
                    "reason": "顾客投诉",
                },
                {
                    "reply": "抱歉让你久等了，我马上转给店员处理。",
                    "memory_updates": [],
                    "proposed_actions": [
                        {
                            "type": "handoff_to_human",
                            "arguments": {"reason": "顾客投诉"},
                        }
                    ],
                    "needs_human": True,
                    "reason": "顾客投诉",
                },
            ]
        )
        engine = UnifiedDialogueEngine(
            model,
            StaticContext(ConversationMemory(), ()),
            FakeKnowledge(),
        )

        decision = engine.decide(
            "conversation-1",
            (stored_message("m1", "我要投诉，你们一直不回消息"),),
        )

        self.assertEqual(decision.proposed_actions[0].arguments["reason"], "顾客投诉")
        self.assertIn("invalid_action:handoff_to_human", model.inputs[1]["repair_request"]["violations"][0])

    def test_exact_dress_price_question_requires_human_handoff(self) -> None:
        model = FakeModel(
            [
                {
                    "reply": "具体价格要结合款式沟通。",
                    "memory_updates": [],
                    "proposed_actions": [],
                    "needs_human": False,
                    "reason": "",
                },
                {
                    "reply": "具体方案我请店员和你沟通一下，不在这里报精确成交价。",
                    "memory_updates": [],
                    "proposed_actions": [
                        {
                            "type": "handoff_to_human",
                            "arguments": {"reason": "客户询问具体婚纱成交价"},
                        }
                    ],
                    "needs_human": True,
                    "reason": "客户询问具体婚纱成交价",
                },
            ]
        )
        engine = UnifiedDialogueEngine(
            model,
            StaticContext(ConversationMemory(), ()),
            FakeKnowledge(),
        )

        decision = engine.decide(
            "conversation-1",
            (stored_message("m1", "这件婚纱具体多少钱？"),),
        )

        self.assertTrue(decision.needs_human)
        self.assertEqual(decision.proposed_actions[0].type.value, "handoff_to_human")
        self.assertIn(
            "exact_price_requires_human",
            model.inputs[1]["repair_request"]["violations"],
        )

    def test_unknown_memory_field_is_repaired_by_ai(self) -> None:
        model = FakeModel(
            [
                {
                    "reply": "我先帮你记下周六下午。",
                    "memory_updates": [
                        {
                            "key": "preferred_day",
                            "value": "周六",
                            "source": "customer_explicit",
                            "evidence_message_id": "m1",
                            "confidence": 1,
                        }
                    ],
                    "proposed_actions": [],
                    "needs_human": False,
                    "reason": "",
                },
                {
                    "reply": "周六下午我先帮你记下啦，想几点左右到呢？",
                    "memory_updates": [],
                    "proposed_actions": [],
                    "needs_human": False,
                    "reason": "",
                },
            ]
        )
        engine = UnifiedDialogueEngine(
            model,
            StaticContext(ConversationMemory(), ()),
            FakeKnowledge(),
        )

        decision = engine.decide(
            "conversation-1",
            (stored_message("m1", "周六下午想来"),),
        )

        self.assertEqual(decision.memory_updates, ())
        self.assertEqual(len(model.inputs), 2)
        self.assertIn("invalid_output_schema", model.inputs[1]["repair_request"]["violations"][0])


class DialoguePersistenceTests(unittest.TestCase):
    def test_reply_memory_and_action_are_committed_together(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SqliteMessageStore(Path(temp_dir) / "dialogue.db")
            received = store.ingest(
                InboundMessage(
                    channel="wecom_kf",
                    channel_account_id="open-kfid-1",
                    external_user_id="customer-1",
                    external_message_id="msg-1",
                    message_type="text",
                    text_content="我叫小许，想约周六下午三点",
                    occurred_at=NOW,
                ),
                debounce_seconds=0,
            )
            model = FakeModel(
                {
                    "reply": "收到呀小许，周六下午三点我先帮你确认一下～",
                    "memory_updates": [
                        {
                            "key": "customer_name",
                            "value": "小许",
                            "source": "customer_explicit",
                            "evidence_message_id": received.message_id,
                            "confidence": 1,
                        }
                    ],
                    "proposed_actions": [
                        {
                            "type": "request_time_confirmation",
                            "arguments": {"requested_time": "周六下午三点"},
                        }
                    ],
                    "needs_human": False,
                    "reason": "",
                }
            )
            engine = UnifiedDialogueEngine(model, store, FakeKnowledge())
            pipeline = ReliableMessagePipeline(
                store,
                engine,
                NullGateway(),
                debounce_seconds=0,
            )
            process_time = datetime.now(timezone.utc) + timedelta(minutes=1)

            self.assertTrue(pipeline.process_once("worker-1", process_time))

            memory = store.load_memory(received.conversation_id)
            self.assertEqual(memory.facts[0].key, "customer_name")
            self.assertEqual(memory.facts[0].value, "小许")
            self.assertEqual(store.action_count(), 1)
            self.assertEqual(store.status_counts("outbox_messages"), {"pending": 1})
            self.assertEqual(store.status_counts("inbox_jobs"), {"done": 1})


if __name__ == "__main__":
    unittest.main()
