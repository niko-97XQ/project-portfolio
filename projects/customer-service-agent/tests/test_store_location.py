from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'src'))

from forthatday_agent.application.dialogue_engine import UnifiedDialogueEngine
from forthatday_agent.domain.memory import ConversationMemory
from forthatday_agent.domain.messages import MessageActor, StoredMessage
from forthatday_agent.infrastructure.markdown_knowledge import MarkdownKnowledgeProvider


ADDRESS = '上海市虹口区花园路128号，六街区6C301室'
GOOD_REPLY = '我们门店在' + ADDRESS + '。'


def output(reply):
    return {'reply': reply, 'memory_updates': [], 'proposed_actions': [], 'catalog_signals': [],
            'needs_human': False, 'reason': ''}


class Model:
    def __init__(self, replies):
        self.replies = replies
        self.inputs = []

    def generate(self, system_prompt, input_data):
        self.inputs.append(input_data)
        return output(self.replies[min(len(self.inputs) - 1, len(self.replies) - 1)])


class Context:
    def load_memory(self, conversation_id):
        return ConversationMemory()

    def recent_messages(self, conversation_id, limit=20):
        return ()


class Knowledge:
    def search(self, query, limit=8):
        return [{'source': 'store-location.md', 'title': '门店地址', 'text': '门店地址：' + ADDRESS}]


class CatalogAndLocation(Knowledge):
    def search(self, query, limit=8):
        return super().search(query, limit) + [{
            'source': 'customer_safe_catalog', 'catalog_ref': 'test-dress', 'image_available': 'true',
            'title': 'Test dress', 'text': 'A test dress.',
        }]


class StoreLocationTests(unittest.TestCase):
    def decide(self, replies, text='你们的门店在哪里啊', knowledge=None, state=None):
        now = datetime.now(timezone.utc)
        message = StoredMessage('message-1', 'conversation-1', 'external-1', MessageActor.CUSTOMER,
                                'text', text, {}, now, now)
        model = Model(replies)
        engine = UnifiedDialogueEngine(model, Context(), knowledge or Knowledge())
        decision = engine.decide('conversation-1', (message,), appointment_state=state or {})
        return decision, model

    def test_real_knowledge_retrieves_verified_complete_address(self):
        provider = MarkdownKnowledgeProvider(PROJECT / 'knowledge')
        for query in ('你们的门店在哪里啊', '发个定位', '门店具体地址', '怎么走'):
            with self.subTest(query=query):
                results = provider.search(query, limit=8)
                self.assertTrue(any(item['source'] == 'store-location.md' and ADDRESS in item['text']
                                    for item in results))

    def test_direct_text_address_needs_no_repair(self):
        decision, model = self.decide([GOOD_REPLY])
        self.assertEqual(decision.reply, GOOD_REPLY)
        self.assertEqual(len(model.inputs), 1)
        self.assertIn(ADDRESS, model.inputs[0]['retrieved_knowledge'][0]['text'])

    def test_reported_reply_is_repaired_to_address_without_visit_or_reservation(self):
        decision, model = self.decide([
            '我们门店在市中心，具体地址我发你定位～周六下午四点见，到时候店员会给你留好 Rina 那款。', GOOD_REPLY,
        ])
        self.assertEqual(decision.reply, GOOD_REPLY)
        violations = model.inputs[1]['repair_request']['violations']
        self.assertIn('unsupported_location_delivery', violations)
        self.assertIn('missing_store_address', violations)
        self.assertIn('unasked_location_reservation', violations)

    def test_navigation_link_promise_is_repaired(self):
        decision, model = self.decide([GOOD_REPLY + '我马上把导航链接发给你。', GOOD_REPLY])
        self.assertEqual(decision.reply, GOOD_REPLY)
        self.assertIn('unsupported_location_delivery', model.inputs[1]['repair_request']['violations'])

    def test_missing_room_number_requires_repair(self):
        decision, model = self.decide(['门店在上海市虹口区花园路128号。', GOOD_REPLY])
        self.assertEqual(decision.reply, GOOD_REPLY)
        self.assertIn('missing_store_address', model.inputs[1]['repair_request']['violations'])

    def test_unverified_city_is_repaired(self):
        decision, model = self.decide(['我们在杭州市中心。', GOOD_REPLY])
        self.assertEqual(decision.reply, GOOD_REPLY)
        self.assertIn('missing_store_address', model.inputs[1]['repair_request']['violations'])

    def test_map_image_promise_is_not_rewritten_into_dress_images(self):
        decision, model = self.decide(['我给你发张地图。', GOOD_REPLY], knowledge=CatalogAndLocation())
        self.assertEqual(decision.reply, GOOD_REPLY)
        self.assertEqual(decision.catalog_signals, ())
        self.assertIn('unsupported_location_delivery', model.inputs[1]['repair_request']['violations'])

    def test_negated_location_capability_is_not_a_delivery_promise(self):
        reply = '我无法发送定位，' + GOOD_REPLY
        decision, model = self.decide([reply])
        self.assertEqual(decision.reply, reply)
        self.assertEqual(len(model.inputs), 1)

    def test_explicit_appointment_question_may_include_confirmed_time(self):
        reply = GOOD_REPLY + '周六下午四点见。'
        decision, model = self.decide([reply], text='门店地址在哪，顺便确认周六下午四点的预约',
                                      state={'staff_time_approved': True})
        self.assertEqual(decision.reply, reply)
        self.assertEqual(len(model.inputs), 1)

    def test_location_only_does_not_repeat_even_confirmed_appointment(self):
        decision, model = self.decide([GOOD_REPLY + '周六下午四点见。', GOOD_REPLY],
                                      state={'staff_time_approved': True})
        self.assertEqual(decision.reply, GOOD_REPLY)
        self.assertIn('unasked_location_reservation', model.inputs[1]['repair_request']['violations'])

    def test_punctuation_and_spaces_in_complete_address_are_accepted(self):
        reply = '地址：上海市虹口区花园路128号\n六街区 6C301 室。'
        decision, model = self.decide([reply])
        self.assertEqual(decision.reply, reply)
        self.assertEqual(len(model.inputs), 1)

    def test_non_location_question_does_not_require_store_address(self):
        decision, model = self.decide(['你好，有什么想了解的？'], text='你好')
        self.assertEqual(len(model.inputs), 1)

    def test_repair_still_promising_location_is_rejected(self):
        with self.assertRaises(ValueError):
            self.decide([GOOD_REPLY + '我发你定位。'])


if __name__ == '__main__':
    unittest.main()
