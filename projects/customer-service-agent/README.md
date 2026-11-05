# 企业级智能客服 Agent

将大模型接入真实客服工作流，重点解决消息可靠性、业务动作约束与 AI / 人工协同，而不只是生成聊天回复。

## 项目背景

原项目面向线下服务门店：顾客咨询、偏好收集、商品推荐和预约意向需要与店员确认衔接。模型能组织语言，但不能自行确认库存、承诺档期或把付款截图视为银行到账。

完整系统使用 Python 3.12、DeepSeek 兼容接口、企业微信加密回调与客服 API、Markdown 知识库分块检索、SQLite、RapidOCR、Docker Compose 和 Caddy。

**本目录是最新生产项目核心模块的脱敏节选，不是完整部署包。** 包名、商家名称、商品标识与测试数据已泛化；未包含企业知识、完整提示词、员工 Web 界面、OCR 模型或生产配置。

## 技术亮点

| 问题 | 实现 | 代码入口 |
| --- | --- | --- |
| 回调重试、进程异常与发送失败 | SQLite 持久化 inbox/outbox、幂等处理、有限重试与任务恢复 | [消息管线](src/service_agent/application/message_pipeline.py)、[存储实现](src/service_agent/infrastructure/sqlite_message_store.py) |
| AI 与店员同时回复 | 明确所有权，生成与发送阶段检查人工状态 | [会话状态机](src/service_agent/domain/conversations.py) |
| 本地交接成功但平台仍拒绝发送 | 查询原生状态；必要时结束并核验会话；失败不假报成功 | [企业微信适配器](src/service_agent/infrastructure/wecom_api.py)、[交接服务](src/service_agent/application/staff_appointments.py) |
| 企业事实不能靠模型猜 | Markdown 按标题分块、标题与正文加权检索 | [知识检索](src/service_agent/infrastructure/markdown_knowledge.py) |
| 模型不能直接执行副作用 | 结构化决策契约与独立业务状态机 | [决策模型](src/service_agent/domain/dialogue.py)、[预约状态机](src/service_agent/domain/appointments.py) |
| 平台回调与员工操作安全 | 签名校验、AES 解密、过期签名链接与 CSRF 工具 | [回调加解密](src/service_agent/infrastructure/wecom_callback_crypto.py)、[员工签名](src/service_agent/application/staff_access.py) |

## 离线演示

```bash
python demo.py
```

无需安装第三方依赖，也不读取 `.env`。演示依次检查：知识检索、重复消息去重、真实 SQLite 出站任务、发送前切换人工后取消 AI 消息，以及带摘要交还 AI 的领域状态转换。回答为本地确定性演示，发送目标为内存记录器，不调用大模型或企业微信。

## 回归测试

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

测试使用临时目录、合成商品和伪造传输层，不访问生产数据库或发送真实微信消息。具体范围见 [验证记录](docs/validation.md)。

## 深入阅读

- [技术架构与设计取舍](docs/architecture.md)：完整系统与公开模块的边界。
- [工程案例](docs/engineering-cases.md)：人工交接状态不一致与不受支持的回复承诺。
- [验证与限制](docs/validation.md)：已验证内容、未完成能力与测试解释。

## 已知限制

当前检索是词项加权匹配，不是向量检索；OCR 不是支付到账核验；持久化队列不等于端到端严格 exactly-once。本仓库不提供线上管理入口、服务器连接信息或能够直接接管真实客服的配置。

离线演示中的企业资料和商品均为虚构，真实录屏与原始企业资料不公开。
