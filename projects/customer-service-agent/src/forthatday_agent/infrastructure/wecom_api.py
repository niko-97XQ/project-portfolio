from __future__ import annotations

import hashlib
import json
import mimetypes
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from forthatday_agent.domain.messages import OutboxMessage
from forthatday_agent.application.staff_access import StaffActionSigner


class WeComApiError(RuntimeError):
    def __init__(self, operation: str, errcode: int, errmsg: str) -> None:
        super().__init__(f"WeCom {operation} failed ({errcode}): {errmsg}"[:500])
        self.errcode = errcode


class WeComSessionStateTransitionUnavailable(RuntimeError):
    """The native WeCom session is confirmed ended, awaiting a new customer message."""


class WeComTransport(Protocol):
    def request_json(
        self,
        method: str,
        path: str,
        query: dict[str, str],
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def download(self, path: str, query: dict[str, str]) -> bytes: ...

    def upload(
        self,
        path: str,
        query: dict[str, str],
        file_path: Path,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class SyncedMessages:
    messages: tuple[dict[str, Any], ...]
    next_cursor: str
    has_more: bool


class WeComApiClient:
    def __init__(
        self,
        corp_id: str,
        kf_secret: str,
        transport: WeComTransport | None = None,
        *,
        clock: Any = time.time,
    ) -> None:
        if not corp_id or not kf_secret:
            raise ValueError("WeCom corp id and customer-service secret are required")
        self.corp_id = corp_id
        self.kf_secret = kf_secret
        self.transport = transport or UrllibWeComTransport()
        self.clock = clock
        self._token = ""
        self._token_expires_at = 0.0
        self._token_lock = threading.Lock()

    def sync_messages(
        self,
        callback_token: str,
        open_kfid: str,
        cursor: str = "",
        *,
        limit: int = 1000,
    ) -> SyncedMessages:
        if not callback_token or not open_kfid:
            raise ValueError("callback token and open_kfid are required")
        data = self._authorized_json(
            "POST",
            "/cgi-bin/kf/sync_msg",
            {
                "cursor": cursor,
                "token": callback_token,
                "limit": limit,
                "voice_format": 0,
                "open_kfid": open_kfid,
            },
            "sync messages",
        )
        messages = data.get("msg_list") or []
        if not isinstance(messages, list):
            raise WeComApiError("sync messages", -1, "msg_list is not an array")
        return SyncedMessages(
            messages=tuple(item for item in messages if isinstance(item, dict)),
            next_cursor=str(data.get("next_cursor") or cursor),
            has_more=bool(data.get("has_more")),
        )

    def download_media(self, media_id: str) -> bytes:
        if not media_id:
            raise ValueError("media id is required")
        return self.transport.download(
            "/cgi-bin/media/get",
            {"access_token": self.access_token(), "media_id": media_id},
        )

    def send_text(
        self,
        external_userid: str,
        open_kfid: str,
        content: str,
        idempotency_key: str,
    ) -> str:
        data = self._authorized_json(
            "POST",
            "/cgi-bin/kf/send_msg",
            {
                "touser": external_userid,
                "open_kfid": open_kfid,
                "msgid": _wecom_msgid(idempotency_key),
                "msgtype": "text",
                "text": {"content": content},
            },
            "send text",
        )
        return str(data.get("msgid") or _wecom_msgid(idempotency_key))

    def send_image(
        self,
        external_userid: str,
        open_kfid: str,
        image_path: Path,
        idempotency_key: str,
    ) -> str:
        if not image_path.is_file():
            raise ValueError("outbound image does not exist")
        upload = self.transport.upload(
            "/cgi-bin/media/upload",
            {"access_token": self.access_token(), "type": "image"},
            image_path,
        )
        _raise_for_wecom_error(upload, "upload image")
        media_id = str(upload.get("media_id") or "")
        if not media_id:
            raise WeComApiError("upload image", -1, "media_id is missing")
        data = self._authorized_json(
            "POST",
            "/cgi-bin/kf/send_msg",
            {
                "touser": external_userid,
                "open_kfid": open_kfid,
                "msgid": _wecom_msgid(idempotency_key),
                "msgtype": "image",
                "image": {"media_id": media_id},
            },
            "send image",
        )
        return str(data.get("msgid") or _wecom_msgid(idempotency_key))

    def get_service_state(self, external_userid: str, open_kfid: str) -> int:
        if not external_userid or not open_kfid:
            raise ValueError("external user id and customer-service account id are required")
        data = self._authorized_json(
            "POST",
            "/cgi-bin/kf/service_state/get",
            {"open_kfid": open_kfid, "external_userid": external_userid},
            "get customer-service state",
        )
        state = data.get("service_state")
        if type(state) is not int or state not in {0, 1, 2, 3, 4}:
            raise WeComApiError("get customer-service state", -1, "invalid service_state")
        return state

    def change_service_state(
        self,
        external_userid: str,
        open_kfid: str,
        service_state: int,
        *,
        servicer_userid: str = "",
    ) -> None:
        if not external_userid or not open_kfid:
            raise ValueError("external user id and customer-service account id are required")
        if service_state not in {1, 2, 3, 4}:
            raise ValueError("unsupported WeCom customer-service state")
        body: dict[str, Any] = {
            "open_kfid": open_kfid,
            "external_userid": external_userid,
            "service_state": service_state,
        }
        if service_state == 3:
            if not servicer_userid:
                raise ValueError("a servicer user id is required for human takeover")
            body["servicer_userid"] = servicer_userid
        self._authorized_json(
            "POST",
            "/cgi-bin/kf/service_state/trans",
            body,
            "change customer-service state",
        )

    def staff_user_id_from_oauth_code(self, code: str) -> str:
        if not code.strip():
            raise ValueError("OAuth code is required")
        data = self.transport.request_json(
            "GET",
            "/cgi-bin/auth/getuserinfo",
            {"access_token": self.access_token(), "code": code.strip()},
        )
        _raise_for_wecom_error(data, "get staff identity")
        user_id = str(data.get("UserId") or data.get("userid") or "").strip()
        if not user_id:
            raise WeComApiError("get staff identity", -1, "staff user id is missing")
        return user_id

    def send_application_textcard(
        self,
        user_ids: tuple[str, ...],
        agent_id: int,
        title: str,
        description: str,
        url: str,
    ) -> str:
        if not user_ids:
            raise ValueError("at least one staff user id is required")
        if agent_id <= 0:
            raise ValueError("WeCom agent id must be positive")
        data = self._authorized_json(
            "POST",
            "/cgi-bin/message/send",
            {
                "touser": "|".join(user_ids),
                "msgtype": "textcard",
                "agentid": agent_id,
                "textcard": {
                    "title": title,
                    "description": description,
                    "url": url,
                    "btntxt": "去确认",
                },
                "safe": 0,
            },
            "send staff textcard",
        )
        invalid_users = str(data.get("invaliduser") or "").strip()
        if invalid_users:
            raise WeComApiError("send staff textcard", -1, "one or more staff recipients are invalid")
        return str(data.get("msgid") or "staff-textcard-sent")

    def access_token(self) -> str:
        now = float(self.clock())
        if self._token and now < self._token_expires_at:
            return self._token
        with self._token_lock:
            now = float(self.clock())
            if self._token and now < self._token_expires_at:
                return self._token
            data = self.transport.request_json(
                "GET",
                "/cgi-bin/gettoken",
                {"corpid": self.corp_id, "corpsecret": self.kf_secret},
            )
            _raise_for_wecom_error(data, "get access token")
            token = str(data.get("access_token") or "")
            if not token:
                raise WeComApiError("get access token", -1, "access_token is missing")
            expires_in = max(60, int(data.get("expires_in") or 7200))
            self._token = token
            self._token_expires_at = now + max(30, expires_in - 300)
            return token

    def _authorized_json(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        operation: str,
    ) -> dict[str, Any]:
        data = self.transport.request_json(
            method,
            path,
            {"access_token": self.access_token()},
            body,
        )
        _raise_for_wecom_error(data, operation)
        return data


class ChannelAddressStore(Protocol):
    def channel_address(self, conversation_id: str) -> tuple[str, str, str]: ...


class CatalogImageResolver(Protocol):
    def catalog_image_path(self, model_code: str, position: int = 0) -> Path: ...


class WeComNativeHandoffGateway:
    """Moves an API-managed WeCom session into and out of native staff handling."""

    def __init__(
        self,
        client: WeComApiClient,
        address_store: ChannelAddressStore,
        default_servicer_user_id: str,
    ) -> None:
        if not default_servicer_user_id.strip():
            raise ValueError("a default WeCom servicer user id is required")
        self.client = client
        self.address_store = address_store
        self.default_servicer_user_id = default_servicer_user_id.strip()

    def assign_to_staff(self, conversation_id: str) -> str:
        channel, open_kfid, external_userid = self.address_store.channel_address(conversation_id)
        if channel != "wecom_kf":
            raise ValueError(f"unsupported handoff channel: {channel}")
        self.client.change_service_state(
            external_userid,
            open_kfid,
            3,
            servicer_userid=self.default_servicer_user_id,
        )
        return self.default_servicer_user_id

    def return_to_ai(self, conversation_id: str) -> None:
        channel, open_kfid, external_userid = self.address_store.channel_address(conversation_id)
        if channel != "wecom_kf":
            raise ValueError(f"unsupported handoff channel: {channel}")
        try:
            self.client.change_service_state(external_userid, open_kfid, 1)
        except WeComApiError as error:
            if error.errcode != 95016:
                raise
            # 95016 rejects a transition; it does not prove the human session ended.
            state = self.client.get_service_state(external_userid, open_kfid)
            if state == 1:
                return
            if state == 3:
                self.client.change_service_state(external_userid, open_kfid, 4)
                state = self.client.get_service_state(external_userid, open_kfid)
            if state == 4:
                raise WeComSessionStateTransitionUnavailable(
                    "企业微信人工会话已结束，将从顾客下一条新消息恢复 AI 接待"
                ) from error
            raise


class WeComCustomerServiceGateway:
    def __init__(
        self,
        client: WeComApiClient,
        address_store: ChannelAddressStore,
        *,
        staff_agent_id: int = 0,
        staff_user_ids: tuple[str, ...] = (),
        staff_base_url: str = "",
        staff_signer: StaffActionSigner | None = None,
        catalog_images: CatalogImageResolver | None = None,
    ) -> None:
        self.client = client
        self.address_store = address_store
        self.staff_agent_id = staff_agent_id
        self.staff_user_ids = staff_user_ids
        self.staff_base_url = staff_base_url
        self.staff_signer = staff_signer
        self.catalog_images = catalog_images

    def send(self, message: OutboxMessage) -> str:
        if message.message_type == "staff_textcard":
            if self.staff_signer is None or not self.staff_base_url:
                raise ValueError("staff action link is not configured")
            task_id = str(message.payload.get("task_id") or "")
            url = self.staff_signer.issue_url(self.staff_base_url, task_id)
            return self.client.send_application_textcard(
                self.staff_user_ids,
                self.staff_agent_id,
                str(message.payload.get("title") or "待处理事项"),
                str(message.payload.get("description") or "请打开查看详情。"),
                url,
            )
        channel, open_kfid, external_userid = self.address_store.channel_address(
            message.conversation_id
        )
        if channel != "wecom_kf":
            raise ValueError(f"unsupported delivery channel: {channel}")
        if message.message_type == "text":
            return self.client.send_text(
                external_userid,
                open_kfid,
                str(message.payload.get("text") or ""),
                message.idempotency_key,
            )
        if message.message_type == "image":
            image_path = str(message.payload.get("path") or "").strip()
            if image_path:
                resolved_path = Path(image_path)
            else:
                catalog_ref = str(message.payload.get("catalog_ref") or "").strip().lower()
                if not catalog_ref or self.catalog_images is None:
                    raise ValueError("image path or catalog reference is required")
                position = int(message.payload.get("position") or 0)
                resolved_path = self.catalog_images.catalog_image_path(catalog_ref, position)
            return self.client.send_image(
                external_userid,
                open_kfid,
                resolved_path,
                message.idempotency_key,
            )
        raise ValueError(f"unsupported WeCom message type: {message.message_type}")


class UrllibWeComTransport:
    base_url = "https://qyapi.weixin.qq.com"

    def request_json(
        self,
        method: str,
        path: str,
        query: dict[str, str],
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = self._url(path, query)
        payload = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=payload,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise ConnectionError(f"WeCom HTTP request failed: {type(error).__name__}") from error

    def download(self, path: str, query: dict[str, str]) -> bytes:
        try:
            with urllib.request.urlopen(self._url(path, query), timeout=30) as response:
                content = response.read()
                content_type = response.headers.get("Content-Type", "")
        except (urllib.error.URLError, TimeoutError) as error:
            raise ConnectionError(f"WeCom media download failed: {type(error).__name__}") from error
        if "json" in content_type:
            data = json.loads(content.decode("utf-8"))
            _raise_for_wecom_error(data, "download media")
        return content

    def upload(
        self,
        path: str,
        query: dict[str, str],
        file_path: Path,
    ) -> dict[str, Any]:
        boundary = "----Forthatday" + hashlib.sha256(str(file_path).encode()).hexdigest()[:16]
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        file_data = file_path.read_bytes()
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="media"; filename="{file_path.name}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8") + file_data + f"\r\n--{boundary}--\r\n".encode("ascii")
        request = urllib.request.Request(
            self._url(path, query),
            data=body,
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise ConnectionError(f"WeCom media upload failed: {type(error).__name__}") from error

    def _url(self, path: str, query: dict[str, str]) -> str:
        return self.base_url + path + "?" + urllib.parse.urlencode(query)


def _raise_for_wecom_error(data: dict[str, Any], operation: str) -> None:
    errcode = int(data.get("errcode") or 0)
    if errcode:
        raise WeComApiError(operation, errcode, str(data.get("errmsg") or "unknown error"))


def _wecom_msgid(idempotency_key: str) -> str:
    # WeCom limits client-provided message IDs to 32 characters total.
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:28]
    return f"ftd_{digest}"
