**ADR-0005: 事件总线不引入 VOSR2 的 EventBus（保留现有双层结构）**

- **状态**: Implemented
- **日期**: 2026-10-28
- **决策者**: 项目维护者（双向吸收计划方向二 · 项 D）

---

# 背景与问题

姊妹仓 VOSR2-Lite 用单一 `EventBus`（`app/integrated_app/event_bus.py`，约 60 行）
承担全部 SSE 推送：全局事件与单任务进度都走同一个总线，订阅者各持一个
`asyncio.Queue`。本仓现有两套：

| 组件 | 位置 | 职责 |
|---|---|---|
| `event_bus` | `routes/system/sse.py` | 全局事件广播（`/api/sse/events`） |
| `TaskEventBus` | `services/task_events.py` | 按 `task_id` 分片的任务进度推送 |

计划里列为「待评估是否移植」，因为担心本仓存在重复实现、或 VOSR2 的做法更简洁。

# 决策

**不移植。** 本仓现状是 VOSR2 方案的超集，引入它属于能力倒退。

逐项对比（`services/task_events.py` vs VOSR2 `event_bus.py`）：

| 维度 | SeedVR2 `TaskEventBus` | VOSR2 `EventBus` |
|---|---|---|
| 分片 | 按 `task_id` 独立订阅者集合，避免广播风暴 | 全局单集合，所有订阅者收全量事件 |
| 背压 | 每队列上限 64，满时丢旧事件并告警 | 队列无上限（`asyncio.Queue()`），慢消费者会无界增长 |
| 迟到订阅 | 最终态缓存 + 60s TTL，任务结束后重连仍能拿到结果 | 无缓存，错过即丢 |
| 资源回收 | `clear_task` + `cleanup_expired` 显式清理 | 依赖 `WeakSet` 自动回收 |
| 线程安全 | `threading.Lock` 保护，推理线程可直接 publish | 直接遍历订阅者集合，靠 GIL 侥幸 |
| 全局事件 | 另有 `routes/system/sse.py` 的 `event_bus` | 同一总线兼任 |

VOSR2 的单总线在「一个任务、一个用户、本机」的定位下够用；本仓要同时处理
批量队列、文件夹扫描、多标签页与桌面壳，缺分片与背压会直接变成内存泄漏和
串台（A 任务的进度推到 B 任务的页面）。

# 实施影响

- 无代码改动。
- 反向建议（记入跨仓对照）：VOSR2-Lite 若将来支持批量/多标签，应借鉴本仓的
  `task_events` 分片 + 背压 + 最终态 TTL，而不是继续给全局总线加特例。

# 可回滚路径与待验证项

- 纯决策记录，无回滚成本。
- 待验证（低优先级）：`TaskEventBus` 的 64 条队列上限在「进度回调极密集
  （每帧一次）」场景下是否丢帧过多；若前端出现进度跳跃，考虑提高上限或
  改为按时间节流而非按条数丢弃。
