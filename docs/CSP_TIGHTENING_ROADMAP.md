# CSP 收紧路线图（评估报告 R8 · 2026-09-06）

> 现状基线：`middleware/security_headers.py` 的 `_DEFAULT_CSP` 与
> `templates/base.html` 的 meta CSP。评估报告 R8 指出 `'unsafe-inline'`
> 兜底与 Google Fonts CDN 依赖两点缺口；本路线图给出收紧路径与验收口径。
> 本文是「路线」承诺的落地物——报告原文即定位为路线而非本轮实施项。

## 1. 现状

- **script-src 'self' 'unsafe-inline'**：现代浏览器下 per-request nonce
  存在时忽略 `'unsafe-inline'`（CSP2/3 语义），实际防护由 nonce 承担；
  无 nonce 的渲染回退（`render_page` 未注入）与旧内核则退化为放行。
- **style-src 'self' 'unsafe-inline' https://fonts.googleapis.com**、
  **font-src https://fonts.gstatic.com**：内联 style + 外部字体 CDN。
- 约束（KNOWN_ISSUES #54 / 陷阱 #7）：meta CSP 与响应头 CSP **取交集**，
  任何一边收紧不同步都会拦截页面自身资源——两处必须原子同改。

## 2. 收紧步骤（按依赖顺序）

> **2026-09-06 盘点修订（S1 近乎完成，S2 逼近即时可行）**：全模板 grep
> `on(click|change|input|submit|keydown|load|error|…)=` 共 **0 处**内联事件属性；
> JS 的 100 处 `.onclick =` 为代码赋值（非 HTML 属性，不受 CSP script-src 约束）；
> 22 处 innerHTML 拼接不含 on*。base.html 6 个 script：2 个内联均带 nonce、
> 4 个外链同源。**S2 唯一残留障碍** = `history.html:276/:335` 两处
> `hx-on::after-request`（htmx 经 `new Function` 求值——注意：现行 CSP 无
> 'unsafe-eval'，此 2 处在当前策略下**疑似已静默失效**，删除前先人工确认
> 刷新链路现状）。等价改法：app.js 监听 `htmx:afterRequest` 延时 click
> （<15 分钟工作量 + 一轮 e2e 回归）。58 处模板 `style="…"`（restore.html 占 39）
> 仍是 S4 的主体工作量。

| 步骤 | 内容 | 验收 |
|---|---|---|
| S1 | ~~盘点全部模板/JS 内联事件处理器~~ → **盘点已完成（见上）**：模板零内联事件属性，仅 2 处 htmx `hx-on` 待等价迁移 | ~~grep 模板零 `on*=` 属性~~ ✅（hx-on 迁移后达成） |
| S2 | `script-src` 移除 `'unsafe-inline'`（先完成 hx-on 迁移）：meta 与响应头**同一提交**内同步移除 | `tests/test_csp_nonce.py` 全绿 + 断言 CSP 不含 unsafe-inline |
| S3 | 字体本地化：下载站酷小薇/马善政楷书 woff2 入 `/static/fonts/`（注意字体文件自身的再分发许可），`style-src/font-src` 移除 Google Fonts 域 | UI 字体切换器回归 + CSP 不含 fonts.googleapis.com |
| S4 | 内联 style 迁移（最大工作量，58 处属性）：组件级 `style=` 迁外部样式表/类切换后，`style-src` 移除 `'unsafe-inline'` | 全 UI 视觉回归截图对比 |

## 3. 发布策略

每步先以 **`Content-Security-Policy-Report-Only`** 双头并行观察 ≥1 周
（上报端点可复用 `/api/system/ping` 级轻量端点或控制台采集），零告警后
切换强制头并保留一步可回滚（meta/头两处均在同一提交内，git revert 即回滚）。

## 2b. 实施进度（2026-09-06 自主落地轮）

- **S1 ✅ 完成**：盘点证实模板零内联事件属性、JS 零拼接 `<script>`/on*（见上）。
  唯一残留 `history.html` 两处 `hx-on::after-request` 已迁移为 document 级
  `htmx:afterRequest` 委托监听——且经核实该属性经 htmx `new Function` 求值，
  在现行无 `'unsafe-eval'` 的 CSP 下**本已静默失效**，迁移同时修复了功能。
- **S2 ✅ 完成**：`base.html` meta CSP 改为 `script-src 'self'{% if csp_nonce %}
  'nonce-…'{% else %} 'unsafe-inline'{% endif %}`——nonce 上下文彻底移除
  `unsafe-inline`，无 nonce 异常路径保留回退防白屏；全部 7 处内联 script 由
  `render_page` 无条件注入的 per-request nonce 覆盖。响应头侧（`security_headers.py`）
  无 per-request nonce 可拼，按 CSP 交集语义（meta 更严者生效）保留字符串并注释。
  回归：`tests/test_csp_nonce.py` 新增「nonce 存在时 script-src 无 unsafe-inline」
  正向断言，既有回退断言继续通过。桌面壳桥接经 `initialization_script` 注入页面
  **脚本上下文**，其脚本自身不受 `script-src` 约束；**但它创建的 DOM 受页面
  `style-src` 约束**——2026-11-09 WO-B17 复核更正：原先「不受页面 CSP 约束」的
  表述只对脚本成立，对内联样式不成立（详见 §2e 阻塞项 1）。
- **S3 ☑ 评估后关闭（不做自托管）**：装饰字体加载链已是完全按需——默认启动
  **零第三方请求**（`app.js` `ensureWebfonts` 仅在用户展开字体菜单或已保存装饰
  字体选择时触发）；界面基字体（DM Sans / Instrument Serif）本已本地化。
  自托管 11 个 Google 字体家族需引入 30–60MB CJK 子集碎片（unicode-range
  分片），与便携包体积治理（core ~1.9GB、体积注释三处同源）冲突且用户收益为零。
  CSP 的 `fonts.googleapis.com/gstatic.com` 域因此保留。决策 D22。
- **S4 ✅ 应用侧完成**：调查结论见 §2c；**可见性状态机重构（Phase 1）已完成** 见 §2d；
  **Phase 2 于 2026-11-09 落地**，应用侧内联样式清零，`style-src` 已两侧同步去掉
  `'unsafe-inline'`；桌面壳桥接的内联样式作为已知偏差挂账（超出工单允许改动清单），见 §2e。

## 2c. S4 style= 深度调查结论（2026-09-07，决策=暂停执行，等待可见性状态机重构）

盘点：58 处模板内联 `style=` **全部静态**（零模板变量绑定），其中 21 处为
`display:none` 系——本可机械迁移，但深度调查证明**与 JS 可见性状态机深度耦合**：

- `style.css:3981` 已存在 `.sv-hidden{display:none!important}`；
- JS 有 59 处 `el.style.display = '…'` 写操作与 **8 处
  `el.style.display !== 'none'` 读取判断**（app.js:1076/1085/1202/1421/3144、
  restore.html:2095/2114/2351 等）——迁移后内联读取值为空串，
  `'' !== 'none'` 恒真 → 可见性判断**翻转**；且 `!important` 令
  `style.display='flex'` 显示失效。
- 结论：等价迁移不可行；style-src 移除 `'unsafe-inline'` 的**前置依赖**是把
  可见性控制整体重构为 classList 状态机（show/hide 单一出口 + 禁止内联
  display 读写），属交互逻辑迁移而非样式搬运，必须依托视觉回归基线
  （E1 已恢复，2026-09-07）+ e2e 断言同步改造后分批推进。
- 剩余 37 处一次性静态样式（max-width/padding/calc 阴影等）可低风险迁移，
  但在状态机重构前移除 unsafe-inline 不成立，单独迁移无门禁收益——留待
  状态机批次一并处理。

执行状态：S1 ✅ / S2 ✅ / S3 ✅（评估关闭）/ S4 Phase 1 ✅（2026-11-09，见 §2d）/
**S4 Phase 2 ✅（应用侧清零 + `style-src` 已收紧，见 §2e；桌面壳桥接内联样式为已知偏差，
Report-Only 观察期留待上线前执行）**。

## 2d. S4 Phase 1 落地进度（WO-B02 · 2026-11-09）

§2c 识别出的阻塞点按「先立单一出口、再迁内联」的顺序解除，本阶段交付如下。

- **状态机单一出口**：新增 `static/js/visibility.js`（全局 `SvVisibility`），
  `show(el, mode?)` / `hide(el)` / `isVisible(el)` / `set(el, visible, mode?)` /
  `showId` / `hideId`。隐藏统一挂 `.sv-hidden`（复用既有的
  `display:none!important`）；显示态若需复现原内联
  `display:flex|block|inline-flex|grid`，挂对应 `.sv-vis-*` 模式类，**不再写内联**。
  入口对 `null`/非元素安全返回 `false`；`show`/`hide` 先清空全部模式类，避免残留互斥。
  `base.html` 中该脚本位于 htmx 之后、`app.js` 与各页内联脚本之前。
- **迁移量**（基线实测，较 §2c 的 58/21/59/8 因新增代码已漂移）：
  模板内联 `style="` 66 处 → 34 处，其中**含 `display` 的 32 处全部迁走**
  （原 `display:none` 26 处、静态 `display:flex|inline-block` 6 处）；
  JS 侧 `el.style.display` 98 写 + 11 读 + 10 处三元共 119 处全部改走 `SvVisibility`
  （含 `app.js` 语言切换遮罩的 `cssText`、`clearHistoryModal` 动态模板中的内联
  `display:flex`）。
- **两处非机械改动**：
  1) `#compareCard` 原为 `display:flex !important`，id 特异性会压过 `.sv-hidden`，
     状态机无法隐藏它；迁移前内联 `display:'none'` 同样被压过——即对比卡此前
     **一直显示**，属既存缺陷。去 `!important` 是修复，`display:flex` 保留。
  2) `.sv-vis-*` 与承接原内联静态属性的类一律追加到 `style.css` 末尾，靠同特异性
     后置覆盖组件基础规则（如 `.sv2-error-card{display:none}`）；不新增 `!important`。
- **验证**：`tests/test_visibility_state_machine.py`（11 例：静态面 / CSS 面 /
  六页面渲染面 / node 行为面 / 负控）、`tests/fixtures/visibility_state_machine_harness.js`
  （不依赖 jsdom，加载真实模块，11 条断言）。负控（删掉 `hide()` 的
  `classList.add` 后 harness 必须判红）已固化为用例。全量门禁 ruff / black /
  mypy / api contract / pytest 1994 passed，覆盖率 62.20%（阈值 55%）全绿。
- **本阶段未做**（Phase 2 前置）：剩余 34 处非 display 内联样式（margin / padding /
  width / max-width / color / transform 等）与 `app.js` 中 4 处运行时拼的
  `style="…"` 字符串；`style-src` 移除 `'unsafe-inline'` **仍不成立**。
  **视觉回归未执行**：本地缺 jsdom / Playwright 依赖，`tests/specs/*.spec.ts` 中对
  compareCard / batchToolbar / progressCard 显隐的断言需先补跑（尤其 `#compareCard`
  现在真的可被隐藏，行为与迁移前不同）。

## 2e. S4 Phase 2 落地进度（WO-B17 · 2026-11-09）

§1 的「58 处」与 §2d 的「34 处」再度漂移，本轮以脚本重扫为准。
**结论：应用侧内联样式已清零；`style-src` 保持 `'unsafe-inline'` 不动**——清零在工单
允许改动范围内不成立，唯一阻塞项是桌面壳桥接（见下「阻塞项」）。

### 盘点（Phase 1 后实测，排除 `font-style=` 误报、vendor 与纯注释行）

| 面 | 数量 | 分布 |
|---|---|---|
| 模板 / UI JS 内联 `style="` | 36 | restore 19、settings 5、system_status 4、app.js 4、base 2、downloads 1、index 1 |
| 页内 `<style>` 块 | 2 | downloads.html（dl-* 整页布局）、settings.html（设置分栏 + FAQ） |
| `setAttribute('style'` | 0 | — |
| `.style.cssText` | 1 | app.js:2806 spinner（CSSOM 写入，不受 `style-src` 限制） |

工单只点了 `style="`，两处页内 `<style>` 块未被列出——处理 `<style>` 元素需要 `style-src`
里的 nonce/hash，仅摘 `'unsafe-inline'` 不足以放行，故一并迁入 `style.css`。

### 落地

- `style.css` 末尾追加 Phase 2 段（10031 → 10294 行，LF 保持，**未新增任何 `!important`**）。
- 36 处内联 + 2 处页内块全部迁走，分两批执行（A：base / index / system_status / downloads /
  settings；B：restore / app.js），每批后跑既有测试 15 passed。
- 逐条甄别出 4 个非机械迁移点：

| # | 位置 | 问题 | 处理 |
|---|---|---|---|
| 1 | `#dlRuntimeBar` / `#gpuUtilBar` 的 `width:0%` | 与 JS 赋值的 CSSOM 写入不可区分，不能当静态属性搬 | 只把「初始零值」语义收成 `sv-bar-fill-zero` / `sv-gauge-fill-zero`，JS 写入路径不动 |
| 2 | `agreementAck` 内联 `opacity:.5` | 与 `.sv-btn:disabled{opacity:0.45}`（特异性 0,2,0）冲突，直接迁会变 0.45 | 新增同特异性后置 `.sv2-agreement-ack:disabled{opacity:.5}` 锁回原值 |
| 3 | `settings.html` 页内 `.sv-settings-layout{1fr 1.2fr}` | 需压掉 style.css:2825 的 `180px 1fr` 与 :3737(@768) 的 `1fr` | 整段搬到文件末尾，层叠结果与「页内块在文件之后」一致；测试断言它是最后一处顶层定义 |
| 4 | `sv-batch-file-list` 内联 `max-height:280px` | 基础类给的是 150px | 新增 `.sv-batch-file-list-scanned` 置于末尾 |

- **既有缺陷（与本阶段无关，仅记录）**：`style.css:6566`
  `.sv-progress-bar{width:100%!important}` 令 `dlRuntimeBar.style.width` 长期无效——下载
  进度条宽度本就不动；已在 `downloads.html` 对应行加注释标注，修复不在本工单范围。

### 验证

- `pytest tests/test_csp_nonce.py tests/test_visibility_state_machine.py tests/test_csp_style_migration.py`
  → **31 passed，RC=0**。
- 新增 `tests/test_csp_style_migration.py`（16 例）：静态面（零内联属性 / 零 `<style>` 块 /
  零 `setAttribute('style')`）、六页面渲染面、**迁移锁**（37 个类的声明值与迁移前逐项比对）、
  设置页层叠顺序、页内私有类的定义完整性、S4 闸门不变式、扫描器三条负控。
  迁移锁负控已实跑：把 `object-fit:contain` 改成 `cover` 并删掉 `:disabled` 的 `opacity`
  → 断言如期判红。
- jsdom 冒烟 `node tests/frontend/smoke.js` → **pass=56 / fail=2**，与迁移前完全一致；
  两处 FAIL（「存在 Google Fonts 链接」「设置页含表单」）在 `HEAD` 上同样存在，非本轮回归。
- 渲染快照逐行 diff（`scripts/render_pages.py`；迁移前快照 vs 迁移后快照，且本轮重渲染
  与快照对平 0 差异）：index 6 行、restore 43 行、settings 92 行、history 4 行——差异只有
  「内联属性 → 类名」与「页内 `<style>` 块消失」，无其它结构变化。

### 决策与收紧（2026-11-09 复核后执行）

初次盘点判定为「清零不成立、保持 CSP 原样」；随后复核确认：应用侧**真正**的内联样式为 0——
naive grep 报出的 2 处属 SVG 的 `font-style="italic"` 假阳性（被本文件的 `(?<![A-Za-z0-9_$-])style=`
口径排除），`app.js:2806` 的 `spinner.style.cssText` 属 CSSOM 写入、不受 `style-src` 限制。
故**按其决策直接摘掉 `'unsafe-inline'`**：

- `middleware/security_headers.py` 的 `_DEFAULT_CSP` 与 `templates/base.html` 的 meta **原子同改**：
  `style-src 'self' https://fonts.googleapis.com`（保留 `'self'` 与字体域，决策 D22）；
- `script-src`（`'self'` + nonce/回退）与 `font-src` 未动，未新增任何宽松项；
- 版面/组件样式全部走 `style.css` 类，运行时态走 CSSOM，页面内不再有任何内联样式属性。

**已知偏差（本轮不修，挂账）**：`desktop/src/desktop-bridge.js` 仍有 20 处内联 `style="`，全在
更新弹窗（下载进度 / 失败 / 完成 / 已是最新各态）。该文件由
`desktop/src-tauri/src/window.rs:23`（`include_str!`）与 `:153`（`initialization_script`）
注入**每一个内部页面**；`main.rs:249-256` 写明「更新对话框在主窗桥接层弹出、无需独立窗口」，
`desktop/src` 下只有 `index.html`、**没有 `update.html`**，托盘「检查更新」（`tray.rs:120`）
也直接 emit 给主窗——因此这 20 处内联样式**落在后端页面的 `style-src` 之下**：若启用该桌面壳，
更新弹窗会掉样式（`dlgEl.style.cssText` 与自绘标题栏是 CSSOM 写入，不受影响）。
该文件不在 WO-B17「允许改动」清单内，故不在本单迁移，以
`tests/test_csp_style_migration.py::TestS4Gate::test_desktop_bridge_inline_styles_are_migrated`
（strict-xfail）挂账：迁移该文件后用例 XPASS 判红，提示删标记、断言转硬判据。

**Report-Only 观察期属上线后动作，本单不做**（§3 的观察流程留给发布前执行）。

### 下一步

1. 迁移 `desktop/src/desktop-bridge.js` 的 20 处内联样式：优先复用 `style.css` 已有组件类
   （桥接无法新增样式表），动态值走 CSSOM 赋值；`desktop/src/index.html` 的页内 `<style>`
   同批一并外置，避免日后启用 Tauri CSP 时再返工。
2. 上线前按 §3 以 `Content-Security-Policy-Report-Only` 双头观察 ≥1 周，零告警后切强制头。

## 4. 风险与边界

- S2 前必须完成 S1，否则内联脚本被浏览器静默拦截（表现为 UI 无报错失效）。
- 桌面壳（Tauri）通过 `initialization_script` 注入桥接代码，不走 HTTP 响应头；
  **2026-11-09 复核：桥接创建的内联样式受页面 `style-src` 约束**（脚本自身不受 `script-src`
  约束，二者不可混谈）。`style-src` 收紧后，桥接里残留的 20 处内联样式（更新弹窗）成为
  **已知偏差**：启用该壳时弹窗会掉样式，迁移方案见 §2e「下一步」。
- 明确不做的：CSP 放宽类变更（新增 CDN 域、data: 脚本源等）一律禁止。
