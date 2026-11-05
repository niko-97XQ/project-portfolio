from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from service_agent.infrastructure.wecom_api import WeComSessionStateTransitionUnavailable


@dataclass(frozen=True, slots=True)
class StaffTimeTask:
    id: str
    status: str
    appointment_status: str
    customer_name: str
    customer_phone: str
    requested_time: str


@dataclass(frozen=True, slots=True)
class StaffPaymentTask:
    id: str
    status: str
    appointment_status: str
    customer_name: str
    confirmed_time: str
    extracted_amount: str
    extracted_payee: str
    shows_success: bool
    confidence: float
    reasons: tuple[str, ...]
    evidence_path: Path


@dataclass(frozen=True, slots=True)
class StaffHandoffMessage:
    actor: str
    text: str


@dataclass(frozen=True, slots=True)
class StaffHandoffTask:
    id: str
    status: str
    owner: str
    handoff_status: str
    reason: str
    customer_name: str
    customer_phone: str
    appointment_status: str
    appointment_time: str
    messages: tuple[StaffHandoffMessage, ...]
    conversation_id: str = ""


@dataclass(frozen=True, slots=True)
class PaymentCodes:
    wechat: Path
    alipay: Path

    def validate(self) -> None:
        if not self.wechat.is_file():
            raise ValueError("WeChat payment code image is missing")
        if not self.alipay.is_file():
            raise ValueError("Alipay payment code image is missing")


class StaffAppointmentStore(Protocol):
    def load_time_task_view(self, task_id: str) -> StaffTimeTask: ...

    def approve_time_task(
        self,
        task_id: str,
        staff_id: str,
        payment_codes: PaymentCodes,
    ) -> None: ...

    def reject_time_task(self, task_id: str, staff_id: str) -> None: ...

    def load_payment_task_view(self, task_id: str) -> StaffPaymentTask: ...

    def approve_payment_task(self, task_id: str, staff_id: str) -> None: ...

    def reject_payment_task(self, task_id: str, staff_id: str) -> None: ...

    def load_handoff_task_view(self, task_id: str) -> StaffHandoffTask: ...

    def activate_handoff_task(self, task_id: str, staff_id: str) -> None: ...

    def return_handoff_task(self, task_id: str, staff_id: str, summary: str) -> None: ...

    def queue_handoff_reply(self, task_id: str, staff_id: str, text: str) -> None: ...

    def active_handoff_task_ids_for_staff(self, staff_id: str) -> tuple[str, ...]: ...


class NativeHandoffGateway(Protocol):
    def assign_to_staff(self, conversation_id: str) -> str: ...

    def return_to_ai(self, conversation_id: str) -> None: ...


class StaffAppointmentService:
    def __init__(
        self,
        store: StaffAppointmentStore,
        payment_codes: PaymentCodes,
        native_handoff_gateway: NativeHandoffGateway | None = None,
    ) -> None:
        self.store = store
        self.payment_codes = payment_codes
        self.native_handoff_gateway = native_handoff_gateway

    def get_time_task(self, task_id: str) -> StaffTimeTask:
        if not task_id.strip():
            raise ValueError("task id is required")
        return self.store.load_time_task_view(task_id)

    def approve_time(self, task_id: str, staff_id: str) -> None:
        if not task_id.strip():
            raise ValueError("task id is required")
        if not staff_id.strip():
            raise ValueError("staff id is required")
        self.payment_codes.validate()
        self.store.approve_time_task(task_id, staff_id, self.payment_codes)

    def reject_time(self, task_id: str, staff_id: str) -> None:
        if not task_id.strip():
            raise ValueError("task id is required")
        if not staff_id.strip():
            raise ValueError("staff id is required")
        self.store.reject_time_task(task_id, staff_id)

    def get_payment_task(self, task_id: str) -> StaffPaymentTask:
        if not task_id.strip():
            raise ValueError("task id is required")
        return self.store.load_payment_task_view(task_id)

    def approve_payment(self, task_id: str, staff_id: str) -> None:
        if not task_id.strip() or not staff_id.strip():
            raise ValueError("task id and staff id are required")
        self.store.approve_payment_task(task_id, staff_id)

    def reject_payment(self, task_id: str, staff_id: str) -> None:
        if not task_id.strip() or not staff_id.strip():
            raise ValueError("task id and staff id are required")
        self.store.reject_payment_task(task_id, staff_id)

    def get_handoff_task(self, task_id: str) -> StaffHandoffTask:
        if not task_id.strip():
            raise ValueError("task id is required")
        return self.store.load_handoff_task_view(task_id)

    def activate_handoff(self, task_id: str, staff_id: str) -> None:
        if not task_id.strip() or not staff_id.strip():
            raise ValueError("task id and staff id are required")
        task = self.get_handoff_task(task_id)
        if task.owner == "human":
            return
        assigned_staff_id = staff_id
        if self.native_handoff_gateway is not None:
            if not task.conversation_id:
                raise ValueError("handoff conversation is missing")
            try:
                assigned_staff_id = self.native_handoff_gateway.assign_to_staff(task.conversation_id)
            except Exception as error:
                raise ValueError("无法转接到企业微信原生客服，请稍后重试") from error
        self.store.activate_handoff_task(task_id, assigned_staff_id)

    def return_handoff(self, task_id: str, staff_id: str, summary: str) -> None:
        if not task_id.strip() or not staff_id.strip():
            raise ValueError("task id and staff id are required")
        if not summary.strip():
            raise ValueError("交接摘要不能为空")
        task = self.get_handoff_task(task_id)
        if task.status == "resolved" and task.owner == "ai":
            return
        if task.status != "open" or task.owner != "human":
            raise ValueError("会话当前不在人工接管中")
        if self.native_handoff_gateway is not None:
            if not task.conversation_id:
                raise ValueError("handoff conversation is missing")
            try:
                self.native_handoff_gateway.return_to_ai(task.conversation_id)
            except WeComSessionStateTransitionUnavailable:
                # Only a confirmed ended native session may resume local AI ownership.
                pass
            except Exception as error:
                raise ValueError("无法交还 AI，请稍后重试") from error
        self.store.return_handoff_task(task_id, staff_id, summary.strip())

    def send_handoff_reply(self, task_id: str, staff_id: str, text: str) -> None:
        text = text.strip()
        if not task_id.strip() or not staff_id.strip():
            raise ValueError("task id and staff id are required")
        if not text:
            raise ValueError("回复内容不能为空")
        if len(text) > 2000:
            raise ValueError("回复内容不能超过2000字")
        self.store.queue_handoff_reply(task_id, staff_id, text)

    def active_handoffs_for_staff(self, staff_id: str) -> tuple[StaffHandoffTask, ...]:
        if not staff_id.strip():
            raise ValueError("staff id is required")
        return tuple(
            self.store.load_handoff_task_view(task_id)
            for task_id in self.store.active_handoff_task_ids_for_staff(staff_id)
        )
