from __future__ import annotations

import base64
import hashlib
import struct
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # noqa: E402
from service_agent.infrastructure.wecom_callback_crypto import (  # noqa: E402
    WeComCallbackCrypto,
)
from service_agent.application.wecom_callback import (  # noqa: E402
    WeComCallbackReceiver,
    WeComCallbackWorker,
)
from service_agent.infrastructure.sqlite_message_store import (  # noqa: E402
    SqliteMessageStore,
)


TOKEN = "callback-token"
RECEIVE_ID = "ww-corp-id"
AES_KEY = bytes(range(32))
ENCODING_AES_KEY = base64.b64encode(AES_KEY).decode("ascii").rstrip("=")


def encrypt(message: bytes) -> str:
    plaintext = b"0123456789abcdef" + struct.pack("!I", len(message)) + message + RECEIVE_ID.encode()
    padding = 32 - len(plaintext) % 32
    plaintext += bytes([padding]) * padding
    encryptor = Cipher(algorithms.AES(AES_KEY), modes.CBC(AES_KEY[:16])).encryptor()
    return base64.b64encode(encryptor.update(plaintext) + encryptor.finalize()).decode()


def signature(encrypted: str, timestamp: str, nonce: str) -> str:
    return hashlib.sha1(
        "".join(sorted((TOKEN, timestamp, nonce, encrypted))).encode("utf-8")
    ).hexdigest()


class WeComCallbackCryptoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.crypto = WeComCallbackCrypto(TOKEN, ENCODING_AES_KEY, RECEIVE_ID)

    def test_url_verification_is_verified_and_decrypted(self) -> None:
        encrypted = encrypt(b"verification-ok")

        result = self.crypto.decrypt_url_verification(
            signature(encrypted, "100", "nonce-1"),
            "100",
            "nonce-1",
            encrypted,
        )

        self.assertEqual(result, "verification-ok")

    def test_customer_service_callback_extracts_pull_token_and_account(self) -> None:
        event_xml = (
            b"<xml><ToUserName>ww-corp-id</ToUserName>"
            b"<MsgType>event</MsgType><Event>kf_msg_or_event</Event>"
            b"<Token>pull-token-1</Token><OpenKfId>wk-1</OpenKfId></xml>"
        )
        encrypted = encrypt(event_xml)
        wrapper = f"<xml><Encrypt><![CDATA[{encrypted}]]></Encrypt></xml>".encode()

        event = self.crypto.decrypt_callback(
            signature(encrypted, "101", "nonce-2"),
            "101",
            "nonce-2",
            wrapper,
        )

        self.assertEqual(event.event_type, "kf_msg_or_event")
        self.assertEqual(event.callback_token, "pull-token-1")
        self.assertEqual(event.open_kfid, "wk-1")

    def test_tampered_signature_is_rejected_before_decryption(self) -> None:
        encrypted = encrypt(b"verification-ok")

        with self.assertRaisesRegex(ValueError, "signature"):
            self.crypto.decrypt_url_verification(
                "0" * 40,
                "100",
                "nonce-1",
                encrypted,
            )


class FakeSyncService:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    def sync(self, callback_token: str, open_kfid: str) -> int:
        self.calls.append((callback_token, open_kfid))
        if self.fail:
            raise ConnectionError("temporary sync failure")
        return 1


class WeComCallbackQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SqliteMessageStore(Path(self.temp_dir.name) / "callbacks.db")
        self.crypto = WeComCallbackCrypto(TOKEN, ENCODING_AES_KEY, RECEIVE_ID)
        self.receiver = WeComCallbackReceiver(self.crypto, self.store)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def callback_request(self):
        event_xml = (
            b"<xml><ToUserName>ww-corp-id</ToUserName>"
            b"<MsgType>event</MsgType><Event>kf_msg_or_event</Event>"
            b"<Token>pull-token-1</Token><OpenKfId>wk-1</OpenKfId></xml>"
        )
        encrypted = encrypt(event_xml)
        query = {
            "msg_signature": signature(encrypted, "200", "nonce-queue"),
            "timestamp": "200",
            "nonce": "nonce-queue",
        }
        body = f"<xml><Encrypt><![CDATA[{encrypted}]]></Encrypt></xml>".encode()
        return query, body

    def test_duplicate_callback_creates_one_durable_job(self) -> None:
        query, body = self.callback_request()

        self.assertTrue(self.receiver.receive(query, body))
        self.assertFalse(self.receiver.receive(query, body))

        self.assertEqual(self.store.callback_job_count("pending"), 1)

    def test_worker_syncs_and_completes_callback_job(self) -> None:
        query, body = self.callback_request()
        self.receiver.receive(query, body)
        sync = FakeSyncService()
        worker = WeComCallbackWorker(self.store, sync)

        self.assertTrue(
            worker.process_once(
                "callback-worker",
                datetime.now(timezone.utc) + timedelta(minutes=1),
            )
        )

        self.assertEqual(sync.calls, [("pull-token-1", "wk-1")])
        self.assertEqual(self.store.callback_job_count("done"), 1)

    def test_sync_failure_is_retried_without_losing_callback(self) -> None:
        query, body = self.callback_request()
        self.receiver.receive(query, body)
        worker = WeComCallbackWorker(self.store, FakeSyncService(fail=True))

        worker.process_once(
            "callback-worker",
            datetime.now(timezone.utc) + timedelta(minutes=1),
        )

        self.assertEqual(self.store.callback_job_count("retry"), 1)

    def test_stale_processing_job_is_recovered_after_worker_crash(self) -> None:
        query, body = self.callback_request()
        self.receiver.receive(query, body)
        claimed_at = datetime.now(timezone.utc) + timedelta(minutes=1)
        first = self.store.claim_wecom_callback("crashed-worker", claimed_at)

        recovered = self.store.claim_wecom_callback(
            "replacement-worker",
            claimed_at + timedelta(minutes=6),
        )

        self.assertIsNotNone(first)
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.id, first.id)
        self.assertEqual(recovered.attempt_count, 2)

    def test_wrong_receive_id_is_rejected(self) -> None:
        crypto = WeComCallbackCrypto(TOKEN, ENCODING_AES_KEY, "different-corp")
        encrypted = encrypt(b"verification-ok")

        with self.assertRaisesRegex(ValueError, "receive id"):
            crypto.decrypt_url_verification(
                signature(encrypted, "100", "nonce-1"),
                "100",
                "nonce-1",
                encrypted,
            )


if __name__ == "__main__":
    unittest.main()
