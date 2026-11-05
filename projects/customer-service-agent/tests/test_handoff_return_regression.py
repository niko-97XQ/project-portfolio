from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from service_agent.application.staff_appointments import PaymentCodes, StaffAppointmentService
from service_agent.domain.conversations import ConversationOwner
from service_agent.domain.dialogue import ActionType, DialogueDecision, ProposedAction
from service_agent.domain.messages import InboundMessage
from service_agent.infrastructure.sqlite_message_store import SqliteMessageStore
from service_agent.infrastructure.wecom_api import WeComApiClient, WeComNativeHandoffGateway


class NativeStateTransport:
    def __init__(self):
        self.state = 3
        self.allow_direct = False
        self.query_error = False
        self.close_error = False
        self.ignore_close = False
        self.requests = []

    def request_json(self, method, path, query, body=None):
        self.requests.append((method, path, body))
        if path == '/cgi-bin/gettoken':
            return {'errcode': 0, 'access_token': 'test-token', 'expires_in': 7200}
        if path == '/cgi-bin/kf/service_state/get':
            if self.query_error:
                return {'errcode': 40014, 'errmsg': 'invalid access token'}
            return {'errcode': 0, 'service_state': self.state}
        if path == '/cgi-bin/kf/service_state/trans':
            target = body['service_state']
            if target == 1 and not self.allow_direct:
                return {'errcode': 95016, 'errmsg': 'not allow transition state'}
            if target == 4 and self.close_error:
                return {'errcode': 95016, 'errmsg': 'not allow transition state'}
            if target != 4 or not self.ignore_close:
                self.state = target
            return {'errcode': 0}
        raise AssertionError(path)


class HandoffReturnRegressionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.store = SqliteMessageStore(root / 'test.db')
        received = self.store.ingest(InboundMessage(
            channel='wecom_kf', channel_account_id='test-account', external_user_id='test-customer',
            external_message_id='test-message', message_type='text', text_content='human please',
        ), debounce_seconds=0)
        self.conversation_id = received.conversation_id
        batch = self.store.claim_inbox_batch('test-worker', datetime.now(timezone.utc) + timedelta(minutes=1))
        self.store.complete_dialogue_batch(batch, DialogueDecision(
            reply='Staff will assist.', memory_updates=(), proposed_actions=(
                ProposedAction(ActionType.HANDOFF_TO_HUMAN, {'reason': 'customer requested staff'}),
            ), needs_human=True, reason='customer requested staff',
        ))
        self.task_id = self.store.first_open_task_id('take_over_conversation')
        self.store.activate_handoff_task(self.task_id, 'test-staff')
        self.transport = NativeStateTransport()
        client = WeComApiClient('test-corp', 'test-secret', self.transport)
        self.gateway = WeComNativeHandoffGateway(client, self.store, 'test-staff')
        self.service = StaffAppointmentService(
            self.store, PaymentCodes(root / 'unused-wechat', root / 'unused-alipay'), self.gateway,
        )

    def tearDown(self):
        self.directory.cleanup()

    def return_to_ai(self):
        self.service.return_handoff(self.task_id, 'test-staff', 'Staff completed the requested explanation.')

    def assert_still_human(self):
        self.assertIs(self.store.conversation_owner(self.conversation_id), ConversationOwner.HUMAN)
        self.assertEqual(self.store.audit_count('handoff_returned_to_ai'), 0)
        self.assertEqual(self.service.get_handoff_task(self.task_id).status, 'open')

    def test_disallowed_direct_return_ends_human_session_before_local_ai(self):
        self.return_to_ai()
        self.assertEqual(self.transport.state, 4)
        self.assertIs(self.store.conversation_owner(self.conversation_id), ConversationOwner.AI)
        self.assertEqual(self.store.audit_count('handoff_returned_to_ai'), 1)
        self.assertIn('Staff completed', self.store.load_memory(self.conversation_id).summary)
        self.assertEqual([body['service_state'] for _, path, body in self.transport.requests
                          if path.endswith('/trans')], [1, 4])

    def test_confirmed_ended_session_allows_local_ai(self):
        self.transport.state = 4
        self.return_to_ai()
        self.assertIs(self.store.conversation_owner(self.conversation_id), ConversationOwner.AI)
        self.assertTrue(any(path.endswith('/get') for _, path, _ in self.transport.requests))

    def test_supported_direct_transition_preserves_existing_path(self):
        self.transport.allow_direct = True
        self.return_to_ai()
        self.assertEqual(self.transport.state, 1)
        self.assertIs(self.store.conversation_owner(self.conversation_id), ConversationOwner.AI)

    def test_already_ai_after_rejected_transition_does_not_end_session(self):
        self.transport.state = 1
        self.return_to_ai()
        self.assertEqual(self.transport.state, 1)
        self.assertIs(self.store.conversation_owner(self.conversation_id), ConversationOwner.AI)
        self.assertEqual([body['service_state'] for _, path, body in self.transport.requests
                          if path.endswith('/trans')], [1])

    def test_ambiguous_transition_error_is_not_treated_as_ended(self):
        self.transport.state = 2
        with self.assertRaises(ValueError):
            self.return_to_ai()
        self.assert_still_human()

    def test_failed_state_query_does_not_enable_local_ai(self):
        self.transport.query_error = True
        with self.assertRaises(ValueError):
            self.return_to_ai()
        self.assert_still_human()

    def test_failed_end_session_does_not_enable_local_ai(self):
        self.transport.close_error = True
        with self.assertRaises(ValueError):
            self.return_to_ai()
        self.assert_still_human()

    def test_success_response_without_ended_state_does_not_enable_ai(self):
        self.transport.ignore_close = True
        with self.assertRaises(ValueError):
            self.return_to_ai()
        self.assert_still_human()

    def test_repeat_click_does_not_transition_twice(self):
        self.return_to_ai()
        requests = len(self.transport.requests)
        self.return_to_ai()
        self.assertEqual(len(self.transport.requests), requests)
        self.assertEqual(self.store.audit_count('handoff_returned_to_ai'), 1)

    def test_stale_resolved_task_cannot_end_a_new_human_session(self):
        self.return_to_ai()
        self.store.activate_native_staff(self.conversation_id, 'test-staff')
        self.transport.state = 3
        requests = len(self.transport.requests)
        with self.assertRaises(ValueError):
            self.return_to_ai()
        self.assertEqual(len(self.transport.requests), requests)
        self.assertEqual(self.transport.state, 3)

    def test_empty_summary_never_calls_native_api(self):
        with self.assertRaises(ValueError):
            self.service.return_handoff(self.task_id, 'test-staff', '')
        self.assertEqual(self.transport.requests, [])
        self.assert_still_human()


if __name__ == '__main__':
    unittest.main()
