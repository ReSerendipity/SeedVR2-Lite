# Changelog

> **本地未分发引用**：以下历史条目里出现的 `AGENTS.md`、`docs/agents/`、`docs/project/`、`docs/plans/`、
> `docs/reports/`、`docs/_devarchive/`、`examples/`、`precheck.ps1` 均为维护者本地文件，未随仓库分发；
> 对外可执行的禁区与门禁口径见 `docs/CODING_STANDARDS.md` 第 5 节。历史记录按原样保留，不改写。

> 2026 Q3 及更早版本已分片到 [CHANGELOG-2026-Q3.md](CHANGELOG-2026-Q3.md)（C 轨，单片 ≤128 KiB）。

## [1.9.1] - 2026-11-18

### Fixed

* **安装包**：`scripts/build_desktop_bundle.ps1` 将根目录的 `configs_3b`、`configs_7b` 复制进数据卷 `app/`，修复安装后启动时报 `Config file not found: configs_3b/config.json`。

## [1.9.0] - 2026-11-18

### Added

* **魔搭公开源迁移**：主权重与 VAE 走 `Comfy-Org/SeedVR2` 官方公开仓；`pos_emb.pt` / `neg_emb.pt` 优先从安装包内置的 `scripts/bundle_assets/` 复制；运行时改为 `ReSerendipity/VOSR2Lite-Runtime`（与 VOSR2-Lite 共用 `runtime-v1`，Python 3.12.10 + torch 2.10.0+cu128）。
* **品牌视觉**：桌面壳换奶油浅色主题与绿色品牌图标。（`3c59f31`）

### Fixed

* **瘦包补齐 `common/model_lib`** 并对缺依赖启动路径降级，修复便携包引导失败。（`39787bd`）
* **桌面壳更新端点**正名为现仓名 `SeedVR2-Lite`（旧 `SeedVR2-lite` 依赖 URL 大小写不敏感兜底，见审查 2026-11-18 F-S1）。

## [1.8.0] - 2026-11-16

### Added

* **段间 Hann 混合与 OOM 分级重试**：长视频分段推理在段边界加入 Hann 窗重叠混合，消除拼接跳变；显存不足时先降 tile、再降分辨率，并记录每次自动降档；修复同一 latent 二次 OOM 时未命中降档缓存的问题。（`ba06fce`、`4d918ea`）
* **在线运行时安装与官方下载源**：环境包支持安装时直连 PyTorch 官方索引，模型默认改走 Comfy-Org/SeedVR2 官方仓，保留社区仓 `--repo` 覆盖；补权重双命名族映射与完整性校验。
* **视频画质客观基线与质量熔断**：新增确定性合成剪辑的 PSNR/SSIM 基线门禁、A3 质量熔断与自动回滚、DR 恢复实测记录。（`5d6b2a2`）

### Changed

* **CSP style-src 摘除 `unsafe-inline`**：模板/JS 的内联 display 与静态样式迁移到 `.sv-hidden` / `.sv-vis-*` 状态机与 CSSOM；新增迁移锁测试防止回流。（`b7130ed`）
* **文档与治理**：建立 `docs/INDEX.md`、ADR-0019 台账入库、README 显存估算与对比图更新；`check_local_only_refs.py` 归一化 `..` 路径，消除已分发文件误报。

### Fixed

* **torchvision Windows 锁哈希**：PyTorch 索引页对 Windows 轮只给 metadata、无 `#sha256` 锚点；用 `generate_lock.py` 从实际 R2 wheel 重生成，增补 `ce0744…`，版本集不变。
* **CI 环境与跨引擎 E2E**：Linux 视频基线前显式安装 ffmpeg；E2E 样式注入改 CSSOM，htmx 关闭指示器样式注入，WebKit 下改断言可见性状态类而非受祖先影响的 computed display。
* **Trivy 镜像门禁**：scan job 补 checkout 以读取 `.trivyignore.yaml`；对 updater 钉死的 zip 4.6.1 CVE 按既有政策登记存量豁免并设到期日，扫描仍阻断。

## [1.7.0] - 2026-10-28

### Added

* **Ctrl+V 粘贴上传**：从截图工具/网页复制图片后在工作台直接粘贴即入队，省掉「保存到磁盘再选文件」。`app.js` 新增 `setupPasteUpload`，三类情况不接管（焦点在输入框/可编辑区、上传区不可见、剪贴板无图片）；剪贴板图片常无文件名（Firefox 甚至给 `image.png`），统一重命名为 `pasted-<时间戳>.<ext>` 否则后端按扩展名拒收；与拖拽/点选复用同一套回调，两条路径行为不分叉。五套语言包补 `restore.pasted`。（`a3c5f8c`）
* **服务端输出长边硬上限 `inference.max_output_side`**（默认 6144，0=不限制）：`resolution` 由客户端决定、`max_resolution` 只是请求侧上限，此前传 `resolution=20000` 没有任何拦截点——请求被接受、加载模型、必然 OOM 且已烧掉几十分钟 GPU。现在依赖解析阶段即 400 并给出可用上限；`max_resolution` 超限只钳位不拒绝；两倍模式算出的分辨率同样受约束（与既有 8MP 像素上限互补）。（`91773a6`）
* **history schema v5 输入分布台账**：history 表新增 `input_width` / `input_height` / `input_duration_sec`（v4→v5 幂等迁移，迁移前自动备份），单文件与批量入口均经媒体几何探测落账（探测失败留 0，fail-open）；`.gitignore` 增加 `perf/results/`（perf_monitor 台账目录）。（`8cc6298`）
* **训练 smoke 种子固定 + 权重/ckpt sidecar 溯源 + 量化基线定期化**：`train_minimal_lora` 接入 `common.seed.set_seed` + `--seed`（默认 42），结果与 sidecar 记录种子；checkpoint 落盘写 weight_sidecar（training 段含来源/超参），`download_model` 校验后写权重 sidecar（幂等）；`gpu-smoke.yml` 量化质量基线纳入 schedule 定期执行并归档 artifact；`download_model` 增加双命名族并存警告。（`861398e`）

### Changed

* **移除 `gaussian_blend` / `groupnorm_accumulate` 两个无消费方的配置键**：二者被读进 `_vae_tiled_config` 却无人消费，日志仍打印 `gaussian_blend=True`，让用户以为混合可切换。复核确认 tile 接缝一直由上游 `tiled_decode` 的 raised-cosine 斜坡融合负责（`ramp = 0.5-0.5*cos(t*pi)`，按 count 归一化），不存在"关掉混合"的开关；因此不引入 VOSR2 的 Gaussian 实现（替换已验证的融合属高风险无收益，且需 monkey-patch 上游）。决策与证据见 `docs/adr/0006-gaussian-blend-not-adopted.md`，附 5 条语义哨兵测试。（`3851184`）
* **事件总线不引入 VOSR2 的 `EventBus`（ADR-0005）**：本仓 `services/task_events.py` 的 `TaskEventBus` 已具备按 `task_id` 分片、每队列 64 条背压、最终态缓存 + 60s TTL、跨线程发布；VOSR2 的全局单总线缺分片与背压，在本仓的批量/多标签/桌面壳场景会串台与无界增长。反向建议一并记录。见 `docs/adr/0005-event-bus-vs-progress-listeners.md`。（`6c61a2c`）
* **权重双命名族与完整性自检差异注释**：`config.yaml` model 段注释说明 `ema_` 前缀族与裸名族互不兼容、`sha256_*_alt` 用途与下载路由，及 `integrity_enforce=false` 仅为本地开发默认值、发布构建期注入 `true` 的差异；仅注释变更。（`b2823e0`）
* **治理文件同步**：`CODEOWNERS` 修正死规则（路径改指 `app/integrated_app/security` 等、新增 `model_lib/` 禁区、删除失效条目）；`SECURITY_MATRIX.md` 代码审查行状态按实际同步。（`2c44d34`）

### Fixed

* **依赖升级修复 dependabot 11 项告警（3 个包）**：urllib3 2.7.0→2.8.0（三清单同步）、source-map-js 1.2.1→1.2.2（tests npm）、rustls 0.23.43→0.23.45（desktop Cargo.lock）；uv.lock 顺带对齐 pyproject 过期条目（ruff 0.16.6、项目版本 1.6.1）。（`2b1e3b6`）

## [1.6.1] - 2026-10-02

### Fixed

* **标题字体切换器「选了没反应」（变量名错位回归）**：`app.js` 的 apply/restore 写入的是 `--sv-font`，而现行主题 CSS 消费 `--sv-font-display`（旧主题单一变量在重构时拆成 `-display/-body/-mono`，此处未跟上）——选中字体被存进 localStorage 并高亮菜单项，但没有任何规则读取它，标题字体永不变。改写 `--sv-font-display`（`static/js/app.js`，缓存戳 bump 至 `v=3.8.3`）。实测：选书法体后 h1 计算字体即时变化、跨刷新持久。
* **历史统计不排除回收站记录**：`history_db.get_statistics()` 的五条聚合（总数 / 按类型 / 按状态 / 平均耗时 / 成本可见性 SUM）全不带 `deleted_at IS NULL`，而列表查询带——软删除（回收站，`delete_record` 默认行为）后统计卡数字永不回落，与列表页不一致。五条聚合补齐过滤；恢复出回收站自动重新计入。附测试（`tests/test_storage_lifecycle.py`：软删→回落→恢复→重计）。
* **`start.bat --dev` 起不来（uvicorn 找不到 `app_server:app`）**：`app_server` 刻意不提供模块级 `app`（ASGI 应用在 `create_app(config)` 现场构造），而 uvicorn `--reload` 只接受 import string。新增专用开发入口 `app/integrated_app/dev_runner.py`（import 时构造 `app`，无其他行为），`start.bat --dev` 改指 `dev_runner:app`；生产启动路径（clean_launch / start_portable）不受影响。实测：`--reload` reloader 启动、ping 正常、文件变更触发 Reloading。附契约锁测试（`tests/test_dev_runner.py`）。

## [1.6.0] - 2026-09-30

### Added

* **上游同步探针 `scripts/check_upstream_sync.py`**：核对 vendored `model_lib/{dit,dit_v2,video_vae_v3}` 与上游 `models/` 的语义差异。原计划用逐文件 SHA256，实测**39 个共有文件哈希 100% 不等**（本地树加了中文 docstring、现代化了类型注解、并做过符号重命名），哈希门禁会永远输出「全部分叉」从而被忽略；改为**去 docstring/去注解/去 import 后按顶层符号取哈希**，分 `same` / `upstream_only`（真待同步）/ `local_only`（本地领先，属资产）/ `body_differs`（待人工判定）四态，实测把 100% 分叉压到 14 相同 / 25 差异。内置 8 条「已知答案」自检，不符即**拒绝出报告**；网络不可用与代码分叉用不同退出码（0/1/2/3）。附 19 条 CPU 单测。

* **权重档位监控 `scripts/check_numz_release.py`**：比对 `numz/SeedVR2_comfyUI`、`Comfy-Org/SeedVR2`、`cmeka/SeedVR2-GGUF` 的实际档位与 `config.yaml` 登记项。判据是「尺寸:档位」而非文件名——否则同档位的另一源命名（numz `seedvr2_ema_3b_fp16` vs Comfy-Org `seedvr2_3b_fp16`）每轮都会刷成"新档"，监控会和永远全红的门禁一样被忽略。附 20 条 mock 单测（不联网），含"网络失败必须报无法判定、不得报一致"。

* **torch.compile 真实可用性探测 `compile_support()`**：既有 `is_available()` 只看 `hasattr(torch,"compile")`，Windows 上恒为真，而实际编译可能静默失败回退——设置里的开关于是"能勾、能存、不生效"。新探测拿一个最小 Module 走到**一次真实前向**（惰性编译下只看 `compile()` 返回值会误报可用），结果经 `GET /api/system/settings` 的 `performance.compile_available` / `compile_reason` 暴露，UI 据此把两个编译开关置灰并显示原因；探测失败不阻断设置接口。

* **`/api/system/settings` 新增 `torch_compile_dit` / `torch_compile_vae` 与 `performance` 投影**；`restore.html` 高级面板的 DiT / VAE 段各加一个编译开关（走设置接口而非修复表单：编译在模型加载期套用，按请求改只会反复重编）。词表 5 份同步补 7 个键。

* **`scripts/perf_compile_baseline.py`**：四组合（off/dit/vae/both）性能基线，每组独立子进程 + 跑前清 inductor 缓存 + 固定 seed + `color_correction=none`；带 `assert_compile_engages()` 前置自检——编译没真生效就**拒绝出报告**，防止把"同一条 eager 路径跑四遍"读成"该特性无收益"。

* **桌面壳可执行门禁 `.github/workflows/desktop-shell.yml`**：Tauri v2 / Rust 侧此前没有任何机械验证入口（19 条 workflow 只覆盖 Python 与 CodeQL，规范声明的 `cargo clippy --all-targets -- -D warnings` / `cargo test` 从未被串联）。新 workflow 仅在桌面壳相关路径改动时触发，与后端 `ci.yml` 并行；并把 tauri-build 生成的 `desktop/src-tauri/permissions/autogenerated/*.toml` 纳入跟踪（`.gitignore` 加例外说明），使能力 ACL 在 git 里可审计、`git clean` 与新克隆不会静默丢失——新增自定义命令须 `main.rs` 注册 + `build.rs` 声明 + 提交重生成的 toml 三处同步，由该 workflow 机械兜底。同批补自绘标题栏所需的三个前端命令（`window_start_dragging` / `window_is_maximized` / `window_request_close`），改走命令而非 `data-tauri-drag-region`（后者对脚本加载的远程源页面不生效）。

* **水印溯源闭环补齐最后一段**：`watermark.strip_watermark_envelope()` 把从产物里提取的信封（`<task_id>|<64hex摘要>` 或 `<品牌>_<task_id>|unsigned`，外加按字节补齐的尾部噪声）还原成可用于反查的 `task_id`；`GET /api/system/history/resolve` 的 `watermark_payload` 入口据此归一化（此前直接把带摘要的提取结果当 task_id 查库必然 `found=false`）；`scripts/verify_watermark.py --show-payload` 同步打印「反查 ID」。

* **`scripts/experiment_watermark_transcode.py --attacks` 图像域攻击矩阵**：PNG/JPEG q95-q80/WebP/缩放/裁边/高斯噪声 × 两档水印的可验证性对照（免 ffmpeg 可单跑），把「抗什么、不抗什么、吃不吃内容」变成可复跑的数字而非文档措辞。

* **引用可用性门禁 `scripts/check_local_only_refs.py`**：把「追踪文件指向未随仓库分发的本地文件」这一方向的幻影引用变成可执行检查（默认扫暂存区、`--all` 扫全库；`.gitignore` 与运行/构建产物目录豁免；多义路径按「命中已跟踪文件即放行」；支持文件级「本地未分发引用」声明与 `docs/ci/local_ref_baseline.json` 历史基线，只防新增不追历史）。接入 `.githooks/pre-commit`、`structure-guard.yml`、`docs-consistency.yml`，并写入 `docs/DOD.md` §3 与 `docs/release-governance.md` §8 清单；新增 `tests/test_local_ref_gate.py`（14 例）锁定取词 / 豁免 / 声明块解析 / 消歧口径。

* **`docs/CODING_STANDARDS.md` 新增第 5 节「禁区与门禁口径（公开子集）」**：禁区表（`model_lib/` / `app/integrated_app/security/` / `config.yaml` 及授权改动后的兑现动作）+ 9 行门禁命令与接线位置 + 裁决顺序（代码 > 追踪文件 > 摘录）。AI 协作协议 `AGENTS.md` 与 `docs/agents/` 按「干净交付」决策仍不随仓库分发，本节的目的是：**克隆仓库后不依赖任何本地文件也能遵守禁区与门禁**。
* **取证模式 `SEEDVR2_WATERMARK_PROOF_MODE=1`**：图像输出为 JPEG/WebP 时强制转存 PNG。有损编码器对隐式标识是临界存活（实测 JPEG q90/q95 依内容摆动、q80 以下必失），开启后产物侧变为确定性可验证；默认关闭以保持交付格式习惯。视频维持 H.264 CRF18（实测 16/16 帧存活），不加无损档。
* **溯源记录改落 `data/provenance/`（可用 `SEEDVR2_PROVENANCE_DIR` 覆写）**：原先写在产物旁边（`<产物名>.provenance.json`）——输出目录是交付面，多出来的 JSON 会被当垃圾删掉（连带丢掉可发现性）并污染保留策略的文件计数。现文件名内嵌产物路径哈希（`<名>__<blake2b8>.provenance.json`），JSON 增加 `output_path` 绝对路径字段保持关联；`.gitignore` 加 `data/provenance/`。
* **发布状态门禁 `scripts/check_release_state.py`**：既有的 `check_readme_release_version.py` 只核「README 声明 == 最新稳定 tag」，抓不到**代码侧版本号超前于实际发布**——实测 `pyproject.toml` 写 1.5.8、CHANGELOG 有 `## [1.5.8] - 2026-09-13` 段落，而仓库最新 tag 停在 v1.5.7：**1.5.8 从未发过版，这样挂了 9 天无人报警**，访客照 CHANGELOG 去找 Release 会拿不到任何东西。新脚本以最新稳定 tag 为锚做四向核对：`pyproject == tag` 为正常；`pyproject > tag` 时 CHANGELOG 对应小节必须显式标注「未发版」；`pyproject < tag`（发版后没回写版本位）报错；任何高于 tag 的 CHANGELOG 小节缺标注同样报错（防止只在 CHANGELOG 里先写一个未来版本）。拿不到 tag 时硬失败，不静默放行。判定核心是纯函数，`tests/test_release_state.py` 13 例全部用构造输入覆盖（含「当前仓库必须自洽」这条不 skip 的实况用例）。接线：`docs-consistency.yml` 新增该步并把 `pyproject.toml` 加进 paths 触发；口径写进 `docs/CODING_STANDARDS.md` §5.2 门禁表与 `docs/release-governance.md` §3 步骤 2 / §8 清单。**同时把本文件 `[1.5.8]` 标题就地标注为未发版**（其条目将随 1.6.0 一并发布）——门禁先红后绿，两个方向都在真仓库上验过。

### Changed
* **12 个缺失并发组的 workflow 补上 `concurrency`，不再重复计算已被取代的运行**：此前 20 个 workflow 里只有 8 个有并发组，最重的 `ci.yml`/`e2e.yml`/`docker-publish.yml` 都没有。实测同一批 PR 里 ci.yml 的 51 次 `pull_request` 运行只对应 8 个不同 PR（约每 PR 推 6 次），也就是多数运行在算已被新推送取代的旧结论。策略分两类：**纯检查类**（ci / dco / e2e / security / gitleaks / docs-consistency / structure-guard / performance / dependency-audit / docker-publish）用 `cancel-in-progress: ${{ github.event_name != 'push' }}`——同 PR 新推送取消旧运行，但 **push（含 main 上的权威验证与镜像发布）永不取消**；**有写副作用类**（automerge 负责开启自动合并、stale 负责评论与关闭 issue）用 `cancel-in-progress: false`，只串行不取消——取消掉"正在开启自动合并"的那个作业会让 PR 静默卡住。`portable-release.yml` / `gpu-smoke.yml` 原有的有意 `cancel: false` 未动。


* **`config.yaml` 的 `torch_compile` 从全局单开关拆为 `dit` / `vae` 两段**（各 `enabled/mode/backend/fullgraph/dynamic`）：改前三处编译代码读的是同一个 dict，"只编一个阶段"物理上无法表达——而这正是 numz 把编译做成独立节点的原因。新增 `TorchCompileStage` / `TorchCompileSettings` 两个配置模型（原先是 `dict[str, Any]`，拼错键无人发现）。旧扁平 `torch_compile.enabled` 仍被接受并对两阶段同时生效（`config.yaml` 是用户手改的文件，直接忽略等于让已开启的人静默掉速）。`seedvr2_engine.py` 中 DiT 与 VAE 两段逐字重复的编译代码收敛为 `_apply_stage_compile`。

* **`start.bat` 两条启动命令加 `-X utf8`**：本机实测 inductor 在非 UTF-8 模式下用 GBK 读文件抛 `UnicodeDecodeError`，被 `CompileOptimizer.compile()` 吞成一条日志并返回原模型，torch.compile 因此从未生效。必须是启动旗标——`clean_launch.py` 同进程起服务，进程内 `os.environ["PYTHONUTF8"]` 已来不及。便携包路径本就正确（`launcher.ps1` 已设 `PYTHONUTF8=1`），受影响的是直连脚本入口。

* **输出文件名一律沿用输入文件名（图/视频、单文件/批量四条路径统一）**：上一提交只去掉了 `_AI` 后缀，单任务仍会被改成 `20260919_101530_3B_a1b2c3d4.png`——而用户是按文件名辨认内容的，改名等于让产物无法检索。现 `_build_output_name(input_path, ext)` 取输入 basename 去扩展名 + 目标扩展名（中间点如 `scene.v2.final` 保留），Windows 风格路径在 Linux 容器下先归一分隔符，非法字符清洗，拿不到可用名字才退回时间戳（绝不产出 `.png` 这类无名文件）。随之删除只服务于命名的 `_normalize_model_tag`（死代码）。**安全口径变化要写清**：原 uuid8 随机后缀的理由是「防输出路径可预测被下载」（T4-3），但两个下载端点 `GET /api/restore/{task_id}/download` 与 `GET /api/system/history/{record_id}/download` **都只接受 id、从不接受文件名**，再过 PathGuard 白名单——可读文件名不构成枚举面，防护职责在 id 侧；安全审计执行清单（维护者本地账本）与数据治理评估已就地按实情补记，不在已分发文件中留下指向本地账本的引用。重名由既有 `_resolve_unique_path` 追加 `_1/_2` 兜底（同一输入修两次不会覆盖上一版）。新增 `tests/test_output_naming.py`（13 例）锁定：沿用输入名、中文与空格、中间点、Windows 路径、非法字符、空名回退、`_AI` 仅显式开启、重名不覆盖。
* **产物不留标识痕迹：文件名默认不再强加 `_AI` 后缀，信息类水印日志降到 DEBUG**：用户侧要求交付形态干净——文件名保持原样（批量路径本就用 `{input_dir}/restored/{input_name}{ext}` 沿用输入名；单任务默认名里的 `_AI` 后缀改为默认不加，需要文件名级显式标识的对外部署用 `SEEDVR2_EXPLICIT_AI_LABEL=1` 显式开启）。同时把**信息类**水印日志统一降为 DEBUG：图像/视频嵌入与验签细节、`合成后水印抽样验证: N/M`、水印密钥首启自动生成与旧位置迁移，在默认 `logging.level: INFO` 下终端与 `logs/app.log` 均不出现「水印」字样。**安全降级不一起静音**（有意保留）：缺密钥 `ERROR` + `WATERMARK_KEY_MISSING` 审计、重复码降档与载荷截断 `WARNING`、落盘复验失败 `ERROR` + 溯源侧车——否则会把刚修好的「以为有、其实没有」重新藏回日志级别以下。新增 `tests/test_watermark.py::TestLogSilence` 两条用例分别把「正常流程 INFO 零水印字样」与「缺密钥必须仍是 error」钉成契约。代价知情：显式标识层就此只剩文件元数据（`ai_generated` / `seedvr2_params`）与协议文案，文件名与画面均无标识。
* **`USER_AGREEMENT.md` 3.3 改为中性表述，与界面口径一致**：原句「输出内容默认嵌入不可感知的内容来源标识（**载荷含品牌标识与时间戳**）……您不得声称输出内容为完全未受 AI 处理的原作」含两处不合适的内容——向已分发文档披露水印载荷结构（等于给去除/伪造提供靶点），以及把义务压在用户"不得声称"上。现改为「输出内容由 SeedVR2 模型生成；对外传播时，请按所在司法辖区的要求对人工智能生成合成内容进行标注」。**代价记录在案**：协议与界面均不再主张"用户不得去除内容来源标识"，隐式水印的实际约束力因此只剩技术层（可验签）而无非议条款支撑；若日后需要恢复该义务，应改为不含实现细节的表述。
* **`extract_watermark()` 提前退出**：位序从图像左上角起算，读够 `expected_length × repeat` 个块即停。语义不变（前缀位序一致，有等价用例把守），但 4K 图的全图扫描降到载荷长度规模——落盘逐产物复验的成本因此可接受。

* **`_VERIFY_SCHEMES` 候选保持 4 组**（无损档 + 鲁棒档 repeat 1..3），新增档位若有需要按同一模式扩展；`security/integrity_manifest.json(.sig.ed25519)` 已随 `security/watermark.py` 改动重算并重新签名（11/11 自检通过）。

* **`security/watermark.py` 模块与函数文档按实测纠偏**：删去「不可移除」这一做不到的承诺，改写为可核对的边界（PSNR 57-69dB / 最大改动 3、鲁棒档 ≈37dB、不抗缩放裁剪旋转、JPEG 临界且吃内容、无密钥不具举证力）。`tests/test_watermark.py` 中原先「因尺寸截断而侥幸通过」的无密钥用例改为正面断言降级语义（真品与伪造品在弱检测下同样通过——这正是必须有密钥的原因）。新增/改写用例合计 24 例；`ruff` / `black` / `mypy app/integrated_app` 全绿，水印相关 7 个测试文件 140 passed。

* **生产载荷一律带品牌前缀，元数据补机器可读 AI 标识字段**：审计「水印到底能证明什么」时发现两处语义空洞。① 有密钥时嵌入的载荷就是裸 `task_id`（提取结果读出来只有一串 UUID + 摘要，**文件里没有任何"出自 SeedVR2"的痕迹**），而分发版安装包刻意不打包密钥、终端用户首启自动生成自己的密钥（已实测：用户机器的产物用项目方密钥验签 `False`）——签名只能证明"出自持有该密钥的那个实例"，所以品牌串必须成为载荷的一部分。现嵌入前统一加 `SeedVR2_ReSerendipity_` 前缀（+176 bit；512×512 起仍能撑住 `repeat=3`，有 `test_production_shaped_payload_keeps_redundancy_at_512` 作容量回归锁；`strip_watermark_envelope()` 反查时剥除）。② 文件元数据此前只写推理参数，没有任何字段能回答"这是 AI 生成合成内容吗"。现 `generation_params_payload()` 恒定附 `ai_generated: true` / `ai_generator: "SeedVR2"`，随 PNG tEXt、JPEG·WebP·TIFF EXIF UserComment 一起走；参数为空时也写（标识义务不因参数缺失而缺席，改掉了原先"空参数返回空串"的契约）。视频容器 `comment` 改为复用同一函数，消掉 `_video_pipeline.py` 里一份手写的重复 `json.dumps`。
* **决定不做输出画面的可见角标**：评审「显式标识」补法时提出加「AI 生成」角标，被明确否决——本项目产物是交付级图像/视频，画面内叠加会破坏观感且用户侧无法接受。因此显式标识层保持在**文件名 `_AI` 后缀 + 元数据机器可读字段**，其代价写进 `docs/COMPLIANCE_CHECKLIST.md` 第 5 项（不再挂"待补角标"，改为如实记录该取舍与其后果）。
* **追踪文件的规范式引用改指分发版**：`docs/DOD.md`（5 处）、`docs/release-governance.md`（3 处）、`model_lib/SOURCE.md`、`tests/frontend/README.md` 等指向 `docs/CODING_STANDARDS.md` 第 5 节；`docs/AI应用分发安全加固指南.md`、`docs/DATA_GOVERNANCE_ASSESSMENT.md`、`docs/VAE_STALL_DIAGNOSIS_20260910.md`、`docs/SECURITY_REMEDIATION_TRACKER.md`、`desktop/README.md`、`docs/发布检查清单.md`、`docs/开发者指南.md`、`docs/桌面应用分发全流程指南.md`、`docs/repo-analysis/ComfyUI-Mie-Package-Launcher_技术学习报告.md` 与本文件则以文件级声明或就地说明标注「维护者本地、未随仓库分发」；`install.bat` / `install.sh` / `pyproject.toml` / `scripts/installer/pyproject.toml` / `scripts/build_portable_bundle.ps1` / `scripts/portable_bundle_lib.ps1` 注释同步。

* **两处不真实描述得到纠正**：`.github/SECURITY.md` 的链指向克隆中不存在的 `[部署文档](docs/plans/DEPLOYMENT.md)`，改指随仓库分发的 `docker-compose.yml` + `deploy/kubernetes/`；`.githooks/README.md` / `.githooks/install.sh` 把 `precheck.ps1` 描成「仓库内脚本」，改为明示它未随仓库分发、缺失时钩子自动降级。
* **`docs/AI应用分发安全加固指南.md` 第六节按代码重写，并纠正一处过度承诺**：原文称签名水印"能证明对方删过"——**不成立**：产物一旦被重编码/缩放，水印本就不可读，"缺失"与"被删"无法区分。现写清四层事实（载荷形态、三层标识、跨分发不可归属项目方、可鉴定范围仅未再加工原件）与两条产品取舍（画面无角标、文件名不改名）。README 里"输出文件名默认携带 `_AI` 后缀"的过时说明同步纠正，`.env.example` 补 `SEEDVR2_WATERMARK_PROOF_MODE` 与 `SEEDVR2_PROVENANCE_DIR`。
* **本轮明确记录、决定不动的四项**：单任务输出目录保持 `outputs/image|video`（保留策略与历史库按目录管理，不随批量模板改）；元数据 `ai_generated` 标识保留（画面与文件名均不可见）；抗缩放/裁剪的同步定位码不做（载荷格式变更会让历史产物验证链断裂，需单独立项）；跨分发可归属需在线签发，暂不做。

* **三份对外文档与代码现实对账（2026-09-21 逐项复核）**：① `docs/DATA_GOVERNANCE_ASSESSMENT.md` 有两条 ❌ 早被代码推翻还挂着——「无 schema version / `PRAGMA user_version` 零命中」（实际 `history_db.py` 有 `SCHEMA_VERSION = 4` + v0→v4 迁移链 + 升级前自动备份，迁移异常会冒出 `initialize()` 因而不推进版本号）与「HistoryRecord 无源文件 hash 列」（实际 `input_sha256` 已建列、由 `restore_service` 写库）；「无 PSNR/SSIM」只对一半——`utils/image_metrics.py` 存在且撑起 CI 的 `tests/test_golden_quality.py`，真正缺的是**运行期打分**，措辞已改准。仍成立的三条保留并标注复核日：上传只有字节上限而无像素/时长校验、`folder_path` 与批量入口不过魔数、权重 SHA-256 不入历史。**另补一条本层最深的矛盾**：留存策略按龄删除输入原件与产物，而这两者恰是水印反查与内容比对所需的证据本体（`pinned` 只能人工豁免，磁盘水位触发时连按龄缓冲都没有）。② `.github/SECURITY.md`「输出保护」按实测重写：删去「图像与视频均可在 H.264 编码后稳定验证」这类过度承诺，写清能证明什么（有密钥只证明"出自持钥的那个实例"，分发版各自生成密钥故跨分发不可归属）、不抗什么（缩放/裁剪/旋转、二次重编码；鲁棒档对 JPEG 临界存活），并把失败兜底的侧车真实路径 `data/provenance/` 补上。③ **GPG 状态按实测改写（含对本条初版的更正）**：SECURITY.md 与 `docs/release-governance.md` §3.4 / §8 原写着「Release 自动生成 GPG 签名」。核对仓库 secrets 与 12 个 Release 的资产后，事实是**三个密钥（`GPG_PRIVATE_KEY` / `GPG_PASSPHRASE` / `GPG_KEY_ID`）确已配置**、签名也确实产出过（v1.5.0 与 v1.5.6 带 `SHA256SUMS.gpg`，后者由 `github-actions[bot]` 上传），但签名**不在发版默认链路上**：`portable-release.yml` 的 `sign-release` 条件是 `workflow_dispatch && inputs.upload_to_release`，按 tag 发布时根本不跑；且两个工作流都只认文件名恰为 `SHA256SUMS.txt` 的资产（历史上出现过 `SHA256SUMS-v1.5.4.txt` 与裸 `SHA256SUMS`，都会被判"无可签名资产"而跳过）。净结果：12 个 Release 里 10 个没有签名。三处文档据此改写，`gpg-signed-release.yml` 头部注释补上这两条边界与一句判定口径——**绿色运行 ≠ 已签名，只有资产列表能区分**。（本条初版写成"密钥从未配置"，那是从守卫语句反推的猜测，已被 secrets 清单与 Release 资产证伪；留着这段自我更正的痕迹，是因为"从代码可能推出什么"和"实际发生了什么"不是一回事，这类错误今天犯了两次。）⑤ **决定不做商标注册与软著登记**（台账 T2-4）：本项目为开源且非营利，评估后认为该投入不划算。代价按原样记账：无注册商标时，被冒名或商用盗用的主张强度弱于注册标；软著缺失则权属举证要靠提交历史与首发时间侧证。这条决定与代价记在本文件（与「决定不做输出画面的可见角标」同一记账方式），维护者本地台账 `docs/SECURITY_AUDIT_REPORT_v2_EXECUTION_CHECKLIST.md`（未随仓库分发）的 T2-4 状态行同步标注。④ 本文件 `[Unreleased]` 里重复出现的小标题（`### Fixed` 两次、`### Added` / `### Changed` 各一次重复）收拢成每个类型单一小节，按 Added / Changed / Removed / Fixed 排序；条目正文以「移动前后集合相等」的断言把守，一字未改。
### Fixed
* **`SECURITY_MATRIX.md` 去掉 UTF-8 BOM**：BOM 是提交进 git blob 的（`ef bb bf 23 20 53 65 65 64...`），不是检出产物，所以任何以行首锚定的工具都读不到它的 H1——`grep -c '^# '` 去 BOM 前 0、去后 1。改动恰好 3 字节（1229 → 1226），其余字节逐字相同。仓内另有 20 个带 BOM 的文件本条刻意不碰：9 个 `.ps1` **全部含非 ASCII 字节**（51–10045 字节），PowerShell 5.1 对无 BOM 的 `.ps1` 按 ANSI 码页解码会直接把脚本读坏（实测 here-string 认不出、词法崩在含中文的行），BOM 对它们是必需项而非垃圾；其余为 `.nsi`（NSIS Unicode 同理）/ `.rs` / `.py`，Python 源码按 PEP 263 容忍 BOM，无故障可修。
* **pre-commit 的 mypy 钩子不再写死 `.venv/Scripts/python.exe`**：那条路径只在"当前目录恰好是主 checkout、且是 Windows"时存在。linked worktree（`git worktree add`）没有自己的 `.venv`，POSIX 主机没有 `Scripts/` 这一层，两处都吃 `Executable `.venv/Scripts/python.exe` not found` → 提交被拦下，而类型检查一行都没跑（原注释还要求 "Linux 贡献者请把 entry 改为 .venv/bin/python"，等于把可移植性推给每台机器手改）。改由 `scripts/mypy_gate.py` 按 `.githooks/pre-commit` 的四级回退（`.venv/Scripts/python.exe` → `.venv/bin/python` → `python` → `python3`）挑**第一个真装了 mypy 的**解释器执行，退出码原样透传。三种结局分开：本机根本没有 Python 才放行；装了 Python 却缺 mypy 仍硬失败并给出安装命令（静默跳过等于拆掉本地门禁）。`tests/test_mypy_gate.py` 直接抓 `.githooks/pre-commit` 里的字面量对拍顺序，防两处漂移。残留口径：入口 token 仍是 `python`，只装 `python3` 的主机会在 which 阶段报 not found——此时报错是真诊断。
* **DCO 检查按「每个提交的作者邮箱」豁免，不再按 PR 开启者整条跳过**：原先 job 级 `if: github.actor != 'dependabot[bot]'`，而 actor 是 PR 开启者——一旦 bot 开的 PR 里混进人类无签名提交，整个检查就被跳过而无人察觉。现在 job 永远运行，逐个提交判定：dependabot / github-actions 两类 bot 邮箱自动豁免，人类提交缺 `Signed-off-by` 仍然红（merge commit 按 DCO 惯例跳过）。base/head SHA 改经 env 注入脚本，不再直接插值进 `run:`。实测三例：仅含 bot 提交 → 豁免 1、exit 0；main 最近 3 个人类签名提交 → exit 0；无签名的人类提交 `c4b7e15` → `::error` + exit 1。
* **`performance` 门禁按 runner 档位分流，恢复 PR 档的真实绿**：P95 < 500ms 的硬阈值此前在托管 runner 上无条件判红，而托管 runner 没有 GPU（同机日志 `is_gpu_available:false`、4 核），量出的 11000ms 不是回归信号——最近 4 次 PR 连红即由此而来。新增 `Resolve threshold gate mode` 步：检测本机是否真有可用 GPU（`nvidia-smi`/`rocm-smi`）来选档，`strict` 判红、`report` 只记录归档；将来把该 job 迁到带 `gpu` label 的 self-hosted runner 上无需改 workflow 会自动回到 strict。`workflow_dispatch` 加 `gate_mode`  choice 输入（auto/strict/report）便于手工复验。`tests/perf/locustfile.py` 侧配合读 `PERF_GATE_MODE`，**错误率阈值两档都仍然判红**（它是正确性信号不是时延指标），非法档位值退回 strict。5 种组合已用 locust 打桩 harness 实测：report+延迟超阈→PASS、strict+延迟超阈→FAIL、report+错误率超阈→FAIL、strict+全正常→PASS、bogus 档+延迟超阈→FAIL。
* **firefox 的 `page.goto: Timeout 60000ms` 修在根因上，不动 `retries: 0`**：`tests/specs/performance.spec.ts` 有 10 处裸 `page.goto`，绕过了 `BasePage.navigate()` 里的 `closeSseBeforeNavigation()`——而 SSE 重连风暴正是当初为 firefox 死锁修的根因（api-mocks 用 `route.fulfill` 返回有限响应体，EventSource 依规范自动重连）。修法是把该关闭逻辑抽成 `tests/utils/wait-helpers.ts` 的导出函数（`BasePage` 改为调用同一份，消除两处漂移），spec 在每处 goto 前调用它；**不改任何 goto 的 `waitUntil` 档位**（死锁的因不是等待档位，改档位会悄悄改变各用例的时序假设）。仓库既有的 `retries: 0` 政策（flaky 必须在源头修掉，不用重试掩盖）保持不变。已知残留：另有 7 个 spec 共 58 处裸 goto 走同一路径，本次未动。
* **gpu-smoke 的 skip-record 增加写后回读自证**：`Record skipped smoke as an issue` 步在建好/追加完 issue 之后立刻 `gh issue view` 回读，取不到就 `::error` 并 exit 1。此前"以为开出来了"和"真开出来了"在日志里长得一样——修复前该步每次红且从未真的落库过任何一条停摆记录。
* **导航防复发注释里的 CI 编号取证更正（只改注释与 docstring，零行为改动）**：`tests/pages/base.page.ts`、`tests/utils/wait-helpers.ts`、`tests/test_e2e_navigation_guard.py` 三处都写着「CI #103 的 `theme.spec.ts` 就是 `page.goto: Timeout 60000ms`」。2026-09-25 逐份 firefox job 日志核对（取样 25 份：2026-09-02→09-25 之间全部 12 个 firefox 红的 main push run、若干绿 run 作对照、PR run #103/#104/#105/#161/#163/#164/#166；8 月及更早未扫）结论是这句错了三处——run#103 的 firefox 有 31 条红，其中 `theme.spec.ts:187` 那两条报的是 `locator.click: Timeout 30000ms`（`#agreementModal` 的 overlay 拦住了 `#btnThemeToggle` 的点击，与导航无关），整份日志里 goto/reload 超时 **0 次**；`theme.spec.ts` 报的从来是 `page.reload: Timeout 60000ms`（#65/#68/#69/#70/#71）或 #79 的 `Test timeout … while running "beforeEach" hook`，从未以 goto 那一族报红；取样里唯一有日志实证的 goto 超时是 PR run **#163** 的 `network-conditions.spec.ts:104`（裸 goto、默认 `waitUntil:'load'`，且该 spec 不调 `setupAllMocks` 所以 SSE 仍活着）。reload 族的 run 范围按实测从「#64-#70」订正为 #64/#65/#68/#69/#70/#71（红的是 `history.spec.ts:121` 与 theme.spec.ts 的若干用例）。**机制结论不动**（导航前先掐 SSE、不改 waitUntil 档位），改的只是编号与用例归属；三处注释统一改为可复核的取证口径，并注明取样范围与重取方法。验证：`python -m pytest tests/test_e2e_navigation_guard.py -q` 5 条结构断言全绿（注释文本不参与其判据），`CHANGELOG.md` 本条为该 PR 唯一非注释改动。


* **完整性校验失败措辞分流（纵深防御，非日常误报）**：`load_model` 里完整性校验失败原先一律拼成『文件可能已被篡改或投毒』，而 `verify_checkpoint` 对『文件不存在』与『哈希不符』都只返回 `False`，调用方无从区分。新增 `_describe_integrity_failure()` 按『在盘与否（含别名解析）』分流：缺文件给 `download_model.py` 指引，真哈希不符才保留投毒措辞与 `audit_event`；查不到对应配置项时**保守归入篡改嫌疑**（宁可措辞重，不可放过真投毒）。两类均**照旧拒绝加载**，fail-closed 未变弱。

  **严重性经实测下调（我第一版写重了）**：真跑 `load_model` 才发现 `model_manager` 的前置检查早就给出了准确诊断 —— 请求一个盘上没有的精度会得到『已尝试 fp8, fp16 均无对应文件；配置但未下载: mxfp8, int8_convrot, nvfp4；期望文件名（任一命名皆可）: …；发现未登记权重文件: …』，**根本走不到投毒那句**。所以本修复覆盖的是残余路径（预检所用权重目录与引擎 `get_pretrained_root()` 解析不一致等情形），属纵深防御，不是在修一个用户日常会撞上的误报。教训：判断缺陷的可达性与严重性要跑一遍调用链，不能只读抛错那几行。

  同一次实测也确认了既有的两级精度回退链工作正常（实测 `mxfp8 → 回退到可用精度 fp8`，`status: ok`），这是我撤销自加的 `_fallback_precision` 的实证依据（重复实现且会抢占既有链路的选择顺序）。

* **7B 分支裸 `ImportError` 换成可执行的明确错误**：`model_size == "7b"` 走 `model_lib/dit`（v1 树），实测 `import model_lib.dit` 直接抛 `cannot import name 'NaRotaryEmbedding3d'`（3 处断裂早登记在 `tests/test_model_lib_import_sentinel.py` 的 `KNOWN_BROKEN_INTRA_IMPORTS`），7B 与权重、显存无关地无法实例化。现改为抛带定位信息的 `RuntimeError`。**刻意不做的修法**：把上游缺失符号回填进 `model_lib/dit` 并不困难，但该树内部已不一致——`dit/nadit.py` 用 `apply_rope(x, freqs=)`，而同树 `dit/nablocks/mmsr_block.py`（上游原文）按 `self.rope(vid_q, vid_k, window_shape, cache)` 调用，两套 RoPE 接口互不兼容；在没有 7B 权重可比对的前提下回填，等于**拿响亮地失败换一个数值可疑但可能静默成功的 7B 路径**。真修需先定路线，见 `docs/plans/UPSTREAM_SYNC_PLAN.md` S10。

* **`scripts/verify_engine.py` 三处使其自检结论不可信的缺陷**：① 权重存在性直查 `config` 登记名，不走引擎同源的 `find_weight_file` 别名解析——盘上是 Comfy-Org 命名（`seedvr2_3b_fp8_e4m3fn`）而 config 登记 numz 命名时，假报"权重文件不存在，请去 HF 下载"；② 修好①后哈希仍会假红，因为只比 `sha256_<档>` 而合法的另一源字节落在 `sha256_<档>_alt`，现改用 `weight_hash_candidates`；③ `ModelManager()` 无参构造自 config 注入改造起就 `TypeError`，即**第 5/6 步从未真正执行过**却照常打印分区标题。补 8 条走真实文件系统的测试（既有用例一律 `@patch("os.path.exists")` 打桩，恰好绕过了①②，所以它们永远测不到）。

* **torch.compile 在本机从未生效 —— 两道独立的墙**：`CompileOptimizer.compile()` 把编译期异常吞成一条 error 日志并返回原模型，导致设置里的开关"能勾、能存、行为不变"。① 进程未运行在 UTF-8 模式时，inductor 用 GBK 读文件抛 `UnicodeDecodeError`；② 补上 `-X utf8` 后小 Module 探测通过，**真实 3B DiT 首帧却抛 `Cannot find a working triton installation`** —— CUDA 上 inductor 编译真实模型需要 triton，Windows 无官方轮子。第一道会遮住第二道，只解一个不够（我一度据 `PYTHONUTF8=1` 下"探测通过"就断言与 triton 无关，那是错的：探测物太小）。根因**不是**同段输出里的 `triton not found` 警告与 GBK 报错的因果混读——现已分别验证。处理：`start.bat` 两条启动命令加 `-X utf8`（`clean_launch.py` 同进程起服务，进程内 `os.environ["PYTHONUTF8"]` 已太晚，那会产出一个看着像修好的 no-op）；便携包 `launcher.ps1` 本就设了 `PYTHONUTF8=1`，受影响面只在直连脚本。新增 `compile_support()`：**必须跑到一次真实前向并显式检查 triton**，经 `/api/system/settings` 的 `performance.compile_available/reason` 暴露，UI 据此把两个编译开关置灰并说明原因。实测分三层，见下两条。

* **torch.compile 补齐环境后实测为「净负收益」，不该按 numz 的说法当提速项推销**：装 `triton-windows 3.8.0`（仅装进 `.venv` 做实验、未进 pyproject/lock，测完已卸）并以 `-X utf8` 重跑四组基线 —— 同一输入、固定 seed、每组跑前清 inductor 缓存、`color_correction=none`：

  | 组合 | 首包 | 稳态 | 稳态 vs 不编译 | 峰值显存 |
  |---|---|---|---|---|
  | `off` | 15.85s | 6.41s | — | 3218 MiB |
  | `dit` | 59.43s | 9.45s | **慢 47%** | **6766 MiB** |
  | `vae` | 23.00s | 9.59s | **慢 50%** | 3252 MiB |
  | `both` | 86.63s | 10.43s | **慢 63%** | 6766 MiB |

  即代码注释与 UI 提示里"DiT 20-40% / VAE 15-25% 提速"（抄自 numz、本仓从未验证）在 12GB 笔记本卡上**方向是反的**：稳态慢一半、DiT 显存翻倍、冷启动 3.7–5.5 倍。顺带推翻另一句"编译不改变数值"—— DiT 组输出的 mean/std 由 80.730/73.976 移到 80.726/73.960（量级极小但非零），VAE 组则不变；**只有把判据从 sha 换成数值统计才看得见**（sha 因隐形水印每次必然不同，用它当判据会同时产生假红与假绿）。提示文案与注释已改成本实测口径；是否整体摘除该开关留作决策，本轮不擅自删功能。
  第三道墙也记一下：`compile_support()` 最初在 **CPU** 上探测，inductor 对 CPU 算子退到 C++ 后端并要求 MSVC `cl.exe`（本机没有），于是把"可用的 CUDA 编译环境"误判成不可用并会错误置灰 UI —— 探测物的**设备**也必须与被测对象一致。

* **本会话内自我引入的回归：`dit.fullgraph` 曾被预置为 `true`**，会让新加的 DiT 编译开关一点就整次任务失败——`blockswap.py` 刻意用 `torch.compiler.disable` 标记计时区域，`fullgraph=True` 必然抛错而非降级（`reduce-overhead` 的 CUDA graphs 与跨 CPU/GPU 搬权重是第二层冲突）。两段默认现均为 `fullgraph: false` + `mode: default`，并把该边界写进 `_apply_stage_compile` 的注释：其 `try/except` 只覆盖装配期，**覆盖不到首次 forward**，所以默认值才是实际防线。

* **网页上传路径的产物名仍是缓存层改名结果（纠正上一提交"四条路径统一"的过度声明）**：`POST /api/restore/` 的上传分支会先把文件存成 `<时间戳>_<原名>_<uuid6>.ext`（`cache.generate_unique_filename`），引擎从 `input_path` 推名字时拿到的已经是这个名字——用户看到的产物形如 `1758383000_镜头A特写_a1b2c3.png`，**"输入什么名就是什么名"在网页上传这条最常用路径上并未兑现**。现由上传路由把 `multipart` 原始文件名一路传下来：新增 `app/integrated_app/utils/output_names.py` 作为唯一信任边界（`build_output_name` / `sanitize_stem`），`process_image_task` 与 `process_video_task` 增加 `output_name` 形参，扩展名仍由引擎按目标格式覆写；引擎侧原 `_build_output_name` 迁入该模块，两条路径共用一个实现。
* **顺带堵住该路径引入的穿越面**：客户端可控的文件名若直接 `os.path.join(output_dir, filename)` 可把产物写到 `outputs/` 之外；NUL 等控制字符还会写出无法访问的文件（原清洗表只剥 `\ / : * ? " < > |`）。清洗点现统一剥控制字符（`\x00-\x1f` 与 `\x7f`）、折叠 `..`、截断 48 字符、空名退回时间戳。新增 `tests/test_output_naming.py::TestSanitizeIsTheTrustBoundary`，用 6 组穿越样例（`../../evil.png`、`..\..\x.png`、`/etc/passwd`、`a/../../b.png`、`....//x.png`、`C:\Windows\sys32.png`）钉住"产物名永不含分隔符与 `..`"。
* **启动日志里的 `watermark_min_free_gb=` 改为 `disk_floor_gb=`，SSE 事件 `kind` 同步改成 `retention_disk_floor`**：真机日志扫描下，默认 INFO 级别唯一出现「watermark」字样的三行来自这里，而它指的是**磁盘水位**（低于该剩余空间就按保留策略清理），与数字水印毫无关系——同名让「默认日志不出现水印字样」这条口径无法字面成立。两处仍是原样：`periodic_output_cleanup()` 的形参名（内部签名，不在任何对外可见面上）。**同时纠正本条目最初的错误说法**：它当时写「SSE kind 保持不变，因为有测试把守」——不成立，`test_output_retention_watermark.py` 那两处只是把**测试自己构造的**字面量灌进 `event_bus` 再读回，既不引用生产者也不锁契约；改名前后靠全仓 grep 确认「一个生产者、零消费者」（前端 / 桌面壳 / 文档均未读该 kind），因此这层一致性由 grep 而非 CI 兜底。
* **幻影引用门禁的忽略豁免对「存在但为空」的忽略目录失效**：`ignored_paths()` 走 `git ls-files --others --ignored --directory`，而 git 不追踪空目录——`data/provenance/` 存在但为空时列不出来，文档引用它就被判成本机独有路径（本文件自己中招，提交被 pre-commit 拒绝）。现把 `provenance` 补进 `check_local_only_refs.ARTIFACT_PARTS`，与 `outputs` / `checkpoints` 同一口径：运行时路径约定，不是「请去阅读的文件」；`tests/test_local_ref_gate.py` 增样例锁定。
* **幻影引用门禁在 GBK 控制台下「报告即崩」**：违例文本含中文与 `⚠`，`print` 直接抛 `UnicodeEncodeError`——门禁仍以非零码退出，但维护者看到的是一栈回溯而不是「该修哪一行」。现按 `generate_integrity_manifest.py` 同口径，模块级把 stdout/stderr 重设为 UTF-8（`errors="replace"`）。
* **顺手抓出 main 上的一处幻影引用（并记录这类门禁的可见性边界）**：`scripts/install-hooks.ps1` 的注释让读者去看 `CONTRIBUTING.md`，而它按「干净交付」决策不随仓库分发（现改指随仓库分发的 `.githooks/README.md`）。**要点在于：命中条件要求路径「本机存在且被忽略」，所以这类引用在维护者机器上红、在 CI 克隆里恒绿**——`--all` 在 CI 通过并不等于零幻影，只有维护者本机跑才算数；本条即本机 `--all` 抓出、PR 门禁全绿。
* **改了 `app_server.py` 却没重算清单：11 项哈希里 1 项过期（提交后自查抓出）**：`app/integrated_app/security/integrity_manifest.json` 覆盖 11 个核心模块，上一条日志改名动的正是其中之一，而清单没重算重签——`integrity_enforce=true` 的部署会在启动自检误报「清单被篡改」直接拒绝启动。现已重算 + 重签（Ed25519 验签 PASS，字节仍是 LF + 单个结尾换行）。**并把这条人工兑现动作变成机器门禁**：`tests/test_secret_key_and_manifest_signature.py::TestManifestTracksSource` 两条用例——① 逐项 sha256 与源码比对，改核心模块没重算即红（反向验证过：把清单还原成上一条提交的版本，用例必失败）；② 清单字节必须是 git 存放态（把 CRLF 那条根因锁死，签名与克隆字节不再可能分叉）。
* **有损图像输出的水印静默全灭（隐式标识取证链缺口）**：`security/watermark.py` 的图像档（`alpha=0.5`）此前对**所有**图像输出统一使用，而 2026-09-19 实测该档水印**经 JPEG q95 即不可验证**（WebP 同样失效），项目图像输出又明确支持 `jpg` / `webp` 格式（`output_format` 参数）——选有损格式的产物实际带着「以为有水印、其实没有、且无人知晓」的状态出厂。视频管线早在 R2 就有合成后抽帧复验，图像管线只检查「嵌入是否抛异常」，而编码器抹掉水印不算异常，`mark_metadata` 兜底永不触发。现：① `watermark_policy.select_image_embed_tier()` 按输出格式选档（无损 `0.5×1` 保画质，JPEG/WebP 走鲁棒档 `0.05×3`）；② 落盘后 `output_carries_watermark()` 重读产物验签；③ 缺失统一交 `handle_watermark_loss()` 按 `watermark_on_failure` 处置（`mark_metadata` 写溯源侧车 + 审计 `WATERMARK_LOSS_DEGRADED`；`block` 删除已落盘产物并抛错，兑现「产出不落盘」字面语义）。验证：`tests/test_watermark_policy.py` 新增 14 例（含无损档 JPEG q95 必失的回归哨兵）。**诚实边界**：鲁棒档对 JPEG 是**临界存活**（实测真实照片/平滑渐变 q90-q95 活、合成细密纹理与均匀白噪声 q95 即死、q80 以下全死），因此结论只来自落盘复验，不来自档位承诺。

* **`security.watermark.enable` 是无人可读也无人可写的死开关**：图像与视频管线都读 `config["security"]["watermark"]["enable"]`，但 `AppConfig` 里根本没有顶层 `security` 段（安全配置在 `runtime.security`），`config.yaml` 也没有该键，于是恒为 `True`——一个看似可关水印、实际永远关不掉的配置。现按「内容标识不可关」的合规口径**移除该读取**，水印强制启用（要关只能改代码），并清理两处恒真分支。

* **无密钥部署的水印降级不再静默**：`_load_secret_key()` 返回 None 时此前只有一条 debug 日志，产物却从「可证伪归属」跌到「任何人可伪造」而无人察觉。现降级路径显式化：`error` 日志（一次）+ 生产侧审计事件 `WATERMARK_KEY_MISSING`（`report_missing_watermark_key()`，刻意放在服务层而非安全模块，避免 CLI/单测往取证日志里写假事件）+ 未签名载荷补终止符（配合恒定存在的品牌前缀），使 `verify_watermark()` 的弱检测仍然可用。

* **小图容量不足导致的降档/截断不可发现**：生产载荷（品牌前缀 + 任务 ID + 摘要）约 824 bit，`repeat=3` 需 ≥2328 个 8×8 块（约 400×400 以上），小图会被静默砍成 `repeat=1`（存活率随之下滑）甚至截断（必然验签失败）。现两处都记 `warning`，并补 `tests/test_watermark_policy.py::test_repeat_downgrade_is_warned`。

* **`extract_watermark()` 默认长度只有 32 字符，长载荷被自己截断**：默认 `expected_length=256` bit（32 字符）连 64 位签名摘要都取不全——`verify_watermark()` 因为显式传 2048 而侥幸正常，但 `scripts/verify_watermark.py --show-payload` 与任何直接调 `extract_watermark()` 的取证路径都会拿到被腰斩的载荷（反查落空）。现默认改 2048 bit 并在 docstring 写明"取小了双双失败"。该缺陷在载荷加品牌前缀（+22 字符）后立刻暴露为三条测试红灯，属被实测抓出的存量 bug。
* **鲁棒档产物"验签通过但报不出载荷"**：`extract_watermark()` 按默认无损档参数提取，对 JPEG/WebP/视频帧这类鲁棒档产物只会读出乱码——于是 `scripts/verify_watermark.py --show-payload` 在 `verify_watermark()` 明明返回 True 的情况下报不出 task_id（端到端实测复现于 `.jpg` / `.webp` 产物）。新增 `extract_watermark_best()` 按 `_VERIFY_SCHEMES` 逐档尝试并优先返回可验签的载荷，CLI 改用它并对不可打印字符做转义；`tests/test_watermark.py::test_extract_best_recovers_robust_tier_payload` 把"必须逐档尝试"钉成契约。

* **`scripts/check_no_hardcoded_paths.py --all` 在中文文件名 + GBK 宿主上崩溃**：`subprocess.run(text=True)` 以本地代码页解码 `git ls-files -z` 输出，遇中文路径抛 `UnicodeDecodeError` 使 `stdout` 为 None，全库扫描必崩（默认的暂存区扫描因无中文文件名而不触发）。改为取 bytes 后按 UTF-8 解码；现 `--all` 可正常跑完并通过。

* **`docs/repo-analysis/ComfyUI-Mie-Package-Launcher_技术学习报告.md` 泄露本机绝对路径**（`C:\Users\<用户名>\reference_repos\...`）：改为不含用户名的相对描述。同类残留在 `docs/repo-analysis/` 另两份报告与 `docs/增量更新发布手册.md`、`docs/壳更新-下一轮改动清单.md` 中，按第 1 节现有 docs 豁免口径未一并处理。

* **首启协议遮罩把整套 E2E 打红（`main` 自 #89 起连红）**：`restore.html` 的 `#agreementModal` 没有遮罩关闭路径（必须勾选后点「同意并开始使用」），fresh Playwright context 下 `.sv-modal-overlay.show` 持续拦截指针事件 → 34 条点击类用例全灭，另有 4 张视觉基线把遮罩画进了产物。现由 `tests/playwright.config.ts` 的 `storageState` 统一预置 `sv_onboarding_seen_v2` / `sv_agreement_seen_v1`（数据在 `fixtures/test-data.ts` 的 `FIRST_RUN_LOCAL_STORAGE`）。**为什么放 config 而不是某个 helper**：`security.spec.ts` 有 4 条用例不经过 `setupAllMocks()`，逐处预置必然再漏一次。**代价与兜底**：预置后测试再也看不到该浮层，于是新增 `tests/specs/first-run-agreement.spec.ts` 专门守住两格——「未确认时遮罩可见且真实点击被拦截」「勾选同意后 seen 标记落库且跨页面保持」。seen 值须与模板里的 `AGREEMENT_VERSION` 严格相等；改协议版本而未同步此处，会以同样的遮罩拦截红灯复现，不会静默放行。验证：本地真起服务跑 chromium-desktop 全量 233/233（workers=2）；firefox/webkit 由 CI 矩阵覆盖。
* **语言代码可越出 `locales/` 目录读取任意 JSON（CodeQL `py/path-injection` 217/218，真实可利用）**：`_load_translations()` 对未映射语言走 `filename = f"{lang}.json"` 兜底，而 `POST /api/system/locale` 把 `body["locale"]` 原样交给 `set_locale()`，全程无可用语言校验——`../../…` 因此能抵达 `locales/` 之外的任意 `.json`：文件存在与否、能否解析、是否权限不足三种结果在日志与返回值上可区分（存在性预言机），解出的内容还会进翻译缓存。现加 `_LANG_TAG_RE`（BCP-47 主标签 + 最多两个子标签）白名单，非法值**在触碰文件系统之前**返回 None；`zh-TW` 一类合法但未映射的标签仍走通兜底。测试：`tests/test_i18n.py::TestLangCodeCannotEscapeLocalesDir` 两条（越界一律 None + 未映射合法标签仍可加载）。

* **checkpoint 文件名由 sink 侧自守（`py/path-injection` 215/216，当前不可利用但无防线）**：`TaskCheckpoint._path()` 返回 `checkpoint_dir / f"{task_id}.json"`，其下游是 read / write / **unlink**。入站的幂等键正则 `^[A-Za-z0-9_.-]{1,64}$` 确实排除了分隔符，但 `remove_checkpoint()` 也会被历史删除路径以 DB 回读的 task_id 调用——白名单不该只长在入站那一头。现 `_path()` 用 `[A-Za-z0-9_.-]{1,64}` 白名单 `fullmatch` 并**取匹配结果**拼文件名（先前手写 `if "/" in task_id` CodeQL 不认，告警只会换个行号重锚；字符集白名单 + 使用匹配值才是它认得的消毒形态），全由点号组成的 ID（`.` / `..`）一并拒绝，并再断言 resolve 后仍落在 `checkpoint_dir` 内（`ValueError`，不静默降级）。测试：`tests/test_checkpoint_ttl.py::TestTaskIdCannotEscapeCheckpointDir` 两条。

* **演示站 `demo/index.html` 四处 HTML 注入（`js/xss-through-dom` 231–234）**：用户挑的文件名、手输的文件夹路径、表单选中值被拼进 `innerHTML`。已改为 `textContent` / 文本节点落地。真实浏览器对照验证（同一探针脚本）：修复前 `<img src=x onerror=…>` 形态的文件夹路径会**真的生成 `<img>` 节点**、文件名被吃掉成 `.png`；修复后按字面显示、`#fileCard` 只剩预览用的那一个 `<img>`，显存估算文案一字未变、控制台无报错。**风险定性要诚实**：该页是无后端的静态模拟器，四处输入均出自访问者本人（不读 URL 参数，远程不可触发），属自 XSS 面——收口理由是「公开托管在 `reserendipity.github.io` 上不该有注入点」，不是修一个可远程打的洞。

* **CodeQL 其余 6 条 high 复核为误报并已在告警面 dismiss 写理由**：`security/path_guard.py` 3 条（`resolve()` 即消毒动作本身，入参是配置中的白名单条目；该目录属 `docs/CODING_STANDARDS.md` §5.1 禁区，未改码）、两处下载端点的 `FileResponse`（`output_path` 来自服务端任务态且过 PathGuard 才放行）、`tests/test_i18n_completeness.py` 的 `py/bad-tag-filter`（测试内剥标签正则，非安全边界）。对账记录见 `docs/SECURITY_REMEDIATION_TRACKER.md` §2 及其后注。
* **完整性清单的行尾陷阱（`generate_integrity_manifest.py` 写 CRLF → 签名对新克隆失效）**：生成器按宿主文本模式写文件，在 Windows 上产出 CRLF，而 `.gitattributes` 规定 `*.json eol=lf`、`pre-commit` 的 end-of-file-fixer 还会补/改结尾换行——**签在磁盘字节上的签名，与克隆出来的字节不是同一份**，用户端启动自检会误报"清单签名无效"（`integrity_enforce=true` 时直接拒绝启动）。现生成器以 `newline="
"` + 单个结尾换行直接产出 git 存放态字节，顺序不再敏感；`sign_integrity_manifest.py` 的 HMAC 分支补上与 Ed25519 分支同口径的"签完立即回验、失败即非零退出"，把这类事故从人工记顺序变成机器门禁。
* **`.githooks/pre-push` 的幻影引用声明在 rebase/合并中被丢回，导致 `check_local_only_refs --all` 报 5 处**：`docs-consistency.yml` 与 `structure-guard.yml` 跑的都是 `--all`，即**当时 CI 已经会红**。已补回文件级「本地未分发引用：precheck.ps1」声明；现全库 745 个追踪文件零幻影引用。
* **门禁回归测试用条件 skip 换绿灯（CI 上 14 例中 3 例静默失效）**：`tests/test_local_ref_gate.py` 读本机 `git ls-files` 结果再决定是否 skip，而 `AGENTS.md` / `docs/agents/` / `docs/README.md` 只存在于维护者机器 → CI 上这三例必跳。**最终落地形态是 #120 的最小 git 仓夹具**（在 `tmp_path` 里 `git init` 一个可控仓、把 `check_local_only_refs.ROOT` 重定向过去），三种磁盘状态在受控夹具里复现，生产代码不必为测试开洞；本 PR 原先加的 `hits_local_only(..., exists=)` 注入缝因此撤销，只保留一条「真仓库采集器仍有效」的用例作夹具与本仓的对照。
* **`desktop/src-tauri/src/health_check.rs` 被编辑器带进 UTF-8 BOM**（HEAD 版本无 BOM）：已去除。`window.rs` 的既有 BOM 未动（属另一条进行中的桌面壳工作，避免并行改撞车）。
