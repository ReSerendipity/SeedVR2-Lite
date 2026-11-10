# WO-B17 · SeedVR2-Lite CSP S4 Phase 2 执行报告

- 工单：`WO-B17-seedvr2-csp-phase2.md`（批次 W5，大任务）
- 仓库：`C:/Users/Doro/APP/repository/SeedVR2-Lite`
- 分支：`fix/desktop-shell-csrf-unify`，HEAD `4bba55e`（未 commit，未 push）
- 执行日期：2026-11-09（含当日复核决策轮）
- **结论：步骤 1–5 全部完成。应用侧内联样式清零，`style-src` 已在 meta 与响应头两侧同步
  去掉 `'unsafe-inline'`（保留 `'self'` 与字体域）。测试 35 passed + 1 xfailed，RC=0。**

---

## 1. 执行摘要

| 项 | 结果 |
|---|---|
| 模板 / UI JS 内联 `style="` | 36 → **0** |
| 页内 `<style>` 块 | 2 → **0** |
| `setAttribute('style'` | 0 → 0 |
| `app/**/*.py` 里的内联 `style="` | 0 → 0 |
| `.style.cssText`（CSSOM，不受 `style-src` 限制） | 1 处保留（app.js:2806 spinner） |
| **`style-src`** | **`'self' https://fonts.googleapis.com`（`'unsafe-inline'` 已摘，两处原子同改）** |
| `script-src` / `font-src` | 未动（无 `'unsafe-eval'`、未放宽） |
| 测试 | `35 passed, 1 xfailed`，RC=0 |
| 前端冒烟 | `pass=56 / fail=2`，2 处 FAIL 为既有 |
| 视觉等价 | 迁移前后快照仅「内联 → 类名」；收紧后再渲染仅 CSP meta 那一行变化 |

---

## 2. 盘点（步骤 1）

工单「事实基线」为「约 66 处内联 `style="`」，实测已漂移。精确口径（排除 `font-style=`
误报、vendor、纯注释行）：

| 文件 | 内联 `style="` |
|---|---|
| `templates/restore.html` | 19 |
| `templates/settings.html` | 5 |
| `templates/system_status.html` | 4 |
| `static/js/app.js` | 4 |
| `templates/base.html` | 2 |
| `templates/downloads.html` | 1 |
| `templates/index.html` | 1 |
| **合计** | **36** |

另有两类工单未列出、但同样受 `style-src` 管辖的形态，一并处理：

- 页内 `<style>` 块 **2 处**：`downloads.html`（`dl-*` 整页布局）、`settings.html`（设置分栏 + FAQ）。
  处理 `<style>` 元素需要 `style-src` 的 nonce/hash，**只摘 `'unsafe-inline'` 不足以放行**，故迁出。
- `.style.cssText` **1 处**（`app.js:2806`）：CSSOM 写入，`style-src` 不约束，保留。

> 口径复核（2026-11-09 决策轮）：naive grep 在应用侧报出的 2 处 `style="` 均为 SVG 的
> `font-style="italic"` 假阳性——`base.html:75`（品牌 logo SVG）、`settings.html:61`（设置页
> 印章 SVG）；另有 1 处位于 `visibility.js:8` 的块注释里（叙述历史）。三者都不是 DOM 样式属性，
> 真正内联样式为 **0**。

分类结果：36 处全部为**静态样式**（零模板变量绑定、零第三方注入），但其中 4 处存在
「迁了会变」或「迁了会失效」的耦合，见 §3。

---

## 3. 迁移清单与 4 个非机械点（步骤 2、3）

`static/css/style.css` 末尾追加 Phase 2 段（10031 → 10294 行，LF 保持，**未新增 `!important`**），
新增类按页面分组：

- `base.html`：`sv-font-dd-glyph`、`sv-bar-fill-zero`
- `index.html`：`sv-recent-empty-actions`
- `system_status.html`：`sv-gauge-fill-zero`、`sv-vram-num`
- `downloads.html`：整段 `.dl-*`（迁移自页内 `<style>`，35 行）
- `settings.html`：`sv-settings-paths-note`、`sv-about-faq-*` + 整段 `.sv-settings-*`
  （含 @1100 断点，迁移自页内 `<style>`，67 行）
- `restore.html`：`sv2-btn-retry`、`sv2-result-media`、`sv2-result-img`、`sv2-allow-dirs(+body)`、
  `sv2-vae-tier-hint`、`sv2-agreement-{modal,title,text,list,ack}`、
  `sv2-scan-{flex1,check,total,stats}`、`sv-batch-file-list-scanned`、`sv2-allow-dir-del`
- `app.js`（`clearHistoryModal` 动态模板）：`sv-modal-sm`、`sv-clear-history-{text,chk,label}`

4 个非机械点（逐条甄别，均为「不做就等于引入回归」）：

| # | 位置 | 问题 | 处理 |
|---|---|---|---|
| 1 | `#dlRuntimeBar` / `#gpuUtilBar` 的 `width:0%` | 与 JS 的 CSSOM 赋值不可区分，静态迁移会与运行态打架 | 只收「初始零值」语义（`sv-bar-fill-zero` / `sv-gauge-fill-zero`），JS 写入路径不动 |
| 2 | `agreementAck` 内联 `opacity:.5` | 与 `.sv-btn:disabled{opacity:0.45}`（特异性 0,2,0）冲突，直迁会让默认态可见变浅 | 新增同特异性后置 `.sv2-agreement-ack:disabled{opacity:.5}` 锁回原值 |
| 3 | `settings.html` 页内 `.sv-settings-layout{1fr 1.2fr}` | 需压掉 `style.css:2825` 的 `180px 1fr` 与 `:3737(@768)` 的 `1fr` | 整段搬到文件末尾，层叠结果与「页内块在文件之后」一致；测试断言其为最后一处顶层定义 |
| 4 | `sv-batch-file-list` 内联 `max-height:280px` | 基础类给的是 `150px` | 新增 `.sv-batch-file-list-scanned` 置于末尾（同特异性后置覆盖） |

迁写方式：Python 脚本逐条断言「原文恰好命中 1 次」后按字节写回，写回后再断言 CR=0，
全程保持 LF（避免 diff 膨胀）。分两批执行，每批后跑既有测试，均为 15 passed。

**记录在案的既有缺陷（非本工单引入、本轮未修）**：`style.css:6566`
`.sv-progress-bar{width:100%!important}` 使 `downloads.html` 的 `dlRuntimeBar.style.width`
长期无效——下载进度条宽度本就不动。已在 `downloads.html` 该行加注释标注，修复不在本工单范围。

---

## 4. 步骤 4：摘 `'unsafe-inline'`（已执行）

### 4.1 决策依据

初次盘点判定「清零不成立」；复核确认清零在应用侧成立（§2 口径复核），故按决策直接摘除，
两处**原子同改**：

| 文件 | 改动 |
|---|---|
| `app/integrated_app/middleware/security_headers.py:46`（`_DEFAULT_CSP`） | `style-src 'self' 'unsafe-inline' https://fonts.googleapis.com` → `style-src 'self' https://fonts.googleapis.com` |
| `app/integrated_app/templates/base.html:10`（meta CSP） | 同上 |

保留 `'self'`（自有样式表）与 `https://fonts.googleapis.com`（决策 D22：字体按需加载、
不自托管）；`script-src`（`'self'` + nonce/回退）与 `font-src`
（`'self' data: https://fonts.gstatic.com`）**一字未动**，未引入 `'unsafe-eval'` 或任何放宽项。

### 4.2 实机核对（六页面一致）

| 页面 | 响应头 style-src | meta style-src |
|---|---|---|
| `/`、`/restore`、`/settings`、`/downloads`、`/history`、`/system-status` | `'self' https://fonts.googleapis.com` | `'self' https://fonts.googleapis.com` |

响应头 `script-src 'self' 'unsafe-inline'`（S2 既有语义，未动）、`font-src 'self' data: https://fonts.gstatic.com`。

### 4.3 负控（四条，均已跑）

1. **端到端闸门负控**：把 `_DEFAULT_CSP` monkeypatch 回含 `'unsafe-inline'` 的版本，
   同一闸门断言对真实响应头**如期判红**——证明绿断言咬住的是真实响应头，不是空转。
2. **谓词负控**：`style-src` 判定函数对「放回 unsafe-inline」的 CSP 字符串判红。
3. **扫描器负控**：注入 `style="margin-top:12px"` 的探针必判红；`font-style="italic"`
   不误判；`<style>` 块可检出。
4. **迁移锁负控**（实跑）：把 `object-fit:contain` 改成 `cover`、删掉 `.sv2-agreement-ack:disabled`
   的 `opacity` → 断言如期判红。

### 4.4 Report-Only 观察期

**`Content-Security-Policy-Report-Only` 观察期属上线后动作，本单不做**（路线图 §3 的双头
观察流程留给发布前执行）。

### 4.5 已知偏差（挂账，未修）

`desktop/src/desktop-bridge.js` 仍有 **20 处内联 `style="`**，全在更新弹窗（下载进度 / 失败 /
完成 / 已是最新各态）。该文件由 `desktop/src-tauri/src/window.rs:23`（`include_str!`）与
`:153`（`initialization_script`）注入**每一个内部页面**；`main.rs:249-256` 明确
「更新对话框在主窗桥接层弹出、无需独立窗口」，`desktop/src` 下只有 `index.html`、
**没有 `update.html`**，托盘「检查更新」（`tray.rs:120`）也直接 emit 给主窗。因此这些内联样式
**落在后端页面的 `style-src` 之下**：**若启用该桌面壳，更新弹窗会掉样式**
（`dlgEl.style.cssText` 与自绘标题栏是 CSSOM 写入，不受影响）。

`desktop/**` 不在本工单「允许改动」清单内，本轮不迁移；已以 strict-xfail 用例挂账
（`TestS4Gate::test_desktop_bridge_inline_styles_are_migrated`）：迁移该文件后用例会 XPASS 判红，
提示删标记、断言转硬判据。迁移方案见路线图 §2e「下一步」。

---

## 5. 测试命令与 rc（如实回报）

```
# 1) 三个 CSP/可见性相关文件
.\.venv\Scripts\python.exe -m pytest tests/test_csp_nonce.py tests/test_visibility_state_machine.py tests/test_csp_style_migration.py -q
→ 35 passed, 1 xfailed, 1 warning   RC=0

# 2) 新增护栏文件单跑
.\.venv\Scripts\python.exe -m pytest tests/test_csp_style_migration.py -q
→ 20 passed, 1 xfailed             RC=0

# 3) 既有 CSP / 可见性
.\.venv\Scripts\python.exe -m pytest tests/test_csp_nonce.py tests/test_visibility_state_machine.py -q
→ 15 passed                        RC=0

# 4) 前端冒烟（jsdom 装在 agent 工作区，未入仓）
$env:NODE_PATH="<agent-workspace>\tmp\jsdom\node_modules"; node tests\frontend\smoke.js
→ RESULT: pass=56 fail=2           RC=1
   FAIL「存在 Google Fonts 链接」、FAIL「设置页含表单」——两者在 HEAD 上同样存在，非本轮回归
   （迁移前基线跑亦为 pass=56 / fail=2）

# 5) 渲染 + 快照对平
.\.venv\Scripts\python.exe scripts\render_pages.py   → 4 页全部 [OK]
迁移前快照 vs 迁移后快照：index 6 行、restore 43、settings 92、history 4
  差异仅「内联 style → 类名」与「页内 <style> 块消失」
收紧后再渲染 vs 迁移后快照：每页 2 行（=1 删 +1 增），且**只有 CSP meta 那一行**
```

> 环境备注：仓库根 `python` 无 pytest，必须用 `.venv\Scripts\python.exe`（3.12.10 / pytest 9.1.1）。

---

## 6. `git diff` 范围（本工单改动）

```
 app/integrated_app/middleware/security_headers.py |  16 +-   (CSP 收紧 + 注释同步)
 app/integrated_app/templates/base.html            |  17 +-   (内联类 + meta 收紧)
 app/integrated_app/static/css/style.css           | 359 +-   (Phase 2 段)
 app/integrated_app/static/js/app.js               | 131 +-
 app/integrated_app/templates/downloads.html       |  39 +-
 app/integrated_app/templates/history.html         |   5 +-   (Phase 1 既有)
 app/integrated_app/templates/index.html           |  10 +-
 app/integrated_app/templates/restore.html         | 193 +-
 app/integrated_app/templates/settings.html        |  78 +-
 app/integrated_app/templates/system_status.html   |  24 +-
 docs/CSP_TIGHTENING_ROADMAP.md                    | 137 +-
 11 files changed, 701 insertions(+), 308 deletions(-)
```

新增（未跟踪）：`static/js/visibility.js`（Phase 1）、`tests/fixtures/visibility_state_machine_harness.js`
（Phase 1）、`tests/test_visibility_state_machine.py`（Phase 1）、`tests/test_csp_style_migration.py`
（本轮）、`reports/WO-B17-csp-phase2.md`（本报告）。

**未触碰**：`pyproject.toml`、`requirements*.txt`、`uv.lock` 的工作区改动为既有状态（非本轮产生）；
`desktop/**` 未改。未 commit、未 push。

---

## 7. 遗留与后续（不阻塞本单）

| # | 项 | 状态 | 建议 |
|---|---|---|---|
| 1 | `desktop/src/desktop-bridge.js` 20 处内联样式（更新弹窗） | 已知偏差，strict-xfail 挂账 | 迁移为 `style.css` 组件类（桥接不能新增样式表），动态值走 CSSOM；同批外置 `desktop/src/index.html` 的页内 `<style>` |
| 2 | Report-Only 观察期 | 本单不做（上线后动作） | 发布前按路线图 §3 双头观察 ≥1 周，零告警后切强制头 |
| 3 | `style.css:6566` `.sv-progress-bar{width:100%!important}` | 既有 bug，未修 | 单独立项（去 `!important` 需连同 `dlRuntimeBar` 的 CSSOM 写入一起看） |
| 4 | 工单基线漂移（66 → 36 + 2 处 `<style>` 块） | 已在路线图 §2e 重述 | 后续同类工单把「页内 `<style>` 块」与「Python 侧拼装」写入盘点口径 |

---

## 8. 验收自查

- [x] 步骤 1 盘点（含 `setAttribute('style'` / `cssText` / 页内 `<style>` 块 / Python 侧拼装，超工单口径部分已标注）
- [x] 步骤 2 迁移清单（文件:行 → 类名，见 §3）
- [x] 步骤 3 分批迁移 + 每批跑既有测试（15 passed / 批），未 commit，文件清单见 §6
- [x] 步骤 4 摘 `'unsafe-inline'`：meta 与响应头原子同改，保留 `'self'` 与字体域；四条负控已跑
- [x] 步骤 5 路线图更新（§2b 事实更正、S4 状态、§2e 决策与偏差、§4 风险项）与本报告
- [x] 视觉行为不变：迁移前后快照仅内联→类名；收紧后仅 CSP meta 一行变化；冒烟与迁移前一致
- [x] 红线：未加 `'unsafe-eval'`、未放宽 `script-src`、未改业务逻辑、未改无关模板结构、未 push
- [x] Report-Only 观察期已注明「属上线后动作，本单不做」
- [x] 已停住等验收
