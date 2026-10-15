**ADR-0004: 应用代码增量更新器与分层发布信任根（追溯记录）**

- **状态**: Implemented（追溯补记；对应本仓 2026-10-15 五维评估「架构技术债」待改进 #2，并作为「软件供应链安全」待改进 #1 的决策背景）
- **日期**: 实施锚点为 `docs/发布检查清单.md`（1.5.2→1.5.3 连续增量更新与崩溃恢复实测，2026-09-05）；本 ADR 补记于 2026-10-15
- **决策者**: 项目维护者

# 背景与问题

- 全量分卷包约 5.3 GB，小修复也要求用户重下全量包不可接受；
- 更新通道是把代码送进用户机器的唯一常态入口，必须回答「用户凭什么信这个包」；
- 壳（Rust）与应用代码（Python/静态资源）更新节奏不同：壳低频、应用高频（增量包约 1.3 MB）。

# 评估的备选方案

1. **tauri-plugin-updater 全量更新（壳+应用一体）**——单通道最简；代价是每次更新退化为完整安装包，全量体积问题回归。
2. **应用代码 zip 增量 + SHA256 校验 + 原子换载**——更新包小；校验值来自同渠道（release 正文或 .sha256 资产），防传输损坏与版本错配，但不防渠道劫持投毒。
3. **git pull / 覆盖式解压**——不适用非开发者终端用户；半途失败会留下损坏的安装目录。

# 决策

采用**分层信任根 + 方案 2 的增量通道**：

- 壳更新：tauri-plugin-updater，minisign 公钥内嵌 `desktop/src-tauri/tauri.conf.json`，安装前强制签名验证；
- 应用代码更新：`desktop/src-tauri/src/updater.rs` 实现「下载 → SHA256 → version.json 一致性 → 解压（zip-slip 防护）→ 停服 → 保留 runtime/model/data/logs 与 config.yaml、.watermark_key → 原子换载（app→app.bak→app.new→app）→ 健康检查 → 失败自动回滚」；
- 发布资产分层：SHA256SUMS.txt 必出；`integrity_manifest.json` 签名（公钥随包、用户端启动自检）为必选层；GPG（SHA256SUMS.gpg）与 Authenticode 为可选补签层——取舍与现状记录在 `.github/workflows/portable-release.yml`、`.github/workflows/gpg-signed-release.yml` 注释；
- 已知边界（诚实声明）：应用代码增量包的 SHA256 取自同渠道，渠道被劫持场景不防投毒；为该包增加独立签名已列为评估待改进项（供应链 P2），不在本 ADR 范围内实施。

# 实施影响

- `desktop/src-tauri/src/updater.rs` 与换载/回滚单测（swap_rolls_back_when_promote_fails 等）；
- `.github/workflows/portable-release.yml`：tag 推送=构建+解包冒烟+artifact（GOTCHAS #113：tag 恒不发布，增量包由维护者手工挂同一 tag）；dispatch+upload_to_release=发布并 GPG 签名 SHA256SUMS.txt；
- 发布过程实测记录：`docs/发布检查清单.md`（增量更新端到端、崩溃恢复两项 ✓）。

# 可回滚路径与待验证项

- 换载失败路径由单测覆盖（app/ 必须恢复为旧版本，应用不损坏）；
- 待验证项：应用代码包签名层落地后，重跑发布检查清单的增量更新验收。
