from __future__ import annotations

import http.client
import sys
import threading
import unittest
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from forthatday_agent.infrastructure.wecom_http import (  # noqa: E402
    MAX_CALLBACK_BODY_BYTES,
    create_wecom_callback_server,
)


class FakeReceiver:
    def __init__(self) -> None:
        self.received: list[tuple[dict[str, str], bytes]] = []

    def verify_url(self, query: dict[str, str]) -> str:
        if query.get("msg_signature") != "valid":
            raise ValueError("bad signature")
        return "verified-echo"

    def receive(self, query: dict[str, str], body: bytes) -> bool:
        if query.get("msg_signature") != "valid":
            raise ValueError("bad signature")
        self.received.append((query, body))
        return True


class BrokenReceiver(FakeReceiver):
    def receive(self, query: dict[str, str], body: bytes) -> bool:
        raise RuntimeError("database password must not leak")


class WeComHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.receiver = FakeReceiver()
        self.server = create_wecom_callback_server("127.0.0.1", 0, self.receiver)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(
        self,
        method: str,
        target: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes]:
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_address[1], timeout=2
        )
        connection.request(method, target, body=body, headers=headers or {})
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    def test_health_check(self) -> None:
        self.assertEqual(self.request("GET", "/healthz"), (200, b"ok"))

    def test_verification_returns_decrypted_echo(self) -> None:
        status, body = self.request(
            "GET",
            "/wecom/callback?msg_signature=valid&timestamp=1&nonce=n&echostr=e",
        )
        self.assertEqual((status, body), (200, b"verified-echo"))

    def test_callback_is_accepted_and_forwarded(self) -> None:
        payload = b"<xml>encrypted</xml>"
        status, body = self.request(
            "POST",
            "/wecom/callback?msg_signature=valid&timestamp=1&nonce=n",
            payload,
            {"Content-Type": "application/xml"},
        )
        self.assertEqual((status, body), (200, b"success"))
        self.assertEqual(self.receiver.received[0][1], payload)

    def test_invalid_signature_gets_generic_error(self) -> None:
        status, body = self.request(
            "GET",
            "/wecom/callback?msg_signature=secret&timestamp=1&nonce=n&echostr=e",
        )
        self.assertEqual((status, body), (400, b"invalid callback"))
        self.assertNotIn(b"secret", body)

    def test_oversized_callback_is_rejected_before_read(self) -> None:
        status, _ = self.request(
            "POST",
            "/wecom/callback?msg_signature=valid&timestamp=1&nonce=n",
            b"",
            {"Content-Length": str(MAX_CALLBACK_BODY_BYTES + 1)},
        )
        self.assertEqual(status, 413)
        self.assertEqual(self.receiver.received, [])

    def test_internal_failure_returns_retryable_generic_error(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.server = create_wecom_callback_server("127.0.0.1", 0, BrokenReceiver())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

        status, body = self.request(
            "POST",
            "/wecom/callback?msg_signature=valid&timestamp=1&nonce=n",
            b"<xml />",
        )

        self.assertEqual((status, body), (500, b"callback unavailable"))
        self.assertNotIn(b"password", body)


if __name__ == "__main__":
    unittest.main()
