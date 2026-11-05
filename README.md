# 项目作品集

用于技术面试和项目交流的个人项目展示仓库。按真实应用场景介绍项目，提供原版业务源码、功能说明、架构与测试。

## 项目目录

| 项目 | 应用场景 | 能做什么 | 技术栈 |
| --- | --- | --- | --- |
| [Forthatday 企业微信智能客服](projects/customer-service-agent/README.md) | 婚纱工作室的到店前咨询、试纱预约和店员协同 | 自动问答、款式推荐与发图、预约确认、付款截图识别、改期取消、人工接管、库存与预约管理、官网预约申请 | Python、DeepSeek、企业微信 API、Markdown 检索、SQLite、RapidOCR、Docker |
| [工程文件翻译工作台](projects/engineering-translation/) | 英语、马来语工程文件译中 | 原位写入、数字单位核验、断点续跑；[免密码进入真实工作台](https://api.forthatday-studio.com/translate-interview) | 模型翻译、SQLite 队列、逐页检查点、中文预览与原格式导出 |
| [婚纱工作室官网（V13 原版）](projects/bridal-studio-website/) | 款式展示与预约申请 | 滚动时间轴、响应式布局、预约弹窗、提交状态与幂等重试；[原版官网](https://api.forthatday-studio.com/bridal/) | 原生前端、CSS、JavaScript、dialog |
| [Amoment 胶片工作室](projects/amoment/) | 照片胶片风格模拟 | 3D 相机与胶片选择、本地调色、原图对比、相纸与胶片边框、JPG 导出 | Three.js、WebGL、Canvas、JavaScript |

每个项目独立放在 `projects/` 下；后续继续添加，不把计划中的工作写成已完成成果。

## Forthatday：从咨询到到店试纱

顾客在微信里询问试纱规则、门店位置或婚纱款式，AI 根据企业知识回答并了解偏好。希望到店时，系统收集预约意向并通知店员确认；确认后发送付款码，识别顾客提交的付款截图，符合业务规则时确认预约，不确定时由店员复核。

投诉、退款、特殊价格和无法确认的情况交给店员。人工接管后 AI 停止自动回复，员工处理完后填写摘要，再按平台状态协调交还 AI。员工后台同时提供库存、实体件、尺码、档期、预约日历和官网申请管理。

**查看：[应用场景与完整功能](projects/customer-service-agent/README.md) · [原版源码](projects/customer-service-agent/src/forthatday_agent/) · [架构](projects/customer-service-agent/docs/architecture.md) · [工程案例](projects/customer-service-agent/docs/engineering-cases.md)**

## 客服项目原版范围

- 保留原项目 `forthatday_agent` 包名、业务实现、提示词、员工界面、知识检索和平台适配，不再以泛化后的代码节选替代。
- 包含原版服务启动脚本、数据库迁移、配置模板、Docker 文件和公开业务知识。
- 业务源码与当前本地 `agent_v2/src/` 按文件 SHA-256 核对；只在历史测试中替换一处私人联系方式。
- 真实密钥、客户数据库、付款凭证、商品图片、内部品牌清单、服务器连接资料不属于可公开源码，仍不提交。

## 本地验证

```bash
git clone https://github.com/niko-97XQ/project-portfolio.git
cd project-portfolio/projects/customer-service-agent
python -m venv .venv
```

激活虚拟环境后执行：

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

测试使用隔离数据和模拟接口，不会替你发送微信消息。真实运行还需要自己的企业微信授权、模型凭证和私有业务资料，见项目介绍中的配置说明。客服项目当前未启用 GitHub Actions 自动测试。

2026-11-05，本仓库原版业务代码的 162 项本地回归全部通过。

## 新增项目

在 `projects/` 下添加独立目录，参考 [项目介绍模板](templates/project/README.md)，补齐应用场景、已实现功能、技术方案、代码、验证和限制，并更新上面的目录。

公开边界见 [说明](SECURITY.md)。仓库未附加通用开源许可证；公开可见不等于授予任意商业复用权限。
