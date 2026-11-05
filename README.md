# 项目作品集

面向技术面试与代码评审的个人项目索引。每个项目独立组织背景、技术方案、核心代码、验证方法与已知限制。

## 项目目录

| 项目 | 场景 | 技术重点 | 查看 |
| --- | --- | --- | --- |
| 企业级智能客服 Agent | 咨询接待与人工协同 | Python、企业微信、结构化模型决策、Markdown 检索、SQLite 持久化队列 | [项目介绍](projects/customer-service-agent/README.md) · [核心代码](projects/customer-service-agent/src/service_agent/) |
| 工程文件翻译工作台 | 英语、马来语工程文件译中 | 原位写入、数字单位核验、断点续跑 | [免登录体验](https://niko-97xq.github.io/project-portfolio/engineering-translation/) · [项目与代码](projects/engineering-translation/) |
| 婚纱工作室展示与预约网站 | 款式浏览与预约申请 | 原生前端、滚动交互、原生 dialog、HTTP 表单、SQLite 事务幂等与限流 | [免登录预览](https://niko-97xq.github.io/project-portfolio/bridal-studio/) · [项目与代码](projects/bridal-studio-website/README.md) |

这里只展示已经整理的项目，不将计划中的工作列为已完成成果。后续项目会继续添加到本表。

## 建议阅读顺序

1. 阅读项目介绍，了解问题、解决方案和实现边界。
2. 查看技术架构与设计取舍，理解为什么这样设计。
3. 运行离线演示和测试，再阅读对应代码。
4. 查看工程案例，讨论故障定位、状态一致性和改进方向。

第一个项目的入口：[技术架构](projects/customer-service-agent/docs/architecture.md)、[工程案例](projects/customer-service-agent/docs/engineering-cases.md)、[验证记录](projects/customer-service-agent/docs/validation.md)。

客服 Agent 公开节选有 61 项测试，婚纱网站有 18 项测试；两个 Python 项目共 79 项本地测试通过，都附带无需账号和密钥的隔离演示。

婚纱网站另附独立的本机页面与合成预约演示：[架构](projects/bridal-studio-website/docs/architecture.md)、[工程案例](projects/bridal-studio-website/docs/engineering-cases.md)、[验证与限制](projects/bridal-studio-website/docs/validation.md)。实时 AI 试穿未完成接入，不计入已完成能力。

## 快速体验

需要 Python 3.12；离线演示不需要模型 Key、企业微信账号或生产数据库。

```bash
git clone https://github.com/niko-97XQ/project-portfolio.git
cd project-portfolio/projects/customer-service-agent
python demo.py
```

演示使用真实核心模块和临时数据库，但回答来自明确标注的本地演示引擎，不代表线上模型或微信客户端联调。

## 组织方式

```text
projects/
  customer-service-agent/   首个项目：公开核心代码与隔离演示
  bridal-studio-website/    展示交互、预约策略与本机演示
templates/
  project/                 后续项目的中文介绍模板
scripts/
  check_public_content.py  公开内容基础检查
  run_checks.py            一键运行公开检查、测试与离线演示
```

新增项目时，在 `projects/` 下使用独立目录，并参考 [项目模板](templates/project/README.md) 补齐材料，无需改动已有项目代码。

安装项目测试依赖后，在仓库根目录运行 `python scripts/run_checks.py` 即可复现 Python 项目的检查。当前未添加自定义测试工作流，不将本地验证描述为云端 CI；GitHub Pages 的站点发布构建不等于这些测试已在云端执行。

## 公开范围

- 展示代码节选、合成测试数据、设计文档与离线演示，不是生产环境完整备份。
- 不发布密钥、客户记录、企业内部品牌清单、商品图片、付款码、服务器连接信息或生产运维记录。
- 未经权利确认的真实录屏和客户截图不随仓库发布。
- 没有将 Embedding、支付到账核验、高并发压测或未经统计的效果提升写成已完成能力。

更多说明见 [公开与隐私边界](SECURITY.md)。仓库未附加通用开源许可证；公开可见不等于授予任意商业复用权限。
