from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html import escape
from http.cookies import SimpleCookie
import hmac
import json
import mimetypes
from threading import Lock
import time
from urllib.parse import quote, unquote, urlencode

from forthatday_agent.application.staff_access import (
    StaffActionSigner,
    StaffPasswordVerifier,
    StaffSessionSigner,
)
from forthatday_agent.application.staff_appointments import StaffAppointmentService
from forthatday_agent.application.inventory_web import (
    render_inventory_dashboard,
    render_inventory_analytics,
    render_appointment_calendar,
    render_appointment_detail,
    render_inventory_planning,
    render_inventory_product_detail,
    render_inventory_products,
    render_inventory_schedule,
)
from forthatday_agent.infrastructure.catalog_store import CatalogStore
from forthatday_agent.infrastructure.wecom_api import WeComApiClient
from forthatday_agent.infrastructure.website_request_store import WebsiteRequestStore, IntakeError
from forthatday_agent.application.website_requests import public_intake, render_requests


@dataclass(frozen=True, slots=True)
class StaffWebResponse:
    status: int
    body: bytes
    content_type: str = "text/html; charset=utf-8"
    headers: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class _InventoryAuth:
    staff_id: str
    session_token: str
    response_headers: tuple[tuple[str, str], ...] = ()


STAFF_SESSION_COOKIE = "ftd_staff_session"
STAFF_LOGIN_CSRF_COOKIE = "ftd_login_csrf"


class StaffTaskWebApp:
    def __init__(
        self,
        service: StaffAppointmentService,
        signer: StaffActionSigner,
        *,
        wecom_client: WeComApiClient | None = None,
        corp_id: str = "",
        agent_id: int = 0,
        staff_user_ids: tuple[str, ...] = (),
        app_base_url: str = "",
        catalog_store: CatalogStore | None = None,
        session_signer: StaffSessionSigner | None = None,
        password_verifier: StaffPasswordVerifier | None = None,
    ) -> None:
        self.service = service
        self.signer = signer
        self.wecom_client = wecom_client
        self.corp_id = corp_id
        self.agent_id = agent_id
        self.staff_user_ids = frozenset(staff_user_ids)
        self.app_base_url = app_base_url.rstrip("/")
        self.catalog_store = catalog_store
        self.session_signer = session_signer
        self.password_verifier = password_verifier
        self.website_store = WebsiteRequestStore(catalog_store.database_path) if catalog_store else None
        self._login_failures: dict[str, list[float]] = {}
        self._login_lock = Lock()

    def get(
        self,
        path: str,
        query: dict[str, str],
        headers: dict[str, str] | None = None,
    ) -> StaffWebResponse | None:
        if path == "/staff/inventory/website":
            return self._website_requests(path, query, headers or {})
        if path == "/staff/login":
            return self._staff_login_page(query, headers or {})
        if path == "/staff/login/wecom":
            return self._staff_wecom_redirect(query)
        if path == "/staff/logout":
            return self._staff_logout()
        if path == "/staff/context":
            return self._native_context(query)
        if path == "/staff/inventory":
            return self._inventory_dashboard(path, query, headers or {})
        if path == "/staff/inventory/products":
            return self._inventory_products(path, query, headers or {})
        if path in {
            "/staff/inventory/appointments",
            "/staff/inventory/flow",
            "/staff/inventory/schedule",
        }:
            return self._inventory_schedule(path, query, headers or {})
        appointment_id = _inventory_appointment_id_from_path(path)
        if appointment_id is not None:
            return self._inventory_appointment_detail(
                path,
                appointment_id,
                query,
                headers or {},
            )
        if path == "/staff/inventory/analytics":
            return self._inventory_analytics(path, query, headers or {})
        if path == "/staff/inventory/planning":
            return self._inventory_planning(path, query, headers or {})
        model_code = _inventory_product_code_from_path(path)
        if model_code is not None:
            return self._inventory_product_detail(path, model_code, query, headers or {})
        image_model_code = _inventory_image_code_from_path(path)
        if image_model_code is not None:
            return self._inventory_image(path, image_model_code, query, headers or {})
        evidence_task_id = _evidence_task_id_from_path(path)
        if evidence_task_id is not None:
            try:
                self.signer.verify(
                    evidence_task_id,
                    query.get("expires", ""),
                    query.get("token", ""),
                )
                task = self.service.get_payment_task(evidence_task_id)
                if not task.evidence_path.is_file() or task.evidence_path.stat().st_size > 10_000_000:
                    raise ValueError("payment evidence is unavailable")
                content_type = mimetypes.guess_type(task.evidence_path.name)[0] or "image/jpeg"
                return StaffWebResponse(200, task.evidence_path.read_bytes(), content_type)
            except (OSError, ValueError):
                return StaffWebResponse(403, b"evidence unavailable")
        task_id = _task_id_from_path(path)
        if task_id is None:
            return None
        try:
            self.signer.verify(task_id, query.get("expires", ""), query.get("token", ""))
        except ValueError:
            return _page(403, "链接不可用", "链接已失效，或任务不存在。请从企业微信重新打开提醒。")
        try:
            task = self.service.get_time_task(task_id)
            return _page(200, "确认试纱时间", _task_content(task, query))
        except ValueError:
            try:
                payment = self.service.get_payment_task(task_id)
                return _page(200, "复核付款截图", _payment_task_content(payment, query))
            except ValueError:
                try:
                    handoff = self.service.get_handoff_task(task_id)
                    return _page(200, "人工接管会话", _handoff_task_content(handoff, query))
                except ValueError:
                    return _page(403, "链接不可用", "链接已失效，或任务不存在。请从企业微信重新打开提醒。")

    def _website_requests(self, path, query, headers, form=None):
        auth = self._inventory_auth(path, query, headers, allow_oauth_redirect=form is None)
        if isinstance(auth, StaffWebResponse):
            return auth
        error = ""
        status = 200
        if form is not None:
            try:
                self.session_signer.verify_csrf(auth.session_token, form.get('csrf', ''))
            except ValueError:
                return _page(403, '操作未执行', '页面验证已失效，请刷新后重试。')
            try:
                self.website_store.update_status(form.get('request_id',''), form.get('status',''), int(form.get('version','0')), auth.staff_id)
            except ValueError as exc:
                error, status = str(exc), 409
            else:
                return StaffWebResponse(303,b'',headers=(('Location','/staff/inventory/website?status=&saved=1'),))
        return StaffWebResponse(status, render_requests(self.website_store,staff_id=auth.staff_id,csrf=self.session_signer.csrf_token(auth.session_token),query=query,error=error),headers=auth.response_headers)

    def _native_context(self, query: dict[str, str]) -> StaffWebResponse:
        if self.wecom_client is None or not self.corp_id or self.agent_id <= 0 or not self.app_base_url:
            return _page(503, "客户上下文暂不可用", "内部身份验证尚未配置完成。")
        code = query.get("code", "").strip()
        if not code:
            oauth_query = urlencode(
                {
                    "appid": self.corp_id,
                    "redirect_uri": f"{self.app_base_url}/staff/context",
                    "response_type": "code",
                    "scope": "snsapi_base",
                    "agentid": self.agent_id,
                }
            )
            url = f"https://open.weixin.qq.com/connect/oauth2/authorize?{oauth_query}#wechat_redirect"
            return StaffWebResponse(302, b"", "text/plain; charset=utf-8", (("Location", url),))
        try:
            staff_id = self.wecom_client.staff_user_id_from_oauth_code(code)
        except (ConnectionError, ValueError):
            return _page(403, "无法验证身份", "请从企业微信客服工具栏重新打开。")
        if staff_id not in self.staff_user_ids:
            return _page(403, "无访问权限", "当前企业微信账号未被配置为接待人员。")
        handoffs = self.service.active_handoffs_for_staff(staff_id)
        return _page(200, "客户上下文", _native_context_content(handoffs))

    def _inventory_dashboard(
        self,
        path: str,
        query: dict[str, str],
        headers: dict[str, str],
    ) -> StaffWebResponse:
        auth = self._inventory_auth(path, query, headers)
        if isinstance(auth, StaffWebResponse):
            return auth
        return StaffWebResponse(
            200,
            render_inventory_dashboard(
                self.catalog_store.dashboard_summary(),
                staff_id=auth.staff_id,
            ),
            headers=auth.response_headers,
        )

    def _inventory_products(
        self,
        path: str,
        query: dict[str, str],
        headers: dict[str, str],
    ) -> StaffWebResponse:
        auth = self._inventory_auth(path, query, headers)
        if isinstance(auth, StaffWebResponse):
            return auth
        search = query.get("q", "")[:80]
        catalog_type = query.get("type", "")
        try:
            products = self.catalog_store.internal_products(search, catalog_type=catalog_type)
        except ValueError:
            products = ()
        return StaffWebResponse(
            200,
            render_inventory_products(
                products,
                staff_id=auth.staff_id,
                query=search,
                catalog_type=catalog_type,
            ),
            headers=auth.response_headers,
        )

    def _inventory_product_detail(
        self,
        path: str,
        model_code: str,
        query: dict[str, str],
        headers: dict[str, str],
    ) -> StaffWebResponse:
        auth = self._inventory_auth(path, query, headers)
        if isinstance(auth, StaffWebResponse):
            return auth
        try:
            product = self.catalog_store.internal_product(model_code)
        except ValueError:
            return _page(404, "款式不存在", "这条款式资料可能已停用或款号有误。")
        return StaffWebResponse(
            200,
            render_inventory_product_detail(
                product,
                staff_id=auth.staff_id,
                csrf_token=self.session_signer.csrf_token(auth.session_token),
                saved=query.get("saved") == "1",
            ),
            headers=auth.response_headers,
        )

    def _inventory_schedule(
        self,
        path: str,
        query: dict[str, str],
        headers: dict[str, str],
        *,
        error: str = "",
        status: int = 200,
    ) -> StaffWebResponse:
        auth = self._inventory_auth(path, query, headers)
        if isinstance(auth, StaffWebResponse):
            return auth
        inventory_view = path == "/staff/inventory/flow" or (
            path == "/staff/inventory/schedule" and query.get("view") == "inventory"
        )
        if not inventory_view:
            try:
                selected_date = date.fromisoformat(query.get("date", ""))
            except ValueError:
                selected_date = datetime.now().date()
            calendar_view = "week" if query.get("view") == "week" else "month"
            if calendar_view == "week":
                period_start = selected_date - timedelta(days=selected_date.weekday())
                period_days = 7
            else:
                period_start = selected_date.replace(day=1)
                period_days = monthrange(period_start.year, period_start.month)[1]
            appointments = self.catalog_store.calendar_appointments(
                period_start,
                days=period_days,
            )
            return StaffWebResponse(
                status,
                render_appointment_calendar(
                    appointments,
                    period_start,
                    staff_id=auth.staff_id,
                    csrf_token=self.session_signer.csrf_token(auth.session_token),
                    view=calendar_view,
                    saved=query.get("saved") == "1",
                    error=error,
                ),
                headers=auth.response_headers,
            )
        products = self.catalog_store.internal_products(limit=500)
        blocks = self.catalog_store.unit_blocks(
            ends_after=datetime.now().strftime("%Y-%m-%dT%H:%M")
        )
        return StaffWebResponse(
            status,
            render_inventory_schedule(
                products,
                blocks,
                staff_id=auth.staff_id,
                csrf_token=self.session_signer.csrf_token(auth.session_token),
                selected_unit=query.get("unit", "")[:80],
                saved=query.get("saved") == "1",
                error=error,
            ),
            headers=auth.response_headers,
        )

    def _inventory_appointment_detail(
        self,
        path: str,
        appointment_id: str,
        query: dict[str, str],
        headers: dict[str, str],
        *,
        error: str = "",
        status: int = 200,
    ) -> StaffWebResponse:
        auth = self._inventory_auth(path, query, headers)
        if isinstance(auth, StaffWebResponse):
            return auth
        try:
            appointment = self.catalog_store.calendar_appointment(appointment_id)
            styles = self.catalog_store.appointment_style_options(appointment_id)
        except ValueError:
            return _page(404, "预约不存在", "这条预约可能已经删除或编号有误。")
        return StaffWebResponse(
            status,
            render_appointment_detail(
                appointment,
                styles,
                staff_id=auth.staff_id,
                csrf_token=self.session_signer.csrf_token(auth.session_token),
                saved=query.get("saved") == "1",
                error=error,
            ),
            headers=auth.response_headers,
        )

    def _inventory_analytics(
        self,
        path: str,
        query: dict[str, str],
        headers: dict[str, str],
    ) -> StaffWebResponse:
        auth = self._inventory_auth(path, query, headers)
        if isinstance(auth, StaffWebResponse):
            return auth
        try:
            days = int(query.get("days", "90"))
            summary = self.catalog_store.analytics_summary(window_days=days)
        except ValueError:
            summary = self.catalog_store.analytics_summary(window_days=90)
        return StaffWebResponse(
            200,
            render_inventory_analytics(summary, staff_id=auth.staff_id),
            headers=auth.response_headers,
        )

    def _inventory_planning(
        self,
        path: str,
        query: dict[str, str],
        headers: dict[str, str],
    ) -> StaffWebResponse:
        auth = self._inventory_auth(path, query, headers)
        if isinstance(auth, StaffWebResponse):
            return auth
        try:
            days = int(query.get("days", "90"))
            summary = self.catalog_store.planning_summary(window_days=days)
        except ValueError:
            summary = self.catalog_store.planning_summary(window_days=90)
        return StaffWebResponse(
            200,
            render_inventory_planning(summary, staff_id=auth.staff_id),
            headers=auth.response_headers,
        )

    def _inventory_image(
        self,
        path: str,
        model_code: str,
        query: dict[str, str],
        headers: dict[str, str],
    ) -> StaffWebResponse:
        auth = self._inventory_auth(path, query, headers, allow_oauth_redirect=False)
        if isinstance(auth, StaffWebResponse):
            return StaffWebResponse(403, b"image unavailable")
        try:
            image_path = self.catalog_store.catalog_image_path(model_code)
            content_type = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
            return StaffWebResponse(200, image_path.read_bytes(), content_type)
        except (OSError, ValueError):
            return StaffWebResponse(404, b"image unavailable")

    def _staff_login_page(
        self,
        query: dict[str, str],
        headers: dict[str, str],
        *,
        error: str = "",
        status: int = 200,
    ) -> StaffWebResponse:
        next_path = _safe_inventory_next(query.get("next", ""))
        staff_id = self._session_staff_id(headers)
        if staff_id:
            return StaffWebResponse(
                303,
                b"",
                "text/plain; charset=utf-8",
                (("Location", next_path),),
            )
        if self.session_signer is None or self.password_verifier is None:
            return _page(503, "网页登录暂不可用", "独立登录账号尚未配置完成。")
        login_token = self.session_signer.issue("login-csrf")
        body = _staff_login_html(
            next_path,
            login_token,
            web_login_enabled=bool(self.password_verifier.usernames),
            wecom_login_enabled=bool(
                self.corp_id and self.agent_id > 0 and self.app_base_url
            ),
            error=error,
        )
        return StaffWebResponse(
            status,
            body,
            headers=((
                "Set-Cookie",
                f"{STAFF_LOGIN_CSRF_COOKIE}={login_token}; Path=/staff/login; "
                "Max-Age=900; Secure; HttpOnly; SameSite=Strict",
            ),),
        )

    def _staff_login_post(
        self,
        query: dict[str, str],
        form: dict[str, str],
        headers: dict[str, str],
    ) -> StaffWebResponse:
        next_path = _safe_inventory_next(form.get("next", query.get("next", "")))
        username = form.get("username", "").strip().lower()
        login_key = self._login_key(headers, username)
        if not self._login_allowed(login_key):
            return self._staff_login_page(
                {"next": next_path},
                headers,
                error="尝试次数过多，请 15 分钟后再试。",
                status=429,
            )
        try:
            if self.session_signer is None or self.password_verifier is None:
                raise ValueError("login unavailable")
            login_token = form.get("login_token", "")
            cookie_token = _cookie_value(
                headers.get("Cookie", ""),
                STAFF_LOGIN_CSRF_COOKIE,
            )
            if not cookie_token or not hmac.compare_digest(cookie_token, login_token):
                raise ValueError("invalid login token")
            if self.session_signer.verify(login_token) != "login-csrf":
                raise ValueError("invalid login token")
            staff_id = self.password_verifier.verify(
                username,
                form.get("password", ""),
            )
            if not staff_id:
                raise ValueError("invalid credentials")
        except ValueError:
            self._record_login_failure(login_key)
            return self._staff_login_page(
                {"next": next_path},
                headers,
                error="账号或密码不正确。",
                status=401,
            )
        self._clear_login_failures(login_key)
        session_token = self.session_signer.issue(staff_id)
        return StaffWebResponse(
            303,
            b"",
            "text/plain; charset=utf-8",
            (
                ("Location", next_path),
                (
                    "Set-Cookie",
                    f"{STAFF_SESSION_COOKIE}={session_token}; Path=/staff; "
                    "Max-Age=43200; Secure; HttpOnly; SameSite=Lax",
                ),
                (
                    "Set-Cookie",
                    f"{STAFF_LOGIN_CSRF_COOKIE}=; Path=/staff/login; "
                    "Max-Age=0; Secure; HttpOnly; SameSite=Strict",
                ),
            ),
        )

    def _staff_wecom_redirect(self, query: dict[str, str]) -> StaffWebResponse:
        if not self.corp_id or self.agent_id <= 0 or not self.app_base_url:
            return _page(503, "企业微信登录暂不可用", "企业微信授权尚未配置完成。")
        next_path = _safe_inventory_next(query.get("next", ""))
        oauth_query = urlencode(
            {
                "appid": self.corp_id,
                "redirect_uri": f"{self.app_base_url}{next_path}",
                "response_type": "code",
                "scope": "snsapi_base",
                "agentid": self.agent_id,
            }
        )
        return StaffWebResponse(
            302,
            b"",
            "text/plain; charset=utf-8",
            ((
                "Location",
                f"https://open.weixin.qq.com/connect/oauth2/authorize?{oauth_query}#wechat_redirect",
            ),),
        )

    def _staff_logout(self) -> StaffWebResponse:
        return StaffWebResponse(
            303,
            b"",
            "text/plain; charset=utf-8",
            (
                ("Location", "/staff/login"),
                (
                    "Set-Cookie",
                    f"{STAFF_SESSION_COOKIE}=; Path=/staff; Max-Age=0; "
                    "Secure; HttpOnly; SameSite=Lax",
                ),
            ),
        )

    def _session_staff_id(self, headers: dict[str, str]) -> str:
        if self.session_signer is None:
            return ""
        token = _cookie_value(headers.get("Cookie", ""), STAFF_SESSION_COOKIE)
        if not token:
            return ""
        try:
            staff_id = self.session_signer.verify(token)
        except ValueError:
            return ""
        web_users = self.password_verifier.usernames if self.password_verifier else frozenset()
        return staff_id if staff_id in self.staff_user_ids or staff_id in web_users else ""

    def _login_key(self, headers: dict[str, str], username: str) -> str:
        forwarded = headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
        return f"{forwarded or 'unknown'}:{username or 'unknown'}"

    def _login_allowed(self, key: str) -> bool:
        cutoff = time.monotonic() - 15 * 60
        with self._login_lock:
            attempts = [value for value in self._login_failures.get(key, ()) if value >= cutoff]
            self._login_failures[key] = attempts
            return len(attempts) < 10

    def _record_login_failure(self, key: str) -> None:
        with self._login_lock:
            self._login_failures.setdefault(key, []).append(time.monotonic())

    def _clear_login_failures(self, key: str) -> None:
        with self._login_lock:
            self._login_failures.pop(key, None)

    def _inventory_auth(
        self,
        path: str,
        query: dict[str, str],
        headers: dict[str, str],
        *,
        allow_oauth_redirect: bool = True,
    ) -> _InventoryAuth | StaffWebResponse:
        if (
            self.catalog_store is None
            or self.wecom_client is None
            or self.session_signer is None
            or not self.corp_id
            or self.agent_id <= 0
            or not self.app_base_url
        ):
            return _page(503, "库存工作台暂不可用", "内部身份验证或库存目录尚未配置完成。")
        session_token = _cookie_value(headers.get("Cookie", ""), STAFF_SESSION_COOKIE)
        staff_id = self._session_staff_id(headers)
        if staff_id:
            return _InventoryAuth(staff_id, session_token)
        code = query.get("code", "").strip()
        if not code:
            if not allow_oauth_redirect:
                return StaffWebResponse(403, b"staff session required")
            preserved_query = {
                key: value for key, value in query.items() if key != "code"
            }
            next_path = path + (
                f"?{urlencode(preserved_query)}" if preserved_query else ""
            )
            if "wxwork" in headers.get("User-Agent", "").lower():
                return self._staff_wecom_redirect({"next": next_path})
            return StaffWebResponse(
                302,
                b"",
                "text/plain; charset=utf-8",
                (("Location", f"/staff/login?{urlencode({'next': next_path})}"),),
            )
        try:
            staff_id = self.wecom_client.staff_user_id_from_oauth_code(code)
        except (ConnectionError, ValueError):
            return _page(403, "无法验证身份", "请从企业微信重新打开库存工作台。")
        if staff_id not in self.staff_user_ids:
            return _page(403, "无访问权限", "当前企业微信账号未被配置为店员。")
        session_token = self.session_signer.issue(staff_id)
        return _InventoryAuth(
            staff_id,
            session_token,
            ((
                "Set-Cookie",
                f"{STAFF_SESSION_COOKIE}={session_token}; Path=/staff; "
                "Max-Age=43200; Secure; HttpOnly; SameSite=Lax",
            ),),
        )

    def post(
        self,
        path: str,
        query: dict[str, str],
        form: dict[str, str],
        headers: dict[str, str] | None = None,
    ) -> StaffWebResponse | None:
        if path == "/website/appointments":
            if self.website_store is None:
                return StaffWebResponse(503,b'{"message":"Appointment intake unavailable"}', 'application/json; charset=utf-8')
            try:
                status, body = public_intake(self.website_store, form, headers or {}, self.app_base_url)
                return StaffWebResponse(status, body, 'application/json; charset=utf-8')
            except IntakeError as exc:
                return StaffWebResponse(exc.status,json.dumps({'message':str(exc)},ensure_ascii=False).encode(),'application/json; charset=utf-8', (('Retry-After','3600'),) if exc.status==429 else ())
        if path == "/staff/inventory/website":
            return self._website_requests(path, query, headers or {}, form)
        if path == "/staff/login":
            return self._staff_login_post(query, form, headers or {})
        if path == "/staff/inventory/appointments":
            return self._inventory_appointment_create_post(path, query, form, headers or {})
        if (
            path.startswith("/staff/inventory/appointments/")
            or path.startswith("/staff/inventory/schedule/appointments/")
        ) and path.endswith("/styles"):
            return self._inventory_appointment_styles_post(path, query, form, headers or {})
        if path in {"/staff/inventory/flow", "/staff/inventory/schedule"} or (
            (
                path.startswith("/staff/inventory/flow/")
                or path.startswith("/staff/inventory/schedule/")
            )
            and path.endswith("/cancel")
        ):
            return self._inventory_schedule_post(path, query, form, headers or {})
        if path == "/staff/inventory/units" or path.startswith("/staff/inventory/units/"):
            return self._inventory_post(path, query, form, headers or {})
        task_id = _task_id_from_path(path)
        if task_id is None:
            return None
        try:
            self.signer.verify(task_id, query.get("expires", ""), query.get("token", ""))
            action = form.get("action", "")
            if action == "approve":
                self.service.approve_time(task_id, "wecom-signed-link")
                message = "已确认该时间，付款说明和收款码已进入发送队列。"
            elif action == "reject":
                self.service.reject_time(task_id, "wecom-signed-link")
                message = "已标记该时间无法安排，系统会继续向顾客询问其他时间。"
            elif action == "approve_payment":
                if form.get("payment_verified") != "yes":
                    return _page(
                        400,
                        "请先完成核对",
                        "需要确认金额、收款方和支付成功状态后，才能人工锁定档期。",
                    )
                self.service.approve_payment(task_id, "wecom-signed-link")
                message = "已确认收到付款，顾客档期已锁定。"
            elif action == "reject_payment":
                self.service.reject_payment(task_id, "wecom-signed-link")
                message = "已要求顾客重新提交完整的付款截图。"
            elif action == "take_over":
                self.service.activate_handoff(task_id, "wecom-signed-link")
                message = "会话已由你接管，AI 已停止回复。请回到微信客服会话继续沟通。"
            elif action == "return_ai":
                self.service.return_handoff(
                    task_id,
                    "wecom-signed-link",
                    form.get("summary", ""),
                )
                message = "会话已交还 AI，交接摘要已写入上下文；将从顾客下一条新消息继续自动接待，不会补发历史消息。"
            elif action == "send_reply":
                self.service.send_handoff_reply(
                    task_id,
                    "wecom-signed-link",
                    form.get("reply", ""),
                )
                message = "回复已进入发送队列，通常几秒内送达顾客。"
            else:
                return _page(400, "操作无效", "没有识别到要执行的操作。")
            try:
                task = self.service.get_time_task(task_id)
            except ValueError:
                try:
                    task = self.service.get_payment_task(task_id)
                except ValueError:
                    task = self.service.get_handoff_task(task_id)
        except ValueError as error:
            return _page(409, "暂时无法处理", str(error))
        if action == "send_reply":
            return _page(
                200,
                "人工接管会话",
                f'<p class="notice">{escape(message)}</p>{_handoff_task_content(task, query)}',
            )
        return _page(200, "处理完成", _result_content(task, message))

    def _inventory_post(
        self,
        path: str,
        query: dict[str, str],
        form: dict[str, str],
        headers: dict[str, str],
    ) -> StaffWebResponse:
        auth = self._inventory_auth(path, query, headers, allow_oauth_redirect=False)
        if isinstance(auth, StaffWebResponse):
            return _page(403, "登录已失效", "请从企业微信重新打开库存工作台。")
        try:
            self.session_signer.verify_csrf(auth.session_token, form.get("csrf", ""))
            if path == "/staff/inventory/units":
                model_code = form.get("model_code", "").strip().lower()
                self.catalog_store.add_inventory_unit(
                    model_code,
                    unit_code=form.get("unit_code", ""),
                    size_label=form.get("size_label", ""),
                    bust_cm=_optional_float(form.get("bust_cm", "")),
                    waist_cm=_optional_float(form.get("waist_cm", "")),
                    hip_cm=_optional_float(form.get("hip_cm", "")),
                    dress_length_cm=_optional_float(form.get("dress_length_cm", "")),
                    location=form.get("location", ""),
                    inventory_state=form.get("inventory_state", "unverified"),
                    condition_state=form.get("condition_state", "good"),
                    notes=form.get("notes", ""),
                )
            else:
                unit_code = unquote(path.removeprefix("/staff/inventory/units/"))
                if not unit_code or "/" in unit_code:
                    raise ValueError("实物编号无效")
                product = self.catalog_store.internal_products(limit=500)
                model_code = next(
                    (item.model_code for item in product if any(unit.unit_code == unit_code for unit in item.units)),
                    "",
                )
                if not model_code:
                    raise ValueError("实物件不存在")
                self.catalog_store.update_inventory_unit(
                    unit_code,
                    size_label=form.get("size_label", ""),
                    bust_cm=_optional_float(form.get("bust_cm", "")),
                    waist_cm=_optional_float(form.get("waist_cm", "")),
                    hip_cm=_optional_float(form.get("hip_cm", "")),
                    dress_length_cm=_optional_float(form.get("dress_length_cm", "")),
                    location=form.get("location", ""),
                    inventory_state=form.get("inventory_state", "unverified"),
                    condition_state=form.get("condition_state", "good"),
                    notes=form.get("notes", ""),
                )
        except ValueError as error:
            return _page(400, "无法保存库存", str(error))
        location = f"/staff/inventory/products/{quote(model_code, safe='-._~')}?saved=1"
        return StaffWebResponse(303, b"", "text/plain; charset=utf-8", (("Location", location),))

    def _inventory_schedule_post(
        self,
        path: str,
        query: dict[str, str],
        form: dict[str, str],
        headers: dict[str, str],
    ) -> StaffWebResponse:
        auth = self._inventory_auth(path, query, headers, allow_oauth_redirect=False)
        if isinstance(auth, StaffWebResponse):
            return _page(403, "登录已失效", "请从企业微信重新打开库存工作台。")
        try:
            self.session_signer.verify_csrf(auth.session_token, form.get("csrf", ""))
            if path in {"/staff/inventory/flow", "/staff/inventory/schedule"}:
                self.catalog_store.add_unit_block(
                    form.get("unit_code", ""),
                    form.get("starts_at", ""),
                    form.get("ends_at", ""),
                    form.get("block_type", ""),
                    notes=form.get("notes", ""),
                )
            else:
                prefix = (
                    "/staff/inventory/flow/"
                    if path.startswith("/staff/inventory/flow/")
                    else "/staff/inventory/schedule/"
                )
                block_id = path.removeprefix(prefix).removesuffix("/cancel")
                if not block_id or "/" in block_id:
                    raise ValueError("档期编号无效")
                self.catalog_store.cancel_unit_block(block_id)
        except ValueError as error:
            products = self.catalog_store.internal_products(limit=500)
            blocks = self.catalog_store.unit_blocks(
                ends_after=datetime.now().strftime("%Y-%m-%dT%H:%M")
            )
            return StaffWebResponse(
                400,
                render_inventory_schedule(
                    products,
                    blocks,
                    staff_id=auth.staff_id,
                    csrf_token=self.session_signer.csrf_token(auth.session_token),
                    selected_unit=form.get("unit_code", "")[:80],
                    error=str(error),
                ),
            )
        return StaffWebResponse(
            303,
            b"",
            "text/plain; charset=utf-8",
            (("Location", "/staff/inventory/flow?saved=1"),),
        )

    def _inventory_appointment_styles_post(
        self,
        path: str,
        query: dict[str, str],
        form: dict[str, str],
        headers: dict[str, str],
    ) -> StaffWebResponse:
        auth = self._inventory_auth(path, query, headers, allow_oauth_redirect=False)
        if isinstance(auth, StaffWebResponse):
            return _page(403, "登录已失效", "请从企业微信重新打开库存工作台。")
        appointment_id = _inventory_appointment_id_from_styles_path(path)
        if appointment_id is None:
            return _page(404, "预约不存在", "预约编号无效。")
        try:
            self.session_signer.verify_csrf(auth.session_token, form.get("csrf", ""))
            model_codes = tuple(
                unquote(key.removeprefix("style_"))
                for key, value in form.items()
                if key.startswith("style_") and value == "1"
            )
            self.catalog_store.set_appointment_style_interests(
                appointment_id,
                model_codes,
            )
        except ValueError as error:
            return self._inventory_appointment_detail(
                f"/staff/inventory/appointments/{appointment_id}",
                appointment_id,
                query,
                headers,
                error=str(error),
                status=400,
            )
        location = f"/staff/inventory/appointments/{quote(appointment_id, safe='-._~')}?saved=1"
        return StaffWebResponse(
            303,
            b"",
            "text/plain; charset=utf-8",
            (("Location", location),),
        )

    def _inventory_appointment_create_post(
        self,
        path: str,
        query: dict[str, str],
        form: dict[str, str],
        headers: dict[str, str],
    ) -> StaffWebResponse:
        auth = self._inventory_auth(path, query, headers, allow_oauth_redirect=False)
        if isinstance(auth, StaffWebResponse):
            return _page(403, "登录已失效", "请从企业微信重新打开库存工作台。")
        appointment_date = form.get("appointment_date", "").strip()
        try:
            self.session_signer.verify_csrf(auth.session_token, form.get("csrf", ""))
            self.catalog_store.add_manual_appointment(
                customer_name=form.get("customer_name", ""),
                customer_phone=form.get("customer_phone", ""),
                starts_at=f"{appointment_date}T{form.get('appointment_time', '').strip()}",
                status=form.get("status", "time_pending_staff"),
            )
        except ValueError as error:
            return self._inventory_schedule(
                path,
                {"date": appointment_date} if appointment_date else query,
                headers,
                error=str(error),
                status=400,
            )
        location = f"/staff/inventory/appointments?date={quote(appointment_date, safe='-')}&saved=1"
        return StaffWebResponse(
            303,
            b"",
            "text/plain; charset=utf-8",
            (("Location", location),),
        )


def _safe_inventory_next(value: str) -> str:
    value = str(value or "").strip()
    if (
        not value.startswith("/staff/inventory")
        or value.startswith("//")
        or "\r" in value
        or "\n" in value
        or len(value) > 500
    ):
        return "/staff/inventory/appointments"
    return value


def _staff_login_html(
    next_path: str,
    login_token: str,
    *,
    web_login_enabled: bool,
    wecom_login_enabled: bool,
    error: str = "",
) -> bytes:
    error_html = f'<div class="error" role="alert">{escape(error)}</div>' if error else ""
    password_form = (
        f"""
        <form method="post" action="/staff/login">
          <input type="hidden" name="next" value="{escape(next_path, quote=True)}">
          <input type="hidden" name="login_token" value="{escape(login_token, quote=True)}">
          <label><span>账号</span><input name="username" autocomplete="username" required autofocus></label>
          <label><span>密码</span><input type="password" name="password" autocomplete="current-password" required></label>
          <button type="submit">登录库存工作台</button>
        </form>
        """
        if web_login_enabled
        else '<p class="unavailable">网页登录账号尚未配置。</p>'
    )
    wecom_link = (
        f'<a class="wecom" href="/staff/login/wecom?{urlencode({"next": next_path})}">使用企业微信免登录</a>'
        if wecom_login_enabled
        else ""
    )
    html = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>登录 · Forthatday 库存工作台</title><style>
    :root{{--canvas:#f2f5f3;--surface:#fff;--ink:#18211d;--muted:#66716b;--line:#d8dfdb;--spruce:#285846;--danger:#963c36}}*{{box-sizing:border-box}}html{{font-family:"Noto Sans SC","Microsoft YaHei",sans-serif;background:var(--canvas);color:var(--ink)}}body{{min-height:100vh;margin:0;display:grid;place-items:center;padding:24px}}main{{width:min(420px,100%)}}.brand{{display:flex;align-items:center;gap:12px;margin-bottom:28px}}.mark{{width:38px;height:38px;display:grid;place-items:center;border:1px solid #71847b;border-radius:6px;font:700 11px monospace}}.brand div{{display:grid;gap:3px}}.brand strong{{font-size:17px}}.brand small{{color:var(--muted);font-size:11px}}section{{padding:28px;border:1px solid var(--line);border-radius:8px;background:var(--surface)}}h1{{margin:0;font-size:25px;letter-spacing:0}}.intro{{margin:8px 0 24px;color:var(--muted);font-size:12px;line-height:1.7}}form{{display:grid;gap:16px}}label{{display:grid;gap:7px}}label span{{color:var(--muted);font-size:11px}}input,button{{width:100%;height:44px;padding:0 12px;border:1px solid #cbd4cf;border-radius:6px;background:#fff;color:var(--ink);font:inherit}}button{{margin-top:4px;border-color:var(--spruce);background:var(--spruce);color:#fff;font-weight:700;cursor:pointer}}.error{{margin:0 0 16px;padding:11px 12px;border-radius:6px;background:#fae9e7;color:var(--danger);font-size:12px}}.wecom{{height:44px;margin-top:14px;display:grid;place-items:center;border:1px solid var(--line);border-radius:6px;color:var(--spruce);font-size:12px;font-weight:700;text-decoration:none}}.unavailable{{color:var(--muted);font-size:12px}}footer{{margin-top:18px;color:var(--muted);font-size:10px;line-height:1.7;text-align:center}}:focus-visible{{outline:3px solid rgba(40,88,70,.25);outline-offset:2px}}
    </style></head><body><main><div class="brand"><span class="mark">FTD</span><div><strong>Forthatday</strong><small>内部库存工作台</small></div></div><section><h1>店员登录</h1><p class="intro">预约、顾客联系方式与内部库存仅限授权店员访问。</p>{error_html}{password_form}{wecom_link}</section><footer>登录状态保留 12 小时，请勿在公共设备保存密码。</footer></main></body></html>"""
    return html.encode("utf-8")


def _task_id_from_path(path: str) -> str | None:
    prefix = "/staff/tasks/"
    if not path.startswith(prefix):
        return None
    task_id = path[len(prefix) :]
    if not task_id or "/" in task_id or len(task_id) > 80:
        return None
    return task_id


def _inventory_appointment_id_from_path(path: str) -> str | None:
    prefixes = (
        "/staff/inventory/appointments/",
        "/staff/inventory/schedule/appointments/",
    )
    prefix = next((value for value in prefixes if path.startswith(value)), "")
    if not prefix:
        return None
    appointment_id = path[len(prefix) :]
    if not appointment_id or "/" in appointment_id or len(appointment_id) > 100:
        return None
    return unquote(appointment_id)


def _inventory_appointment_id_from_styles_path(path: str) -> str | None:
    prefixes = (
        "/staff/inventory/appointments/",
        "/staff/inventory/schedule/appointments/",
    )
    prefix = next((value for value in prefixes if path.startswith(value)), "")
    suffix = "/styles"
    if not prefix or not path.endswith(suffix):
        return None
    appointment_id = path[len(prefix) : -len(suffix)]
    if not appointment_id or "/" in appointment_id or len(appointment_id) > 100:
        return None
    return unquote(appointment_id)


def _inventory_product_code_from_path(path: str) -> str | None:
    prefix = "/staff/inventory/products/"
    if not path.startswith(prefix):
        return None
    model_code = path[len(prefix) :]
    if not model_code or "/" in model_code or len(model_code) > 80:
        return None
    return unquote(model_code).lower()


def _inventory_image_code_from_path(path: str) -> str | None:
    prefix = "/staff/inventory/assets/"
    if not path.startswith(prefix):
        return None
    model_code = path[len(prefix) :]
    if not model_code or "/" in model_code or len(model_code) > 80:
        return None
    return unquote(model_code).lower()


def _cookie_value(raw_cookie: str, name: str) -> str:
    try:
        cookie = SimpleCookie()
        cookie.load(raw_cookie)
        return cookie[name].value if name in cookie else ""
    except (KeyError, ValueError):
        return ""


def _optional_float(value: str) -> float | None:
    value = value.strip()
    if not value:
        return None
    try:
        number = float(value)
    except ValueError as error:
        raise ValueError("尺寸需要填写数字") from error
    if number <= 0 or number > 500:
        raise ValueError("尺寸数值不合理")
    return number


def _evidence_task_id_from_path(path: str) -> str | None:
    suffix = "/evidence"
    if not path.endswith(suffix):
        return None
    return _task_id_from_path(path[: -len(suffix)])


def _task_content(task, query: dict[str, str]) -> str:
    rows = (
        ("顾客", task.customer_name),
        ("电话", task.customer_phone),
        ("希望时间", task.requested_time),
        ("当前状态", _status_label(task.appointment_status)),
    )
    details = "".join(
        f"<dt>{escape(label)}</dt><dd>{escape(value or '未提供')}</dd>"
        for label, value in rows
    )
    if task.status != "open" or task.appointment_status != "time_pending_staff":
        actions = '<p class="notice">这个任务已经处理，无需重复操作。</p>'
    else:
        token = escape(query.get("token", ""), quote=True)
        expires = escape(query.get("expires", ""), quote=True)
        action_url = f"/staff/tasks/{escape(task.id, quote=True)}?expires={expires}&amp;token={token}"
        actions = f"""
        <form method="post" action="{action_url}">
          <button class="approve" type="submit" name="action" value="approve">同意这个时间</button>
          <button class="reject" type="submit" name="action" value="reject">需要换时间</button>
        </form>
        """
    return f"<dl>{details}</dl>{actions}"


def _result_content(task, message: str) -> str:
    time_value = (
        getattr(task, "requested_time", "")
        or getattr(task, "confirmed_time", "")
        or getattr(task, "appointment_time", "")
    )
    return (
        f'<p class="notice">{escape(message)}</p>'
        f'<dl><dt>顾客</dt><dd>{escape(task.customer_name)}</dd>'
        f'<dt>预约时间</dt><dd>{escape(time_value or "待重新沟通")}</dd>'
        f'<dt>状态</dt><dd>{escape(_status_label(task.appointment_status))}</dd></dl>'
    )


def _payment_task_content(task, query: dict[str, str]) -> str:
    amount = f"{task.extracted_amount} 元" if task.extracted_amount else "未识别"
    rows = (
        ("顾客", task.customer_name),
        ("预约时间", task.confirmed_time),
        ("识别金额", amount),
        ("识别收款方", task.extracted_payee or "未识别"),
        ("支付状态", "已识别成功" if task.shows_success else "未识别"),
        ("识别置信度", f"{task.confidence:.0%}"),
        ("未通过原因", "、".join(_reason_label(item) for item in task.reasons)),
    )
    details = "".join(
        f"<dt>{escape(label)}</dt><dd>{escape(value)}</dd>" for label, value in rows
    )
    token = escape(query.get("token", ""), quote=True)
    expires = escape(query.get("expires", ""), quote=True)
    base = f"/staff/tasks/{escape(task.id, quote=True)}"
    image_url = f"{base}/evidence?expires={expires}&amp;token={token}"
    if task.status != "open" or task.appointment_status != "payment_review":
        actions = '<p class="notice">这个任务已经处理，无需重复操作。</p>'
    else:
        action_url = f"{base}?expires={expires}&amp;token={token}"
        actions = f"""
        <form method="post" action="{action_url}">
          <label class="verify"><input type="checkbox" name="payment_verified" value="yes" required>我已核对金额为200元、收款方正确，并确认支付成功</label>
          <button class="approve" type="submit" name="action" value="approve_payment">确认已付款</button>
          <button class="reject" type="submit" name="action" value="reject_payment">要求重新提交</button>
        </form>
        """
    return f'<img class="evidence" src="{image_url}" alt="顾客付款截图"><dl>{details}</dl>{actions}'


def _reason_label(reason: str) -> str:
    return {
        "amount_mismatch": "金额未匹配",
        "success_state_missing": "未识别支付成功状态",
        "confidence_too_low": "图片识别置信度不足",
        "payee_mismatch": "收款方未匹配",
        "duplicate_image": "截图曾被提交",
        "payee_policy_not_configured": "收款方规则待检查",
    }.get(reason, "需要人工核对")


def _handoff_task_content(task, query: dict[str, str]) -> str:
    rows = (
        ("顾客", task.customer_name or "未收集"),
        ("电话", task.customer_phone or "未收集"),
        ("预约状态", _status_label(task.appointment_status) if task.appointment_status else "暂无预约"),
        ("预约时间", task.appointment_time or "未确定"),
        ("接管原因", task.reason or "顾客要求人工回复"),
    )
    details = "".join(
        f"<dt>{escape(label)}</dt><dd>{escape(value)}</dd>" for label, value in rows
    )
    actor_labels = {"customer": "顾客", "staff": "店员", "ai": "AI", "system": "系统"}
    messages = "".join(
        f'<li><strong>{escape(actor_labels.get(item.actor, item.actor))}</strong>'
        f'<span>{escape(item.text)}</span></li>'
        for item in task.messages
    ) or '<li><span>暂无可显示的消息</span></li>'
    token = escape(query.get("token", ""), quote=True)
    expires = escape(query.get("expires", ""), quote=True)
    action_url = (
        f"/staff/tasks/{escape(task.id, quote=True)}?expires={expires}&amp;token={token}"
    )
    if task.status == "resolved" or task.owner == "ai":
        actions = '<p class="notice">这次人工接管已经结束，会话目前由 AI 继续处理。</p>'
    elif task.owner == "human_pending":
        actions = f"""
        <form method="post" action="{action_url}">
          <button class="approve full" type="submit" name="action" value="take_over">我来接管</button>
        </form>
        """
    else:
        actions = f"""
        <form class="reply-form" method="post" action="{action_url}">
          <label class="summary">回复顾客<textarea name="reply" rows="4" maxlength="2000" required placeholder="在这里输入店员回复"></textarea></label>
          <button class="approve full" type="submit" name="action" value="send_reply">发送给顾客</button>
        </form>
        <a class="refresh" href="{action_url}">刷新最近对话</a>
        <form class="return-form" method="post" action="{action_url}">
          <label class="summary">交接摘要<textarea name="summary" rows="4" required placeholder="例如：已确认顾客希望改到周日下午，等待重新确认时间。"></textarea></label>
          <button class="approve full" type="submit" name="action" value="return_ai">交还 AI</button>
        </form>
        """
    return f'<dl>{details}</dl><h2>最近对话</h2><ol class="messages">{messages}</ol>{actions}'


def _native_context_content(handoffs) -> str:
    if not handoffs:
        return '<p class="notice">当前没有分配给你的人工接管会话。</p>'
    blocks = []
    actor_labels = {"customer": "顾客", "staff": "店员", "ai": "AI", "system": "系统"}
    for task in handoffs:
        rows = (
            ("顾客", task.customer_name or "未收集"),
            ("电话", task.customer_phone or "未收集"),
            ("预约", _status_label(task.appointment_status) if task.appointment_status else "暂无预约"),
            ("时间", task.appointment_time or "未确认"),
            ("接管原因", task.reason or "顾客请求人工回复"),
        )
        details = "".join(
            f"<dt>{escape(label)}</dt><dd>{escape(value)}</dd>" for label, value in rows
        )
        messages = "".join(
            f'<li><strong>{escape(actor_labels.get(item.actor, item.actor))}</strong>'
            f'<span>{escape(item.text)}</span></li>'
            for item in task.messages
        ) or '<li><span>暂无可显示的消息</span></li>'
        blocks.append(
            f"<section class=\"context\"><dl>{details}</dl>"
            f"<h2>完整对话</h2><ol class=\"messages\">{messages}</ol></section>"
        )
    return "".join(blocks)


def _status_label(status: str) -> str:
    return {
        "time_pending_staff": "等待店员确认",
        "awaiting_payment": "等待顾客付款",
        "collecting_details": "正在重新沟通时间",
        "payment_review": "等待店员复核付款",
        "confirmed": "档期已锁定",
        "cancelled": "已取消",
    }.get(status, status)


def _page(status: int, title: str, content: str) -> StaffWebResponse:
    body = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)} · Forthatday</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; color: #171717; background: #f5f6f7; font: 16px/1.55 system-ui, sans-serif; }}
    main {{ width: min(100% - 32px, 560px); margin: 40px auto; background: #fff; border: 1px solid #e2e4e7; border-radius: 8px; padding: 28px; }}
    h1 {{ margin: 0 0 24px; font-size: 24px; letter-spacing: 0; }}
    dl {{ display: grid; grid-template-columns: 88px 1fr; gap: 12px 16px; margin: 0 0 28px; }}
    dt {{ color: #6b7075; }} dd {{ margin: 0; overflow-wrap: anywhere; }}
    form {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
    .verify {{ grid-column: 1 / -1; display: flex; gap: 10px; align-items: flex-start; }}
    .verify input {{ width: 20px; height: 20px; flex: 0 0 auto; }}
    .full, .summary {{ grid-column: 1 / -1; }}
    .summary textarea {{ width: 100%; margin-top: 8px; padding: 10px; resize: vertical; font: inherit; }}
    .reply-form {{ margin-bottom: 12px; }}
    .return-form {{ margin-top: 24px; padding-top: 24px; border-top: 1px solid #e2e4e7; }}
    .refresh {{ display: block; padding: 10px 0; color: #147d55; text-align: center; }}
    button {{ min-height: 48px; border: 1px solid #c9cdd2; border-radius: 6px; background: #fff; font: inherit; cursor: pointer; }}
    .evidence {{ display: block; width: 100%; max-height: 560px; object-fit: contain; margin: 0 0 24px; background: #f5f6f7; }}
    h2 {{ margin: 28px 0 12px; font-size: 18px; }}
    .messages {{ list-style: none; margin: 0 0 28px; padding: 0; border-top: 1px solid #e2e4e7; }}
    .messages li {{ display: grid; grid-template-columns: 52px 1fr; gap: 10px; padding: 10px 0; border-bottom: 1px solid #e2e4e7; }}
    .messages strong {{ color: #6b7075; font-size: 14px; }}
    .messages span {{ overflow-wrap: anywhere; }}
    .approve {{ color: #fff; border-color: #147d55; background: #147d55; }}
    .notice {{ margin: 0; padding: 14px 16px; border-left: 4px solid #147d55; background: #f0f8f4; }}
    @media (max-width: 440px) {{ main {{ margin: 16px auto; padding: 22px; }} form {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body><main><h1>{escape(title)}</h1>{content}</main></body>
</html>""".encode("utf-8")
    return StaffWebResponse(status=status, body=body)
