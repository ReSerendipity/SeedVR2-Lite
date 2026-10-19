# Third-Party Notices（第三方组件声明）

> 更新日期：2026-09-02。本清单非穷尽：完整依赖以 `requirements.txt` / `requirements-lock.txt`
> 及安装环境的 `pip freeze` 为准；各组件许可以其官方仓库与包内 LICENSE 为准。

## 项目主许可

SeedVR2-lite 项目代码采用 [Apache License 2.0](LICENSE)。
模型权重与引擎许可见 [NOTICE](NOTICE)，不由本文件管理。

## 主要 Python 依赖（许可类型为常见归类，以各包 LICENSE 为准）

| 组件 | 常见许可类型 | 说明 |
|---|---|---|
| torch / torchvision / torchaudio | BSD-3-Clause | 推理框架 |
| fastapi | MIT | Web 框架 |
| uvicorn | BSD-3-Clause | ASGI 服务器 |
| pydantic / pydantic-core | MIT | 数据校验 |
| aiohttp | Apache-2.0 | 异步 HTTP 客户端 |
| numpy | BSD-3-Clause | 数值计算 |
| Pillow | HPND（PIL Software License） | 图像处理 |
| opencv-python-headless | Apache-2.0 | 视觉处理 |
| safetensors | Apache-2.0 | 模型权重加载 |
| einops | MIT | 张量重排 |
| transformers | Apache-2.0 | 模型库 |
| PyYAML | MIT | 配置解析 |
| htmx | BSD-3-Clause | 前端交互（vendored） |
| bootstrap / bootstrap-icons | MIT | 前端样式（vendored） |

## vendored 组件

### 推理内核（`model_lib/`、`common/`）

- 源自 SeedVR2 上游内核代码，许可遵循上游（见 [model_lib/SOURCE.md](model_lib/SOURCE.md) 与 [NOTICE](NOTICE)）

### 视频/图像修复模型

- SeedVR2 系模型权重由用户自行下载，许可以各模型卡为准（见 [NOTICE](NOTICE)）

---

*疑问或遗漏请通过 Issues 反馈。*
