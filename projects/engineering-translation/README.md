# 江河工程文件翻译工作台

[进入真实工作台 · 免密码](https://api.forthatday-studio.com/translate-interview)

上传自己的 PDF、Excel（XLSX）、TXT 或图片，自动识别混合语言，生成中文译文。PDF 可以指定页码；左右对照原图和译图，完成后可下载。DWG/DXF 请先导出 PDF。

**建议体验：** 在翻译设置中选择 Astra、Sol 6.1 或 DeepSeek，选快速或精细模式，然后上传一页工程图并点击「开始翻译」。预览来自本次真实模型调用。选定模型负责翻译、自查和修订；耗时随文件复杂度和模型响应而变。

面试入口运行正式工作台的界面、解析、排版与后台队列代码。浏览器自动取得独立访客会话，无需账号或密码。访客文件存放于单独服务，与公司员工文件隔离。模型密钥只在服务端；处理文件时，原文和需要识别的图像会提交给所选模型。公共体验有调用额度，请使用可以交给模型服务的文件。

| 代码入口 | 关注点 |
| --- | --- |
| [文件工作台](source/app/ui/full-document-workspace.tsx) | 上传、模型与模式选择、页码、双栏预览及下载 |
| [后台逐页流程](source/lib/page-pipeline.ts) | 从准备、翻译、写回、检查到修订；持久化检查点和中断恢复 |
| [PDF 写回](source/scripts/page-worker.py) | 原位排版、遮罩与图线保留、渲染和合并 |
| [模型提示词](source/lib/page-prompts.ts) | 工程术语、受保护内容、整页覆盖与修订指令 |
| [完整性](source/lib/page-completeness.ts) · [数字和单位](source/lib/checks.ts) | 区分条目写入与整页核对，发现不一致时报告疑点 |

仓库提供用于代码评审的实现节选。在线入口提供完整的访客翻译流程。规则通过不等于语义完全准确，也不能据此宣称比单次翻译提升固定百分比。离线规则测试可运行 `node --test projects/engineering-translation/checks.test.mjs`。
