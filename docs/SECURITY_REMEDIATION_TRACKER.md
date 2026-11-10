# SeedVR2-lite 安全整改追踪表

> 配套 Image-DuoModel 家族安全深度评估（`SECURITY_ASSESSMENT_v2.0.0.md`，17 项发现）。
> 本文记录 SeedVR2-lite 已落地的安全整改项与剩余项，便于跨仓统一对账。
> 【对账注 2026-09-15】09-06 增量审计（docs/reports/SECURITY_AUDIT_SeedVR2-lite_2026-09-06.md，R1–R4；该报告属维护者本地文件，未随仓库分发）此前未纳入本表；经核对：R1 browse-dir/open-explorer 纳入 PathGuard 白名单、R2 水印 fail-open → 侧车元数据策略均已随 1.5.6 修复（见 .github/SECURITY.md 与 CHANGELOG 1.5.6）。
> 【核实注 2026-11-10】R3/R4 已核对并闭合：**R3 留存清理缺口**已由数据治理 P0-1 落地——`app/integrated_app/services/output_retention.py`（按年龄/数量/磁盘水位回收）配套 `tests/test_output_retention_watermark.py`、`tests/test_pinned_retention.py`，outputs 与 uploads 均有周期清理任务（`app/integrated_app/app_server.py`）；**R4 SAST 名实不符**已证实为实——`.github/workflows/security.yml` 名为 `Security Scan (SAST)` 且实际运行 Semgrep 1.173.0 ERROR 硬门禁 + Trivy secret scan，另有 `codeql.yml` 覆盖 python/javascript/typescript/rust。

## 1. 根因门禁（最高优先级，已落地）

**配置幻觉（Phantom Control）**：config.yaml 声明了安全控制，但代码未消费、
不报错、不告警、测试不失败 → 假安全感。SeedVR2-lite 的根因门禁为
`scripts/check_config_refs.py`（SeedVR2 适配版），作为 CI 质量门禁接入
`.github/workflows/ci.yml` 的 `quality-gate` job。

与 Image-DuoModel / TTS-MultiModel 的差异（适配点）：
- SeedVR2 的配置以 **dict** 形式经 `load_config()` 注入 `app.state.config`，
  代码中通过 `config.get("runtime", {}).get("security", {}).get("key", default)` 消费，
  故门禁同时捕获属性链、`.get()` 字符串键与字典下标 `obj["key"]`。
- 安全配置嵌在 **`runtime.security:`**（非顶层 `security:`），门禁遍历根为
  `runtime.security`。

门禁覆盖：
1. `config.yaml` 的 `runtime:` 段每个键须对应 `RuntimeConfig` 字段；
2. `config.yaml` 的 `runtime.security:` 每个键须对应 `RuntimeSecurityConfig` 字段
   （防 `extra="ignore"` 静默吞掉幽灵键）；
3. `runtime.security:` 每个键都须被代码真实消费（声明即消费，否则判失败）。

已通过负向测试证明门禁非 no-op：在临时 config 中加入 `phantom_secret_key` 或
移除某键的消费后，门禁均报 `[FAIL]`；真实仓库下 6 个安全键全部消费 → `[PASS]`。

## 2. 整改状态表

| 编号 | 类别 | 措施 | 状态 | 落地文件 |
|------|------|------|------|----------|
| G-01 | 配置幻觉根因门禁 | `check_config_refs.py`（runtime.security 适配）+ CI 接入 | ✅ 已落地 | `scripts/check_config_refs.py`、`.github/workflows/ci.yml` |
| M-02 | 安全响应头缺失 | `SecurityHeadersMiddleware`：CSP / nosniff / X-Frame-Options / Referrer-Policy / COOP，默认开启，最外层注册 | ✅ 已落地 | `app/integrated_app/middleware/security_headers.py`、`app/integrated_app/app_server.py` |
| C-01 | 0.0.0.0 监听 | `config.yaml` 仅 `127.0.0.1`；`ServerConfig.host` 强制回环校验器（`host_must_be_loopback`）；CI `security-assertions` 禁 0.0.0.0 | ✅ 既有 | `config_models.py`、`ci.yml` |
| H 系 | 鉴权/速率/CSRF | `basic_auth.py` / `rate_limit.py` / `csrf.py` 中间件齐备 | ✅ 既有 | `app/integrated_app/middleware/` |
| M 系 | 路径白名单/完整性 | `path_guard.py`、`integrity_selfcheck.py`、`integrity_manifest.json(.sig)`、`weight_encryption.py`、`watermark.py` 齐备 | ✅ 既有 | `app/integrated_app/security/` |
| I-01 | 语言代码可越出 `locales/` | `_load_translations()` 的未映射语言兜底曾无条件 `f"{lang}.json"`；`POST /api/system/locale` 不校验 locale，`../` 可读取任意 `.json`（存在性预言机 + 内容进翻译缓存）。现按 `_LANG_TAG_RE`（BCP-47 主标签 + ≤2 子标签）白名单放行，非法值直接返回 None 且**不触碰文件系统** | ✅ 已修 | `app/integrated_app/i18n.py`、`tests/test_i18n.py` |
| K-01 | checkpoint 任务 ID 越界 | `_path()` 下游是 read/write/unlink，此前只靠入站幂等键正则把关。现 sink 侧自守：`[A-Za-z0-9_.-]{1,64}` 白名单 `fullmatch` 并取匹配值拼文件名、拒绝全点号 ID，再断言 resolve 后仍在 `checkpoint_dir` 内 | ✅ 已修（工具仍报，见下） | `app/integrated_app/checkpoint.py`、`tests/test_checkpoint_ttl.py` |
| D-01 | 演示站 HTML 注入 | `demo/index.html` 四处把「用户文件名 / 手输路径 / 表单值」拼进 `innerHTML`。改为 DOM API 组装 + `textContent`（真实浏览器验证：修复前会真的注入 `<img>` 且文件名被吃掉成 `.png`，修复后按字面显示且估算文案一字未变） | ✅ 已修 | `demo/index.html` |

> **CodeQL 高危告警对账（2026-09-20，起始 15 条 high）**
>
> 判据以 **main 上 `493c314` 重扫后的告警状态**为准（`code-scanning/alerts` 按
> `most_recent_instance.ref == refs/heads/main` 统计），结果为
> **high open 0 / dismissed 14 / fixed 4**（total 18 含重锚产生的新实例）。
>
> - **代码改动后旧实例自然消失（4 条）**：`js/xss-through-dom` 231–234（demo 四处把用户文件名 /
>   手输路径 / 表单值拼进 `innerHTML`，改 DOM 组装）。
> - **修复有效、但工具仍报，已按 `false positive` 关闭（7 条）**：
>   `py/path-injection` 217/218（i18n 语言代码白名单，**原始缺陷真实可利用**：
>   `POST /api/system/locale` 的 locale 无校验，可越出 `locales/` 读任意 `.json`）；
>   214/215/216 + 重锚新增 260/264（checkpoint：`_path()` 已是白名单 fullmatch + 取匹配值 +
>   `is_relative_to` 断言，`checkpoint_dir` 来自管理员本地 config）。CodeQL 不把
>   `Path.resolve()` / `os.path.join` 前的自定义校验认作 sanitizer，改代码迁就它没有意义。
> - **修复后新增 1 条，同按 `false positive` 关闭**：`demo/index.html` 的
>   `img.src = URL.createObjectURL(f)`——blob URL 由浏览器自造，`<img>` 的 src 无脚本执行面。
> - **复核即误报、合并前已关闭（6 条）**：`security/path_guard.py` 的 3 条（`resolve()` 本身就是
>   消毒动作，入参是配置里的白名单条目；该目录属禁区不改码）、两处下载端点的 `FileResponse`
>   （`output_path` 来自服务端任务态且已过 PathGuard）、`tests/test_i18n_completeness.py` 的
>   `py/bad-tag-filter`（测试内剥标签正则，非安全边界）。
>
> **方法教训（写给下一次做 triage 的人，包括我自己）**：不要用 PR 检查里"new alerts introduced
> by this PR"的清单来判断某条告警是否已消除——那个口径只覆盖**改动行上**的实例，旧实例即使
> 仍在报也不会出现在里面。只有分支重扫后的 `alert.state` 算数。

> 注：SeedVR2-lite 安全基线较成熟（auth/CSRF/rate-limit/path-guard/完整性校验/
> 权重加密/水印均已具备），本仓重点补强的是「配置-实现一致性根因门禁」与「安全
> 响应头中间件」两项此前缺口。

## 3. 日常纪律（与家族一致）

1. **新增 config.yaml 安全键**：必须同步在 `RuntimeSecurityConfig` 声明，并在代码
   中真实读取该字段；否则 `check_config_refs.py` 在 CI 直接判 `[FAIL]`。
2. **删除安全键**：同步删除配置模型字段与代码读取点，避免悬空引用。
3. **禁止绕过门禁**：CI 中该步骤为真实门禁，不得加 `|| true`。
4. **改核心安全模块后重算完整性清单**：`python scripts/generate_integrity_manifest.py`。

## 4. 跨仓对账

| 仓库 | 根因门禁 | 安全头中间件 | 提交状态 |
|------|----------|--------------|----------|
| Image-DuoModel | ✅ | ✅ | 已提交 |
| TTS-MultiModel | ✅ | ✅ | 已提交（本地） |
| SeedVR2-lite | ✅ | ✅ | 本次落地 |
| MiniMax-H3-Lite | 待适配（@dataclass 无 config.yaml） | 审计中 | 只读审计 |
| SpiritPal | 不适用（Rust） | 审计中 | 只读审计 |
| DraftPeek | 不适用（Kotlin） | 审计中 | 只读审计 |
