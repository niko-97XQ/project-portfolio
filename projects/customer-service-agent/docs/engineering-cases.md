# 工程案例

本页解释原项目中的实际问题与修复思路，不公开客户记录或生产日志。

## 交还 AI 成功，但顾客收不到回复

员工操作页显示交还成功，之后顾客消息没有收到回复。沿“回调 → 入站 → 模型 → 出站 → 平台结果”排查，发现模型已经生成回答，错误发生在平台拒绝发送。

旧代码把原生状态转换被拒误认为会话已经结束，导致本地恢复 AI，但企微仍可能处于不能发送的接待状态。

修复先验证任务、人工所有权与摘要，再尝试平台转换；转换拒绝后查询真实状态。仍为人工时按支持的流程结束并确认状态，已结束时等待顾客下一条新消息；查询或结束失败则保留人工，不显示假成功，也不重发历史回复。

对应代码：[交接服务](../src/forthatday_agent/application/staff_appointments.py)、[企微适配器](../src/forthatday_agent/infrastructure/wecom_api.py)、[回归测试](../tests/test_handoff_return_regression.py)。

## 门店地址回复与工具能力不一致

模型曾用“我发你定位”代替具体地址，还带上不必要的预约与留款承诺。但项目没有定位卡片发送动作，话术不会自动产生平台能力。

修复补充已核实的 [地址知识](../knowledge/store-location.md)，在 [对话引擎](../src/forthatday_agent/application/dialogue_engine.py) 和 [安全规则](../src/forthatday_agent/application/dialogue_safety.py) 中检测不支持的承诺、地址不完整及无关预约留款，再让模型修复并重新校验。

对应测试：[门店地址回复](../tests/test_store_location.py)。生成文字和执行动作是两件事，不能让流畅话术冒充真实结果。

## 官网申请与已确认预约分离

官网表单接入员工后台时，没有直接创建已确认试纱预约，而是单独保存待联系申请。接收端处理字段、同源、体积、限流与幂等，员工操作处理登录、CSRF、并发版本和审计，避免表单直接触发收款、锁档或微信消息。

对应代码：[申请服务](../src/forthatday_agent/application/website_requests.py)、[存储](../src/forthatday_agent/infrastructure/website_request_store.py)、[测试](../tests/test_website_requests.py)。

## 讨论重点

- 为什么需要同时协调本地状态和外部平台状态？
- 为什么不能在回调请求内直接调用模型？
- 幂等和重试分别解决什么问题，什么情况下还需要对账？
- 如何防止模型越权承诺预约、库存或付款结果？
