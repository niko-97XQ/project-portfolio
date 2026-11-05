from __future__ import annotations

import sys
import sqlite3
import unittest
from dataclasses import replace
from pathlib import Path
import tempfile
from urllib.parse import parse_qs, urlsplit


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from forthatday_agent.application.staff_access import (  # noqa: E402
    StaffActionSigner,
    StaffPasswordVerifier,
    StaffSessionSigner,
)
from forthatday_agent.application.staff_appointments import (  # noqa: E402
    StaffHandoffMessage,
    StaffHandoffTask,
    StaffPaymentTask,
    StaffTimeTask,
)
from forthatday_agent.application.staff_web import StaffTaskWebApp  # noqa: E402
from forthatday_agent.infrastructure.catalog_store import CatalogStore  # noqa: E402


class FakeWeComClient:
    def __init__(self, staff_id: str = "clerk-1") -> None:
        self.staff_id = staff_id

    def staff_user_id_from_oauth_code(self, code: str) -> str:
        if code != "valid-code":
            raise ValueError("invalid code")
        return self.staff_id


class FakeStaffService:
    def __init__(self, evidence_path: Path) -> None:
        self.task = StaffTimeTask(
            id="task-1",
            status="open",
            appointment_status="time_pending_staff",
            customer_name="小林",
            customer_phone="13800000000",
            requested_time="2026-08-01 15:00",
        )
        self.actions: list[str] = []
        self.payment_task = StaffPaymentTask(
            id="payment-task-1",
            status="open",
            appointment_status="payment_review",
            customer_name="小林",
            confirmed_time="2026-08-01 15:00",
            extracted_amount="",
            extracted_payee="Forthatday-Studio",
            shows_success=True,
            confidence=0.0,
            reasons=("amount_mismatch", "confidence_too_low"),
            evidence_path=evidence_path,
        )
        self.handoff_task = StaffHandoffTask(
            id="handoff-task-1",
            status="open",
            owner="human_pending",
            handoff_status="requested",
            reason="顾客要求真人回复",
            customer_name="小林",
            customer_phone="13800000000",
            appointment_status="confirmed",
            appointment_time="2026-08-01 15:00",
            messages=(
                StaffHandoffMessage("customer", "我想找真人回答"),
                StaffHandoffMessage("system", "我马上请店员来和你对接。"),
            ),
        )

    def get_time_task(self, task_id: str) -> StaffTimeTask:
        if task_id != self.task.id:
            raise ValueError("not found")
        return self.task

    def approve_time(self, task_id: str, staff_id: str) -> None:
        self.actions.append("approve")
        self.task = replace(
            self.task,
            status="resolved",
            appointment_status="awaiting_payment",
        )

    def reject_time(self, task_id: str, staff_id: str) -> None:
        self.actions.append("reject")
        self.task = replace(
            self.task,
            status="resolved",
            appointment_status="collecting_details",
            requested_time="",
        )

    def get_payment_task(self, task_id: str) -> StaffPaymentTask:
        if task_id != self.payment_task.id:
            raise ValueError("not found")
        return self.payment_task

    def approve_payment(self, task_id: str, staff_id: str) -> None:
        self.actions.append("approve_payment")
        self.payment_task = replace(
            self.payment_task,
            status="resolved",
            appointment_status="confirmed",
        )

    def reject_payment(self, task_id: str, staff_id: str) -> None:
        self.actions.append("reject_payment")
        self.payment_task = replace(
            self.payment_task,
            status="resolved",
            appointment_status="awaiting_payment",
        )

    def get_handoff_task(self, task_id: str) -> StaffHandoffTask:
        if task_id != self.handoff_task.id:
            raise ValueError("not found")
        return self.handoff_task

    def activate_handoff(self, task_id: str, staff_id: str) -> None:
        self.actions.append("take_over")
        self.handoff_task = replace(
            self.handoff_task,
            owner="human",
            handoff_status="active",
        )

    def return_handoff(self, task_id: str, staff_id: str, summary: str) -> None:
        if not summary.strip():
            raise ValueError("交接摘要不能为空")
        self.actions.append(f"return_ai:{summary}")
        self.handoff_task = replace(
            self.handoff_task,
            status="resolved",
            owner="ai",
            handoff_status="none",
        )

    def send_handoff_reply(self, task_id: str, staff_id: str, text: str) -> None:
        if not text.strip():
            raise ValueError("回复内容不能为空")
        self.actions.append(f"reply:{text}")


class StaffTaskWebAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.evidence_path = Path(self.temp_dir.name) / "evidence.jpg"
        self.evidence_path.write_bytes(b"test-image")
        self.signer = StaffActionSigner("k" * 32, clock=lambda: 1000)
        self.service = FakeStaffService(self.evidence_path)
        self.app = StaffTaskWebApp(self.service, self.signer)
        url = self.signer.issue_url("https://example.test", "task-1")
        self.query = {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_valid_link_shows_customer_and_actions(self) -> None:
        response = self.app.get("/staff/tasks/task-1", self.query)

        self.assertEqual(response.status, 200)
        self.assertIn("小林".encode(), response.body)
        self.assertIn("同意这个时间".encode(), response.body)
        self.assertNotIn(b"conversation", response.body)

    def test_tampered_link_does_not_reveal_task(self) -> None:
        query = dict(self.query, token="0" * 64)

        response = self.app.get("/staff/tasks/task-1", query)

        self.assertEqual(response.status, 403)
        self.assertNotIn("13800000000".encode(), response.body)

    def test_approve_calls_service_and_shows_final_state(self) -> None:
        response = self.app.post(
            "/staff/tasks/task-1",
            self.query,
            {"action": "approve"},
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(self.service.actions, ["approve"])
        self.assertIn("等待顾客付款".encode(), response.body)

    def test_reject_calls_service_and_hides_old_time(self) -> None:
        response = self.app.post(
            "/staff/tasks/task-1",
            self.query,
            {"action": "reject"},
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(self.service.actions, ["reject"])
        self.assertNotIn("2026-08-01".encode(), response.body)

    def test_payment_review_page_and_evidence_require_signed_link(self) -> None:
        url = self.signer.issue_url("https://example.test", "payment-task-1")
        query = {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}

        page = self.app.get("/staff/tasks/payment-task-1", query)
        evidence = self.app.get("/staff/tasks/payment-task-1/evidence", query)
        denied = self.app.get(
            "/staff/tasks/payment-task-1/evidence",
            dict(query, token="0" * 64),
        )

        self.assertEqual(page.status, 200)
        self.assertIn("确认已付款".encode(), page.body)
        self.assertEqual(evidence.status, 200)
        self.assertEqual(evidence.body, b"test-image")
        self.assertEqual(denied.status, 403)

    def test_staff_can_reject_payment_from_page(self) -> None:
        url = self.signer.issue_url("https://example.test", "payment-task-1")
        query = {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}

        response = self.app.post(
            "/staff/tasks/payment-task-1",
            query,
            {"action": "reject_payment"},
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(self.service.actions, ["reject_payment"])
        self.assertIn("等待顾客付款".encode(), response.body)

    def test_payment_approval_requires_explicit_verification(self) -> None:
        url = self.signer.issue_url("https://example.test", "payment-task-1")
        query = {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}

        rejected = self.app.post(
            "/staff/tasks/payment-task-1",
            query,
            {"action": "approve_payment"},
        )
        accepted = self.app.post(
            "/staff/tasks/payment-task-1",
            query,
            {"action": "approve_payment", "payment_verified": "yes"},
        )

        self.assertEqual(rejected.status, 400)
        self.assertEqual(accepted.status, 200)
        self.assertEqual(self.service.actions, ["approve_payment"])

    def test_handoff_page_supports_takeover_and_return_to_ai(self) -> None:
        url = self.signer.issue_url("https://example.test", "handoff-task-1")
        query = {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}

        page = self.app.get("/staff/tasks/handoff-task-1", query)
        takeover = self.app.post(
            "/staff/tasks/handoff-task-1",
            query,
            {"action": "take_over"},
        )
        active_page = self.app.get("/staff/tasks/handoff-task-1", query)
        returned = self.app.post(
            "/staff/tasks/handoff-task-1",
            query,
            {"action": "return_ai", "summary": "已解释付款截图要求"},
        )

        self.assertEqual(page.status, 200)
        self.assertIn("我来接管".encode(), page.body)
        self.assertIn("我想找真人回答".encode(), page.body)
        self.assertEqual(takeover.status, 200)
        self.assertIn("交还 AI".encode(), active_page.body)
        self.assertEqual(returned.status, 200)
        self.assertIn("顾客下一条新消息".encode(), returned.body)
        self.assertIn("不会补发历史消息".encode(), returned.body)
        self.assertEqual(
            self.service.actions,
            ["take_over", "return_ai:已解释付款截图要求"],
        )

    def test_return_to_ai_requires_summary(self) -> None:
        self.service.handoff_task = replace(self.service.handoff_task, owner="human")
        url = self.signer.issue_url("https://example.test", "handoff-task-1")
        query = {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}

        response = self.app.post(
            "/staff/tasks/handoff-task-1",
            query,
            {"action": "return_ai", "summary": ""},
        )

        self.assertEqual(response.status, 409)
        self.assertEqual(self.service.actions, [])

    def test_active_handoff_page_sends_staff_reply(self) -> None:
        self.service.handoff_task = replace(self.service.handoff_task, owner="human")
        url = self.signer.issue_url("https://example.test", "handoff-task-1")
        query = {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}

        response = self.app.post(
            "/staff/tasks/handoff-task-1",
            query,
            {"action": "send_reply", "reply": "你好，我是店员，接下来由我回复你。"},
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(
            self.service.actions,
            ["reply:你好，我是店员，接下来由我回复你。"],
        )
        self.assertIn("发送给顾客".encode(), response.body)

    def test_inventory_dashboard_requires_oauth_and_uses_catalog_counts(self) -> None:
        catalog_root = Path(self.temp_dir.name) / "catalog"
        catalog_root.mkdir()
        catalog_path = Path(self.temp_dir.name) / "catalog.db"
        catalog = CatalogStore(catalog_path, catalog_root=catalog_root)
        session_signer = StaffSessionSigner("s" * 32, clock=lambda: 1000)
        password_verifier = StaffPasswordVerifier((
            StaffPasswordVerifier.create_record(
                "owner",
                "correct-horse-battery-staple",
                salt=b"1" * 16,
            ),
        ))
        app = StaffTaskWebApp(
            self.service,
            self.signer,
            wecom_client=FakeWeComClient(),
            corp_id="ww-test",
            agent_id=1001,
            staff_user_ids=("clerk-1",),
            app_base_url="https://api.example.test",
            catalog_store=catalog,
            session_signer=session_signer,
            password_verifier=password_verifier,
        )

        redirect = app.get("/staff/inventory", {})
        wecom_redirect = app.get(
            "/staff/inventory",
            {},
            {"User-Agent": "wxwork/4.1"},
        )
        page = app.get("/staff/inventory", {"code": "valid-code"})

        self.assertEqual(redirect.status, 302)
        self.assertEqual(
            redirect.headers[0][1],
            "/staff/login?next=%2Fstaff%2Finventory",
        )
        self.assertEqual(wecom_redirect.status, 302)
        self.assertIn(
            "redirect_uri=https%3A%2F%2Fapi.example.test%2Fstaff%2Finventory",
            wecom_redirect.headers[0][1],
        )
        self.assertEqual(page.status, 200)
        self.assertIn("ftd_staff_session=", page.headers[0][1])
        self.assertIn("库存总览".encode(), page.body)
        self.assertIn("当前账号：clerk-1".encode(), page.body)
        self.assertIn("实物件".encode(), page.body)

        login_page = app.get(
            "/staff/login",
            {"next": "/staff/inventory/appointments"},
        )
        login_token = login_page.headers[0][1].split(";", 1)[0].split("=", 1)[1]
        web_login = app.post(
            "/staff/login",
            {},
            {
                "next": "/staff/inventory/appointments",
                "login_token": login_token,
                "username": "owner",
                "password": "correct-horse-battery-staple",
            },
            {"Cookie": f"ftd_login_csrf={login_token}"},
        )
        self.assertEqual(web_login.status, 303)
        self.assertEqual(web_login.headers[0][1], "/staff/inventory/appointments")
        web_cookie = web_login.headers[1][1].split(";", 1)[0]
        self.assertEqual(
            app.get("/staff/inventory", {}, {"Cookie": web_cookie}).status,
            200,
        )

        cookie = page.headers[0][1].split(";", 1)[0]
        connection = sqlite3.connect(catalog_path)
        try:
            connection.execute(
                """
                CREATE TABLE appointments (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, status TEXT NOT NULL,
                    customer_name TEXT NOT NULL DEFAULT '', customer_phone TEXT NOT NULL DEFAULT '',
                    requested_time_text TEXT NOT NULL DEFAULT '', confirmed_time_text TEXT NOT NULL DEFAULT '',
                    proposed_reschedule_text TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO appointments (
                    id, conversation_id, status, customer_name, customer_phone,
                    requested_time_text, confirmed_time_text, proposed_reschedule_text,
                    created_at, updated_at
                ) VALUES ('appointment-1', 'conversation-1', 'confirmed', '小许',
                          '17300000000', '2026-08-06 15:00', '2026-08-06 15:00', '',
                          '2026-08-01', '2026-08-01')
                """
            )
            connection.commit()
        finally:
            connection.close()
        schedule = app.get(
            "/staff/inventory/flow",
            {},
            {"Cookie": cookie},
        )
        self.assertEqual(schedule.status, 200)
        self.assertIn("实物流转".encode(), schedule.body)
        self.assertIn("请先在款式页录入实物件".encode(), schedule.body)

        calendar = app.get(
            "/staff/inventory/appointments",
            {"date": "2026-08-06"},
            {"Cookie": cookie},
        )
        self.assertEqual(calendar.status, 200)
        self.assertIn("试纱预约".encode(), calendar.body)
        self.assertIn("小许".encode(), calendar.body)
        self.assertIn("17300000000".encode(), calendar.body)
        self.assertIn("新增预约".encode(), calendar.body)
        self.assertIn(b"view=week", calendar.body)
        self.assertIn(b'href="/staff/inventory/flow"', calendar.body)
        week_calendar = app.get(
            "/staff/inventory/appointments",
            {"date": "2026-08-06", "view": "week"},
            {"Cookie": cookie},
        )
        self.assertEqual(week_calendar.status, 200)
        self.assertIn("2026年08月03日 至 08月09日".encode(), week_calendar.body)
        self.assertIn(b"calendar-week", week_calendar.body)

        detail = app.get(
            "/staff/inventory/appointments/appointment-1",
            {},
            {"Cookie": cookie},
        )
        self.assertEqual(detail.status, 200)
        self.assertIn("心仪款式与在店状态".encode(), detail.body)
        token = cookie.split("=", 1)[1]
        manual = app.post(
            "/staff/inventory/appointments",
            {},
            {
                "csrf": session_signer.csrf_token(token),
                "customer_name": "小陈",
                "customer_phone": "13800000000",
                "appointment_date": "2026-08-10",
                "appointment_time": "14:30",
                "status": "time_pending_staff",
            },
            {"Cookie": cookie},
        )
        self.assertEqual(manual.status, 303)
        self.assertEqual(
            manual.headers[0][1],
            "/staff/inventory/appointments?date=2026-08-10&saved=1",
        )
        updated_calendar = app.get(
            "/staff/inventory/appointments",
            {"date": "2026-08-10"},
            {"Cookie": cookie},
        )
        self.assertIn("小陈".encode(), updated_calendar.body)
        self.assertIn("13800000000".encode(), updated_calendar.body)

        saved_styles = app.post(
            "/staff/inventory/appointments/appointment-1/styles",
            {},
            {"csrf": session_signer.csrf_token(token)},
            {"Cookie": cookie},
        )
        self.assertEqual(saved_styles.status, 303)
        self.assertIn("appointment-1?saved=1", saved_styles.headers[0][1])

        legacy_calendar = app.get(
            "/staff/inventory/schedule",
            {"date": "2026-08-06"},
            {"Cookie": cookie},
        )
        legacy_flow = app.get(
            "/staff/inventory/schedule",
            {"view": "inventory"},
            {"Cookie": cookie},
        )
        legacy_detail = app.get(
            "/staff/inventory/schedule/appointments/appointment-1",
            {},
            {"Cookie": cookie},
        )
        self.assertEqual(legacy_calendar.status, 200)
        self.assertEqual(legacy_flow.status, 200)
        self.assertEqual(legacy_detail.status, 200)

        analytics = app.get(
            "/staff/inventory/analytics",
            {"days": "90"},
            {"Cookie": cookie},
        )
        self.assertEqual(analytics.status, 200)
        self.assertIn("数据分析".encode(), analytics.body)
        self.assertIn("数据不足".encode(), analytics.body)

        planning = app.get(
            "/staff/inventory/planning",
            {"days": "90"},
            {"Cookie": cookie},
        )
        self.assertEqual(planning.status, 200)
        self.assertIn("库存规划".encode(), planning.body)
        self.assertIn("暂不决策".encode(), planning.body)
        self.assertIn("至少需要 30 天".encode(), planning.body)


if __name__ == "__main__":
    unittest.main()
