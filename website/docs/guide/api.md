# API 参考

SeedVR2-lite 基于 FastAPI 提供 REST API，启动后可在 <http://127.0.0.1:7870/docs> 查看完整的 Swagger UI。

::: tip 本文与代码同源
本文每一条路径都由 `tests/test_api_contract.py::test_documented_api_paths_exist`
与应用真实路由清单逐条比对——**写了却不存在的路径会让测试变红**。新增端点后请同步补表。
:::

非安全方法（`POST` / `PUT` / `DELETE`）受 CSRF 双提交保护：先发任意安全请求取得
`csrf_token` cookie，再在请求头带 `X-CSRF-Token: <同值>`，否则 403。

## 修复类

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/restore/` | 单文件上传修复（图片 / 视频），返回 `task_id` |
| GET | `/api/restore/{task_id}/progress` | 任务进度（SSE 事件驱动 + 轮询兜底） |
| GET | `/api/restore/{task_id}/result` | 任务结果信息（状态 / 输出路径 / 体积 / 错误） |
| GET | `/api/restore/{task_id}/download` | 下载修复产物 |
| POST | `/api/restore/{task_id}/cancel` | 取消进行中的任务 |
| GET | `/api/restore/scan-folder` | 扫描文件夹（白名单目录内） |

## 批量修复

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/restore/batch` | 文件夹批量修复，返回 `batch_id` |
| GET | `/api/restore/batch/{batch_id}/progress` | 批量进度（逐文件状态、成功 / 失败计数） |
| POST | `/api/restore/batch/{batch_id}/cancel` | 取消批量任务（停止后续文件） |
| POST | `/api/restore/batch/{batch_id}/retry` | 只重试本批中失败的文件 |

## 系统类

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/system/health` | 健康检查（系统 / 模型 / GPU 汇总） |
| GET | `/api/system/ping` | 存活探针（liveness，容器 HEALTHCHECK 使用） |
| GET | `/api/system/ready` | 就绪探针（readiness，模型预热中返回 503 + Retry-After） |
| GET | `/api/system/gpu` | GPU 状态（显存、SM 利用率、温度） |
| GET | `/api/system/gpu/system` | 系统级 GPU 汇总 |
| GET | `/api/system/gpu/vram-estimate` | 估算指定参数下的显存需求 |
| GET | `/api/system/gpu/recommend-params` | 推荐参数组合（精度 / BlockSwap / tile / 风险等级） |
| GET | `/api/system/model/status` | 模型加载状态 |
| POST | `/api/system/model/load` | 加载模型 |
| POST | `/api/system/model/unload` | 卸载模型 |
| POST | `/api/system/model/switch` | 切换模型尺寸 |
| GET | `/api/system/settings` | 读取配置 |
| POST | `/api/system/settings` | 更新配置 |
| GET | `/api/system/locales` | 可用语言清单 |
| POST | `/api/system/locale` | 切换界面语言 |
| GET | `/api/system/browse-dir` | 目录浏览（仅白名单内） |
| POST | `/api/system/open-explorer` | 在系统文件管理器中打开目录 |
| GET | `/api/sse/events` | SSE 实时推送（任务进度 / 模型状态） |

## 指标与可观测

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/system/metrics` | 运行时指标快照（JSON） |
| GET | `/api/system/metrics/inference` | 推理历史列表 |
| POST | `/api/system/metrics/reset` | 重置推理计数器（运维用） |
| GET | `/metrics` | Prometheus 文本暴露格式 |

## 历史记录

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/system/history` | 历史记录列表（分页 / 筛选 / 全文搜索） |
| GET | `/api/system/history/statistics` | 统计聚合（累计耗时 / 输出体积 / 平均耗时） |
| GET | `/api/system/history/{record_id}/download` | 下载该记录的产物 |
| POST | `/api/system/history/{record_id}/cancel` | 取消该记录关联的进行中任务 |
| GET | `/api/system/history/resolve` | 输出文件 → 任务反查（数据溯源） |
| DELETE | `/api/system/history/{record_id}` | 删除单条记录 |
| DELETE | `/api/system/history` | 批量清除历史记录 |

## 界面偏好

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/ui/preferences` | 读取全局偏好 |
| POST | `/api/ui/preferences` | 保存全局偏好 |
| POST | `/api/ui/preferences/reset` | 恢复默认偏好 |
| GET | `/api/ui/restore-preferences` | 读取修复页偏好 |
| POST | `/api/ui/restore-preferences` | 保存修复页偏好 |
| GET | `/api/ui/layout` | 界面布局状态 |
| GET | `/api/ui/parameters` | 参数定义与分组 |
| GET | `/api/ui/parameters/recommendations` | 推荐参数分组 |
| POST | `/api/ui/parameters/validate` | 参数合法性校验 |

## 引擎抽象层（外部集成）

Web UI 走上面的 `/api/restore/*`；下列端点是给 MCP / 外部客户端使用的引擎无关接口。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/engine/list` | 列出已注册引擎 |
| GET | `/api/engine/detect` | 引擎能力探测 |
| POST | `/api/engine/submit` | 提交修复任务 |
| GET | `/api/engine/task/{task_id}` | 查询任务状态 |

## 统一响应格式

```jsonc
// 成功
{ "success": true, "data": { "task_id": "..." } }
// 失败（所有错误——含 HTTPException、CSRF / 限流中间件、404——都走同一个信封）
{ "success": false, "error": { "code": "NOT_FOUND", "message": "任务不存在", "detail": {} } }
```

`/metrics`（Prometheus 文本暴露）不适用此信封。

::: warning 网络绑定
Web UI 默认仅绑定 `127.0.0.1`（`config.yaml` 中 `server.host`），不对外暴露。
请勿将 `server.host` 修改为 `0.0.0.0` 或公网 IP，详见 [安全与合规](./security.md)。
:::
