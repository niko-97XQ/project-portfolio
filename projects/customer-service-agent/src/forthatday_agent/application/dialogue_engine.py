from __future__ import annotations

from dataclasses import dataclass, replace
import json
import re
from typing import Any, Protocol

from forthatday_agent.application.action_policy import ActionContext, ActionPolicy
from forthatday_agent.application.dialogue_safety import DialogueSafetyPolicy, promises_location_delivery
from forthatday_agent.domain.dialogue import CatalogSignal, DialogueDecision
from forthatday_agent.domain.memory import ConversationMemory
from forthatday_agent.domain.messages import StoredMessage


SYSTEM_PROMPT = """你是 Forthatday Studio 的线上婚纱顾问。
主体回复必须结合当前完整上下文自然生成，不复读固定模板。
先接住顾客当前最关心的事情，再自然推进下一步；句子简短，像微信聊天。
不得透露精确婚纱成交价、内部型号、未经店员确认的档期或特殊折扣。
公共价位也只能使用单个模糊锚点，例如“5k 左右”或“4k+”；不得输出起止价格区间。
只回答顾客当前询问的主题。顾客只问款式时，不要主动附带价格、试纱费或预约规则。
顾客问门店位置时，直接回复知识库中已核实的完整文字地址，包含门牌和室号；不知道时请店员核实，不得编造“市中心”等位置。
当前没有发送定位卡片、导航链接或地图图片的动作，不得承诺发定位、发地图或导航链接。顾客只问地址时，不附带预约确认或留款承诺。
不要在回复中复述顾客的完整手机号；确认已收到即可。
顾客提出日期或时间时，绝对不能用“可以的”“可以约”“有档期”“没问题”；只能说先记录并向店员确认。
试纱费、预约、付款、改期、取消和人工接管只能提出白名单动作，由程序执行。
客户明确说出的事实可写入 memory_updates；推测偏好必须标记 ai_inferred 且置信度低于1。
只返回符合约定结构的 JSON 对象。"""

EXACT_PRICE_REQUEST = re.compile(
    r"(?:(?:具体|准确|到底|最低|实价).{0,8}(?:多少钱|价格|价位)|"
    r"(?:多少钱|价格|价位).{0,8}(?:具体|准确|实价))"
)
IMAGE_SEND_INTENT = re.compile(
    r"(?:发|看看|看一下|推荐).{0,12}(?:图片|照片|图)|(?:图片|照片|图).{0,12}(?:发|看看|看一下)"
)
STYLE_REQUEST = re.compile(r"(?:款式|风格|婚纱册|图册|相册|小红书|朋友圈)")
PRICE_OR_FEE_REPLY = re.compile(
    r"(?:价格|价位|试纱费|\d+(?:\.\d+)?\s*[kKwW]\+?|\d{2,5}\s*元)"
)
DATE_REQUEST = re.compile(
    r"(?:今天|明天|后天|周[一二三四五六日天]|星期[一二三四五六日天]|"
    r"[一二三四五六七八九十冬腊正\d]{1,3}月[一二三四五六七八九十廿三\d]{1,3}[号日])"
)
SPECIFIC_TIME_REQUEST = re.compile(
    r"(?:早上|上午|中午|下午|傍晚|晚上|[一二两三四五六七八九十\d]{1,3}点|"
    r"(?:[01]?\d|2[0-3])[:：][0-5]\d)"
)
TIME_QUESTION = re.compile(r"(?:几点|什么时间|哪个时间|希望.{0,6}时间|时间段)")
CONTACT_QUESTION = re.compile(r"(?:姓名|名字|怎么称呼|电话|手机号|联系方式)")
CUSTOMER_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
STORE_LOCATION_REQUEST = re.compile(
    r"(?:门店|店铺|工作室|你们|你家|实体店).{0,12}(?:在哪|哪里|哪儿|地址|位置|定位)|"
    r"(?:门店|到店|具体|详细|完整)地址|(?:发|给).{0,8}(?:地址|定位)|怎么(?:走|去|到店)|"
    r"(?:^|[，。！？])地址(?:[呢呀啊？?]|$)"
)
LOCATION_UNASKED_RESERVATION = re.compile(r"(?:留好|预留|留款|锁档|锁定|保留.{0,6}(?:款|婚纱))")


class StructuredChatModel(Protocol):
    def generate(self, system_prompt: str, input_data: dict[str, Any]) -> dict[str, Any]: ...


class DialogueContextProvider(Protocol):
    def load_memory(self, conversation_id: str) -> ConversationMemory: ...

    def recent_messages(
        self,
        conversation_id: str,
        limit: int = 20,
    ) -> tuple[StoredMessage, ...]: ...


class KnowledgeProvider(Protocol):
    def search(self, query: str, limit: int = 5) -> list[dict[str, str]]: ...


@dataclass(frozen=True, slots=True)
class DialogueContext:
    memory: ConversationMemory
    recent_messages: tuple[StoredMessage, ...]
    knowledge: tuple[dict[str, str], ...]
    appointment_state: dict[str, Any]


class UnifiedDialogueEngine:
    """The only component allowed to generate normal customer-facing replies."""

    def __init__(
        self,
        model: StructuredChatModel,
        context_provider: DialogueContextProvider,
        knowledge_provider: KnowledgeProvider,
        safety_policy: DialogueSafetyPolicy | None = None,
        action_policy: ActionPolicy | None = None,
    ) -> None:
        self.model = model
        self.context_provider = context_provider
        self.knowledge_provider = knowledge_provider
        self.safety_policy = safety_policy or DialogueSafetyPolicy()
        self.action_policy = action_policy or ActionPolicy()
        self.last_decision: DialogueDecision | None = None

    def decide(
        self,
        conversation_id: str,
        messages: tuple[StoredMessage, ...],
        appointment_state: dict[str, Any] | None = None,
    ) -> DialogueDecision:
        memory = self.context_provider.load_memory(conversation_id)
        recent_messages = _merge_messages(
            self.context_provider.recent_messages(conversation_id, limit=20),
            messages,
            limit=20,
        )
        query = "\n".join(message.text_content for message in messages if message.text_content)
        knowledge_query = _knowledge_query(recent_messages, memory)
        knowledge = tuple(self.knowledge_provider.search(knowledge_query, limit=8))
        if appointment_state is None:
            state_loader = getattr(self.context_provider, "load_appointment_state", None)
            appointment_state = state_loader(conversation_id) if state_loader else {}
        context = DialogueContext(
            memory=memory,
            recent_messages=recent_messages,
            knowledge=knowledge,
            appointment_state=appointment_state or {},
        )
        input_data = _prompt_data(context)
        output = self.model.generate(SYSTEM_PROMPT, input_data)
        decision: DialogueDecision | None = None
        violations: list[str] = []
        try:
            decision = _materialize_image_recommendations(
                DialogueDecision.from_model_output(output),
                knowledge,
            )
        except (TypeError, ValueError) as error:
            violations.append(f"invalid_output_schema: {error}")
        if decision is not None:
            violations.extend(self._decision_violations(decision, context.appointment_state))
            violations.extend(_input_business_violations(decision, query))
            violations.extend(_location_violations(decision, query, context.knowledge))
            violations.extend(_catalog_violations(decision, context.knowledge))

        if violations:
            repair_input = dict(input_data)
            repair_input["repair_request"] = {
                "violations": violations,
                "previous_output": output,
                "instruction": _repair_instruction(violations),
            }
            repaired_output = self.model.generate(SYSTEM_PROMPT, repair_input)
            decision = _materialize_image_recommendations(
                DialogueDecision.from_model_output(repaired_output),
                knowledge,
            )
            repaired_violations = self._decision_violations(decision, context.appointment_state)
            repaired_violations.extend(_input_business_violations(decision, query))
            repaired_violations.extend(_location_violations(decision, query, context.knowledge))
            repaired_violations.extend(_catalog_violations(decision, context.knowledge))
            if repaired_violations:
                raise ValueError(
                    "model decision failed validation: " + ", ".join(repaired_violations)
                )
        if decision is None:
            raise ValueError("model did not produce a dialogue decision")
        self.last_decision = decision
        return decision

    def _decision_violations(
        self,
        decision: DialogueDecision,
        appointment_state: dict[str, Any],
    ) -> list[str]:
        violations = list(
            self.safety_policy.validate(decision, appointment_state).violations
        )
        action_context = ActionContext(
            appointment_status=str(appointment_state.get("status") or ""),
            has_confirmed_time=bool(appointment_state.get("staff_time_approved")),
            has_payment_evidence=bool(appointment_state.get("has_payment_evidence")),
        )
        for action in decision.proposed_actions:
            result = self.action_policy.validate(action, action_context)
            if not result.accepted:
                violations.append(f"invalid_action:{action.type.value}: {result.reason}")
        return violations

    def reply(
        self,
        conversation_id: str,
        messages: tuple[StoredMessage, ...],
    ) -> DialogueDecision:
        return self.decide(conversation_id, messages)


def _prompt_data(context: DialogueContext) -> dict[str, Any]:
    return {
        "conversation_memory": context.memory.as_prompt_data(),
        "recent_messages": [
            {
                "id": message.id,
                "actor": message.actor.value,
                "type": message.message_type,
                "content": message.text_content,
                "occurred_at": message.occurred_at.isoformat(),
            }
            for message in context.recent_messages
        ],
        "appointment_state": context.appointment_state,
        "retrieved_knowledge": list(context.knowledge),
        "output_schema": {
            "reply": "string",
            "memory_updates": [
                {
                    "key": "allowed profile field",
                    "value": "any JSON value",
                    "source": "customer_explicit | ai_inferred",
                    "evidence_message_id": "message id",
                    "confidence": "0..1",
                }
            ],
            "proposed_actions": [
                {
                    "type": (
                        "request_time_confirmation | send_payment_codes | "
                        "verify_payment_evidence | request_reschedule_confirmation | "
                        "request_cancellation_confirmation | notify_late_arrival | "
                        "handoff_to_human"
                    ),
                    "arguments": "object",
                }
            ],
            "catalog_signals": [
                {
                    "event_type": "inquiry | recommended | try_on | lost_demand",
                    "catalog_ref": "only a catalog_ref present in retrieved_knowledge",
                }
            ],
            "needs_human": "boolean",
            "reason": "string",
        },
        "allowed_memory_fields": [
            "customer_name",
            "customer_phone",
            "wedding_date",
            "venue",
            "budget_range",
            "style_preferences",
        ],
        "memory_rules": [
            "预约日期和时间不是画像字段，不得写入 memory_updates",
            "不要创造 allowed_memory_fields 以外的字段",
            "客户明确事实必须引用 recent_messages 中真实存在的 evidence_message_id",
        ],
        "workflow_rules": [
            "每轮推荐 3 个不同款式并各发送 1 张主图；若只检索到不足 3 个可靠结果，则只发送可靠结果，不得凑数。",
            "实际向顾客推荐并准备发送图片的款式必须记录 recommended；不要把未介绍的检索结果记录为 recommended。",
            "只要 reply 表示现在要发图片或给顾客看具体款式，就必须为实际发送的最多 3 款创建 recommended 记录；程序会根据记录自动发送主图。",
            "不得声称图片发送失败，也不得承诺发送图片链接；顾客说没看到或要求重发时，简短确认即可，程序会重发上一张商品图。",
            "catalog_ref 仅用于 catalog_signals，绝不能出现在给顾客的 reply 中",
            "顾客询问具体款式时记录 inquiry；回复中实际推荐具体款式时记录 recommended",
            "没有检索到 customer_safe_catalog 时不得编造款式、品牌、租售属性或库存信息",
            "除非检索内容明确说明顾客已主动提到该品牌，否则不要主动说出品牌名称",
            "任何款式的库存与档期都必须结合实体库存并由店员确认，不得直接承诺",
            "客户只提供宽泛时段（如周六下午）时，先询问希望几点到，不要承诺稍后回复",
            "客户只提供日期、没有具体到店时间时，只追问希望几点到；取得日期和时间后再收集姓名和电话",
            "只有客户给出可供店员判断的具体日期和时间后，才提出 request_time_confirmation",
            "只有已取得客户姓名、电话、具体日期和时间后，才提出 request_time_confirmation",
            "handoff_to_human 的 arguments 必须包含非空 reason",
            "询问门店位置时使用 store-location.md 提供的完整文字地址；不承诺发送定位、地图图片或导航链接。",
            "只问地址就只回答地址，不重复历史预约时间、不声称店员已经留好款式。",
        ],
    }


def _merge_messages(
    history: tuple[StoredMessage, ...],
    current: tuple[StoredMessage, ...],
    *,
    limit: int,
) -> tuple[StoredMessage, ...]:
    by_id = {message.id: message for message in history}
    by_id.update({message.id: message for message in current})
    ordered = sorted(by_id.values(), key=lambda item: (item.occurred_at, item.id))
    return tuple(ordered[-limit:])


def _repair_instruction(violations: list[str]) -> str:
    instructions = ["重新生成完整 JSON，并修正全部违规项。"]
    if "unapproved_time_commitment" in violations:
        instructions.append(
            "店员尚未确认档期。不得说可以来、能来、可以约、有档期或时间没问题；"
            "若客户只说宽泛时段，要直接询问希望几点到，并说明取得具体时间后再向店员确认。"
        )
    if "unsupported_followup_promise" in violations:
        instructions.append(
            "当前没有已创建的后续动作，不得承诺稍后回复或晚点联系；请在本条消息中询问下一项必要信息。"
        )
    if "unsupported_location_delivery" in violations or "missing_store_address" in violations:
        instructions.append(
            "直接在本条 reply 中给出 retrieved_knowledge 的 store-location.md 中完整门店地址，包含门牌和室号；"
            "删除发定位、地图图片、位置卡片和导航链接的承诺，不得仅说市中心，也不得编造位置。"
            "没有可靠地址时请店员核实，不得虚构已发送。"
        )
    if "unasked_location_reservation" in violations:
        instructions.append("顾客本轮只问门店位置。只保留文字地址，删除预约日期时间、到店承诺和预留款式的内容。")
    if any(item.startswith("invalid_action:") for item in violations):
        instructions.append(
            "按 violations 中的说明补齐或删除不可执行动作；人工接管动作的 arguments 必须有非空 reason。"
        )
    if any(item.startswith("invalid_output_schema:") for item in violations):
        instructions.append("严格使用 output_schema 和白名单字段，不得发明字段或动作。")
    if "exact_price_requires_human" in violations:
        instructions.append(
            "客户在追问具体婚纱成交价。不得编造价格或定价原因；请说明具体方案由店员沟通，"
            "设置 needs_human=true，并提出带非空 reason 的 handoff_to_human 动作。"
        )
    if "unasked_pricing" in violations:
        instructions.append(
            "客户当前只问款式，没有问价格或试纱。只介绍风格、款式查看渠道并自然询问偏好；"
            "删除价格、试纱费、件数、时长和预约规则。"
        )
    if "precise_price_range_exposed" in violations:
        instructions.append(
            "不得输出任何起止价格区间。公共价位只能使用单个模糊锚点，例如“5k左右”或“4k+”。"
        )
    if "missing_preferred_time_question" in violations or "premature_contact_collection" in violations:
        instructions.append(
            "客户目前只给了日期，还没有具体到店时间。本条只询问希望几点到；"
            "不要提前索要姓名、电话，也不要声称已向店员确认。"
        )
    if "customer_phone_repeated" in violations:
        instructions.append("已经收到联系方式即可，不得在回复中复述顾客的完整手机号。")
    if any(item.startswith("unknown_catalog_ref:") for item in violations):
        instructions.append(
            "catalog_signals 只能引用 retrieved_knowledge 中 customer_safe_catalog 提供的 catalog_ref。"
        )
    if any(item.startswith("catalog_ref_exposed:") for item in violations):
        instructions.append(
            "删除 reply 中的内部 catalog_ref；可使用款式名称描述，但内部款号只能留在 catalog_signals。"
        )
    if "too_many_catalog_recommendations" in violations:
        instructions.append(
            "每轮只保留最匹配的 3 款 recommended 记录，其余款式等顾客继续询问时再推荐。"
        )
    if "image_send_intent_without_recommendation" in violations:
        instructions.append(
            "reply 已表示要立即发图，必须从 retrieved_knowledge 中选择最多 3 个最匹配款式，"
            "为每款添加 recommended catalog_signal；不要只记录 inquiry。"
        )
    return "".join(instructions)


def _input_business_violations(
    decision: DialogueDecision,
    customer_text: str,
) -> list[str]:
    violations: list[str] = []
    if EXACT_PRICE_REQUEST.search(customer_text):
        has_handoff = any(
            action.type.value == "handoff_to_human" for action in decision.proposed_actions
        )
        if not decision.needs_human or not has_handoff:
            violations.append("exact_price_requires_human")
    if STYLE_REQUEST.search(customer_text) and not re.search(
        r"(?:价格|价位|多少钱|预算|试纱)", customer_text
    ):
        if PRICE_OR_FEE_REPLY.search(decision.reply):
            violations.append("unasked_pricing")
    if DATE_REQUEST.search(customer_text) and not SPECIFIC_TIME_REQUEST.search(customer_text):
        if not TIME_QUESTION.search(decision.reply):
            violations.append("missing_preferred_time_question")
        if CONTACT_QUESTION.search(decision.reply):
            violations.append("premature_contact_collection")
    if any(phone in decision.reply for phone in CUSTOMER_PHONE.findall(customer_text)):
        violations.append("customer_phone_repeated")
    return violations


def _location_violations(
    decision: DialogueDecision,
    customer_text: str,
    knowledge: tuple[dict[str, str], ...],
) -> list[str]:
    if re.search(r"品牌定位|市场定位", customer_text) or not STORE_LOCATION_REQUEST.search(customer_text):
        return []
    violations: list[str] = []
    addresses = [
        match.group(1).strip()
        for item in knowledge if item.get("source") == "store-location.md"
        for match in re.finditer(r"^门店地址[：:]\s*(.+)$", str(item.get("text") or ""), re.MULTILINE)
    ]
    normalized_reply = re.sub(r"[\s，,。]", "", decision.reply)
    if addresses and not any(re.sub(r"[\s，,。]", "", address) in normalized_reply for address in addresses):
        violations.append("missing_store_address")
    if not DATE_REQUEST.search(customer_text) and not re.search(r"预约|时间|留款|预留|安排|确认", customer_text):
        if DATE_REQUEST.search(decision.reply) or LOCATION_UNASKED_RESERVATION.search(decision.reply):
            violations.append("unasked_location_reservation")
    return violations


def _catalog_violations(
    decision: DialogueDecision,
    knowledge: tuple[dict[str, str], ...],
) -> list[str]:
    allowed_refs = {
        item.get("catalog_ref", "").strip().lower()
        for item in knowledge
        if item.get("source") == "customer_safe_catalog" and item.get("catalog_ref")
    }
    violations: list[str] = []
    recommended_refs = [
        signal.catalog_ref
        for signal in decision.catalog_signals
        if signal.event_type == "recommended"
    ]
    if len(set(recommended_refs)) > 3:
        violations.append("too_many_catalog_recommendations")
    if allowed_refs and IMAGE_SEND_INTENT.search(decision.reply) and not recommended_refs:
        violations.append("image_send_intent_without_recommendation")
    for signal in decision.catalog_signals:
        if signal.catalog_ref not in allowed_refs:
            violations.append(f"unknown_catalog_ref:{signal.catalog_ref}")
    reply_lower = decision.reply.lower()
    for catalog_ref in allowed_refs:
        if catalog_ref and catalog_ref in reply_lower:
            violations.append(f"catalog_ref_exposed:{catalog_ref}")
    return violations


def _knowledge_query(
    messages: tuple[StoredMessage, ...],
    memory: ConversationMemory,
) -> str:
    customer_texts = [
        message.text_content.strip()
        for message in messages
        if message.actor.value == "customer" and message.text_content.strip()
    ]
    preference_values = [
        fact.value
        for fact in memory.facts
        if fact.key == "style_preferences" and fact.value
    ]
    preference_text = " ".join(
        json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
        for value in preference_values
    )
    return "\n".join([preference_text, *customer_texts[-6:]])[-1200:]


def _materialize_image_recommendations(
    decision: DialogueDecision,
    knowledge: tuple[dict[str, str], ...],
) -> DialogueDecision:
    if promises_location_delivery(decision.reply):
        return decision
    if not IMAGE_SEND_INTENT.search(decision.reply):
        return decision
    allowed_refs = [
        str(item.get("catalog_ref") or "").strip().lower()
        for item in knowledge
        if item.get("source") == "customer_safe_catalog"
        and item.get("catalog_ref")
        and item.get("image_available", "true") != "false"
    ]
    if not allowed_refs:
        return decision
    selected = [
        signal.catalog_ref
        for signal in decision.catalog_signals
        if signal.event_type == "recommended" and signal.catalog_ref in allowed_refs
    ]
    selected.extend(ref for ref in allowed_refs if ref not in selected)
    selected = selected[:3]
    other_signals = tuple(
        signal for signal in decision.catalog_signals if signal.event_type != "recommended"
    )
    count_text = {1: "一", 2: "两", 3: "三"}[len(selected)]
    return replace(
        decision,
        reply=f"我先给你看这{count_text}款，都是按你刚才说的偏好筛的。你看完告诉我更喜欢哪一张～",
        catalog_signals=other_signals
        + tuple(CatalogSignal("recommended", ref) for ref in selected),
    )
