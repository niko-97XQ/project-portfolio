from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from urllib.parse import urlencode


@dataclass(frozen=True, slots=True)
class StaffActionGrant:
    task_id: str
    expires_at: int


class StaffActionSigner:
    def __init__(
        self,
        secret: str,
        *,
        ttl_seconds: int = 7 * 24 * 60 * 60,
        clock=time.time,
    ) -> None:
        if len(secret) < 32:
            raise ValueError("staff action signing key must contain at least 32 characters")
        if ttl_seconds <= 0:
            raise ValueError("staff action link TTL must be positive")
        self.secret = secret.encode("utf-8")
        self.ttl_seconds = ttl_seconds
        self.clock = clock

    def issue_url(self, base_url: str, task_id: str) -> str:
        if not task_id.strip():
            raise ValueError("staff task id is required")
        expires_at = int(self.clock()) + self.ttl_seconds
        token = self._signature(task_id, expires_at)
        query = urlencode({"expires": expires_at, "token": token})
        return f"{base_url.rstrip('/')}/staff/tasks/{task_id}?{query}"

    def verify(self, task_id: str, expires: str, token: str) -> StaffActionGrant:
        try:
            expires_at = int(expires)
        except (TypeError, ValueError) as error:
            raise ValueError("staff action expiry is invalid") from error
        if expires_at < int(self.clock()):
            raise ValueError("staff action link has expired")
        expected = self._signature(task_id, expires_at)
        if not hmac.compare_digest(expected, str(token or "")):
            raise ValueError("staff action signature is invalid")
        return StaffActionGrant(task_id=task_id, expires_at=expires_at)

    def _signature(self, task_id: str, expires_at: int) -> str:
        payload = f"{task_id}.{expires_at}".encode("utf-8")
        return hmac.new(self.secret, payload, hashlib.sha256).hexdigest()


class StaffSessionSigner:
    def __init__(
        self,
        secret: str,
        *,
        ttl_seconds: int = 12 * 60 * 60,
        clock=time.time,
    ) -> None:
        if len(secret) < 32:
            raise ValueError("staff session signing key must contain at least 32 characters")
        self.secret = secret.encode("utf-8")
        self.ttl_seconds = ttl_seconds
        self.clock = clock

    def issue(self, staff_id: str) -> str:
        staff_id = staff_id.strip()
        if not staff_id:
            raise ValueError("staff id is required")
        encoded_id = urlsafe_b64encode(staff_id.encode("utf-8")).decode("ascii").rstrip("=")
        expires_at = int(self.clock()) + self.ttl_seconds
        payload = f"{encoded_id}.{expires_at}"
        return f"{payload}.{self._signature(payload)}"

    def verify(self, token: str) -> str:
        try:
            encoded_id, raw_expiry, signature = token.split(".", 2)
            expires_at = int(raw_expiry)
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError("staff session is invalid") from error
        payload = f"{encoded_id}.{expires_at}"
        if expires_at < int(self.clock()) or not hmac.compare_digest(
            self._signature(payload), signature
        ):
            raise ValueError("staff session is invalid")
        try:
            padding = "=" * (-len(encoded_id) % 4)
            return urlsafe_b64decode(encoded_id + padding).decode("utf-8")
        except (UnicodeError, ValueError) as error:
            raise ValueError("staff session is invalid") from error

    def csrf_token(self, session_token: str) -> str:
        return hmac.new(
            self.secret,
            f"csrf.{session_token}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def verify_csrf(self, session_token: str, csrf_token: str) -> None:
        expected = self.csrf_token(session_token)
        if not hmac.compare_digest(expected, str(csrf_token or "")):
            raise ValueError("invalid form token")

    def _signature(self, payload: str) -> str:
        return hmac.new(self.secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()


class StaffPasswordVerifier:
    ALGORITHM = "pbkdf2_sha256"

    def __init__(self, records: tuple[str, ...]) -> None:
        parsed: dict[str, tuple[int, bytes, bytes]] = {}
        for record in records:
            try:
                username, algorithm, raw_iterations, raw_salt, raw_digest = record.split("$", 4)
                iterations = int(raw_iterations)
                salt = bytes.fromhex(raw_salt)
                digest = bytes.fromhex(raw_digest)
            except (TypeError, ValueError) as error:
                raise ValueError("staff web account record is invalid") from error
            username = username.strip().lower()
            if (
                algorithm != self.ALGORITHM
                or not re.fullmatch(r"[a-z0-9._-]{3,40}", username)
                or iterations < 200_000
                or len(salt) < 16
                or len(digest) != 32
                or username in parsed
            ):
                raise ValueError("staff web account record is invalid")
            parsed[username] = (iterations, salt, digest)
        self._records = parsed

    @property
    def usernames(self) -> frozenset[str]:
        return frozenset(self._records)

    def verify(self, username: str, password: str) -> str | None:
        username = username.strip().lower()
        record = self._records.get(username)
        if record is None:
            # Keep unknown-user checks computationally similar to real accounts.
            hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), b"0" * 16, 200_000)
            return None
        iterations, salt, expected = record
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            iterations,
        )
        return username if hmac.compare_digest(actual, expected) else None

    @classmethod
    def create_record(
        cls,
        username: str,
        password: str,
        *,
        iterations: int = 240_000,
        salt: bytes | None = None,
    ) -> str:
        username = username.strip().lower()
        if not re.fullmatch(r"[a-z0-9._-]{3,40}", username):
            raise ValueError("staff web username is invalid")
        if len(password) < 14:
            raise ValueError("staff web password must contain at least 14 characters")
        salt = salt or secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            iterations,
        )
        return f"{username}${cls.ALGORITHM}${iterations}${salt.hex()}${digest.hex()}"
