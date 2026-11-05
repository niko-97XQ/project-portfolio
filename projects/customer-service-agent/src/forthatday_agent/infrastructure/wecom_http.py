from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Protocol
from urllib.parse import parse_qs, urlsplit


MAX_CALLBACK_BODY_BYTES = 1024 * 1024
MAX_STAFF_FORM_BYTES = 16 * 1024


class CallbackReceiver(Protocol):
    def verify_url(self, query: dict[str, str]) -> str: ...

    def receive(self, query: dict[str, str], body: bytes) -> bool: ...


class StaffWebResponse(Protocol):
    status: int
    body: bytes
    content_type: str
    headers: tuple[tuple[str, str], ...]


class StaffWebApp(Protocol):
    def get(
        self,
        path: str,
        query: dict[str, str],
        headers: dict[str, str] | None = None,
    ) -> StaffWebResponse | None: ...

    def post(
        self,
        path: str,
        query: dict[str, str],
        form: dict[str, str],
        headers: dict[str, str] | None = None,
    ) -> StaffWebResponse | None: ...


def create_wecom_callback_server(
    host: str,
    port: int,
    receiver: CallbackReceiver,
    staff_app: StaffWebApp | None = None,
) -> ThreadingHTTPServer:
    handler = _handler_for(receiver, staff_app)
    return ThreadingHTTPServer((host, port), handler)


def _handler_for(
    receiver: CallbackReceiver,
    staff_app: StaffWebApp | None,
) -> type[BaseHTTPRequestHandler]:
    class WeComCallbackHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            path, query = _request_target(self.path)
            if path == "/healthz":
                self._respond(200, b"ok", "text/plain; charset=utf-8")
                return
            if staff_app is not None:
                try:
                    response = staff_app.get(path, query, self._staff_headers())
                except Exception:
                    self._respond(500, b"staff page unavailable")
                    return
                if response is not None:
                    self._respond(response.status, response.body, response.content_type, response.headers)
                    return
            if path != "/wecom/callback":
                self._respond(404, b"not found")
                return
            try:
                echo = receiver.verify_url(query).encode("utf-8")
            except (ValueError, UnicodeError, SyntaxError):
                self._respond(400, b"invalid callback")
                return
            except Exception:
                self._respond(500, b"callback unavailable")
                return
            self._respond(200, echo, "text/plain; charset=utf-8")

        def do_POST(self) -> None:
            path, query = _request_target(self.path)
            if staff_app is not None and (path.startswith("/staff/") or path == "/website/appointments"):
                self._handle_staff_post(path, query)
                return
            if path != "/wecom/callback":
                self._respond(404, b"not found")
                return
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self._respond(400, b"invalid content length")
                return
            if length < 1 or length > MAX_CALLBACK_BODY_BYTES:
                self._respond(413, b"callback body rejected")
                return
            body = self.rfile.read(length)
            try:
                receiver.receive(query, body)
            except (ValueError, UnicodeError, SyntaxError):
                self._respond(400, b"invalid callback")
                return
            except Exception:
                self._respond(500, b"callback unavailable")
                return
            self._respond(200, b"success", "text/plain; charset=utf-8")

        def _handle_staff_post(self, path: str, query: dict[str, str]) -> None:
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self._respond(400, b"invalid content length")
                return
            if length < 1 or length > MAX_STAFF_FORM_BYTES:
                self._respond(413, b"staff form rejected")
                return
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
            if content_type != "application/x-www-form-urlencoded":
                self._respond(415, b"unsupported media type")
                return
            try:
                raw_form = self.rfile.read(length).decode("utf-8", errors="strict")
            except UnicodeError:
                self._respond(400, b"invalid staff form")
                return
            values = parse_qs(raw_form, keep_blank_values=True)
            form = {key: items[-1] for key, items in values.items() if items}
            try:
                response = (
                    staff_app.post(path, query, form, self._staff_headers())
                    if staff_app else None
                )
            except Exception:
                self._respond(500, b"staff action unavailable")
                return
            if response is None:
                self._respond(404, b"not found")
                return
            self._respond(response.status, response.body, response.content_type, response.headers)

        def _staff_headers(self) -> dict[str, str]:
            return {
                name: value
                for name, value in (
                    ("Cookie", self.headers.get("Cookie", "")),
                    ("User-Agent", self.headers.get("User-Agent", "")),
                    ("X-Forwarded-For", self.headers.get("X-Forwarded-For", "")),
                    ("Origin", self.headers.get("Origin", "")),
                    ("Remote-Addr", self.client_address[0]),
                )
                if value
            }

        def log_message(self, format: str, *args: object) -> None:
            # Callback query parameters contain signatures, so do not log raw URLs.
            return

        def _respond(
            self,
            status: int,
            body: bytes,
            content_type: str = "text/plain; charset=utf-8",
            headers: tuple[tuple[str, str], ...] = (),
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
                "script-src 'unsafe-inline'; "
                "form-action 'self'; base-uri 'none'",
            )
            for name, value in headers:
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

    return WeComCallbackHandler


def _request_target(target: str) -> tuple[str, dict[str, str]]:
    parsed = urlsplit(target)
    values = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=False)
    query = {key: items[-1] for key, items in values.items() if items}
    return parsed.path, query
