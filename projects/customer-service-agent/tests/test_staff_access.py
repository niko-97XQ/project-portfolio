from __future__ import annotations

import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from forthatday_agent.application.staff_access import (  # noqa: E402
    StaffActionSigner,
    StaffPasswordVerifier,
    StaffSessionSigner,
)


class StaffActionSignerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.signer = StaffActionSigner("k" * 32, ttl_seconds=60, clock=lambda: 1000)

    def test_issued_link_verifies_for_same_task(self) -> None:
        url = self.signer.issue_url("https://example.test/", "task-1")
        query = parse_qs(urlsplit(url).query)

        grant = self.signer.verify(
            "task-1",
            query["expires"][0],
            query["token"][0],
        )

        self.assertEqual(grant.task_id, "task-1")
        self.assertEqual(grant.expires_at, 1060)

    def test_token_cannot_be_reused_for_another_task(self) -> None:
        url = self.signer.issue_url("https://example.test", "task-1")
        query = parse_qs(urlsplit(url).query)

        with self.assertRaisesRegex(ValueError, "signature"):
            self.signer.verify("task-2", query["expires"][0], query["token"][0])

    def test_expired_link_is_rejected(self) -> None:
        signer = StaffActionSigner("k" * 32, ttl_seconds=60, clock=lambda: 2000)
        with self.assertRaisesRegex(ValueError, "expired"):
            signer.verify("task-1", "1999", "0" * 64)

    def test_staff_session_and_csrf_are_signed(self) -> None:
        signer = StaffSessionSigner("s" * 32, clock=lambda: 1000)
        token = signer.issue("clerk-1")
        csrf = signer.csrf_token(token)

        self.assertEqual(signer.verify(token), "clerk-1")
        signer.verify_csrf(token, csrf)
        with self.assertRaises(ValueError):
            signer.verify_csrf(token, "tampered")

    def test_staff_password_records_are_salted_and_verified(self) -> None:
        record = StaffPasswordVerifier.create_record(
            "owner",
            "correct-horse-battery-staple",
            salt=b"1" * 16,
        )
        verifier = StaffPasswordVerifier((record,))

        self.assertEqual(
            verifier.verify("OWNER", "correct-horse-battery-staple"),
            "owner",
        )
        self.assertIsNone(verifier.verify("owner", "wrong-password"))
        self.assertIsNone(verifier.verify("unknown", "wrong-password"))
        self.assertNotIn("correct-horse-battery-staple", record)


if __name__ == "__main__":
    unittest.main()
