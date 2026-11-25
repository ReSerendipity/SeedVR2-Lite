# 桌面版（Tauri）

SeedVR2-Turbo 除「浏览器 + 本地服务」形态外，还提供 **Tauri v2 原生桌面版**：Rust 外壳 +
侧载 Python 运行时 + WebView2，把 Web UI 封装为原生桌面应用。壳源码位于仓库
`desktop/` 目录，壳与前端通过 `desktop/src/desktop-bridge.js` 桥接（浏览器模式自动降级，
所有桌面增强功能在纯浏览器访问时静默不可用）。

## 桌面版增强了什么

| 能力 | 说明 |
|---|---|
| 系统托盘 | 左键显隐窗口、右键菜单、忙闲状态图标、更新徽标 |
| 窗口状态记忆 | 位置 / 大小 / 最大化状态跨启动恢复；关闭可最小化到托盘 |
| 系统通知 | Windows Toast（任务完成 / 失败），前台时自动抑制，声音可关 |
| 文件拖拽 | 拖入视频 / 图片直接进入修复工作台（白名单扩展名校验） |
| 增量更新 | 应用代码增量更新：GitHub 检查 → semver 比较 → 下载 → SHA256 校验 → 原子换载，失败自动回滚 |

## 系统要求

- Windows 10 22H2 / Windows 11（x64），WebView2 Runtime（多数系统自带）
- NVIDIA GPU（建议 8GB+ 显存）+ 较新驱动
- 便携分卷包内含侧载 Python 运行时；开发模式复用项目根 `.venv`

## 获取与启动

1. 从 GitHub Releases 下载桌面版分卷包（或按 `desktop/README.md` 自行构建）
2. 解压后运行启动器，壳会拉起 Python 子进程并轮询 `/api/system/health` 就绪后进入主界面
3. 详细的解压 / 首次启动 / 托盘与拖拽操作说明见仓库 `docs/用户手册.md`

## 开发者

桌面壳使用 Rust 实现，质量门禁与 Web 端一致严格：

```bash
cd desktop/src-tauri
cargo test                              # 壳单测（含更新换载 / 回滚）
cargo clippy --all-targets -- -D warnings   # 零警告门禁
```

打包发布流程与增量更新包制作见 `docs/开发者指南.md` 与 `scripts/release_tauri.ps1`。
