<!--
  launcher/release-notes-portable.md
  GitHub Release 正文模板，由 .github/workflows/portable-release.yml 读取并替换占位符后写入 Release。
  占位符：{{VERSION}} = 版本号（不带 v），{{TOTAL_GB}} = 产物合计体积（GB）
  必须放在受跟踪路径：docs/project/ 等自 2026-08-27 起被 .gitignore 忽略，
  CI checkout 里不会有那些文件，放那里会导致发布步骤读不到正文而失败。
  纪律：本文件描述的每个文件名与步骤都必须与当次 manifest.json 一致，不得凭印象改。
-->

# SeedVR2 {{VERSION}} — 便携离线分卷包

完全离线的便携发行包：解压即可运行，全过程不需要联网下载依赖。
合计约 **{{TOTAL_GB}} GB**，拆成多个分卷，**每个文件都小于 GitHub 的 2 GB 上限**。

## 这个包里有什么

| 组件 | 内容 | 分卷数 |
|---|---|:---:|
| `core` | 应用代码 + 便携 Python 3.12 解释器（已预装全部非 torch 依赖）+ `start-portable.bat` | 1 |
| `torch` | PyTorch / TorchVision 的 CUDA 13.2（cu132）wheel + TorchAudio 2.11.0（PyPI CPU 轮，与 cu132 搭配正常），含传递依赖 | 1 |
| `model-shared` | `ema_vae_fp16.safetensors` + `pos_emb.pt` + `neg_emb.pt` | 1 |
| `model-fp8` | `seedvr2_3b_mxfp8.safetensors`（内置主模型，MXFP8 量化） | 2 |

准确的文件名、分卷数量、SHA256 与**各组件版本号**以本次 Release 里的 `manifest.json` / `SHA256SUMS.txt` 为准。
> 上表分卷数按本次构建实测填写（2026-10-28：torch 依赖集缩小，由 2 卷降为 1 卷）。分卷数随依赖体积变化，发版前必须对照当次 `manifest.json` 复核本表。
`manifest.json` 中每个组件带 `version` 字段：`core`/`torch` 跟随应用版本与 torch 钉版，`model-*` 是权重内容哈希——**权重没变，版本号就不变**。

## 安装步骤

1. **下载同一组件的全部文件**（缺任何一个分卷都会报错并停下）。至少要下 `core` + `torch` + `model-shared` + `model-fp8` 的全部 `.00N` 文件，外加 `manifest.json`、`SHA256SUMS.txt`、`portable_bundle_lib.ps1`、`unpack_portable_bundle.ps1`。
2. 把它们**放进同一个文件夹**。
3. 右键 `unpack_portable_bundle.ps1` →「使用 PowerShell 执行」，或在其所在目录执行：

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\unpack_portable_bundle.ps1 -TargetDir D:\SeedVR2
   ```

   脚本会：校验每个分卷的 SHA256 → 合并 → 解压 → **用本地 wheel 离线安装 torch** → 按清单核对落地结果。
4. 进入 `D:\SeedVR2\SeedVR2-Portable\`，双击 `start-portable.bat`，浏览器打开 <http://127.0.0.1:7870>。

只想先校验下载是否完整（不解压）：

```powershell
powershell -ExecutionPolicy Bypass -File .\unpack_portable_bundle.ps1 -VerifyOnly
```

## 运行要求

- Windows 10/11 x64，NVIDIA 显卡，显存 ≥ 8 GB（内置的 3B MXFP8 权重）
- 磁盘预留 ≥ 15 GB（解压后约 12 GB，另需临时空间）
- 已安装 NVIDIA 驱动（torch 为 cu132 / CUDA 13.2 运行时，需较新驱动；驱动过旧时 `torch.cuda.is_available()` 会是 False，脚本会给出提示）
- **不内置 FFmpeg**：图片修复开箱即用；视频修复请自行安装 FFmpeg 并加入 PATH（许可证原因，见 `NOTICE` 第 4 条）

## 从旧版本升级（增量解包）

从本脚本解包出的旧安装升级到新版本时，**不需要重新下载没变化的组件**：

1. 对比新旧两份 `manifest.json` 的组件 `version` 字段，只下载**有变化组件**的全部分卷（外加新的 `manifest.json`、`SHA256SUMS.txt`、`unpack_portable_bundle.ps1`、`portable_bundle_lib.ps1`）；
2. 把它们放进一个文件夹，就地执行：

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\unpack_portable_bundle.ps1 -TargetDir D:\SeedVR2 -ExistingInstall D:\SeedVR2
   ```

   与现有安装一致的组件会被**增量复用**（分卷缺席也允许），只有变化的组件被重新解包；解包后按清单逐文件核对，旧文件被改动过会被发现。

## 模型说明

- 本包只内置 **3B MXFP8** 权重（Comfy-Org 量化格式）。`config.yaml` 的默认精度是 `fp16`，在只有 MXFP8 权重时程序会自动回退（日志里有一条 WARNING，属预期行为）。
- 想要 FP16 或 7B 权重，按仓库 README 的直链自行下载放入 `SeedVR2-Portable\model\` 即可，无需重新打包。

## 与 exe 安装包的区别

本页只提供分卷压缩包。历史上另有 `SeedVR2-Setup-Full-*.exe` / `SeedVR2-Torch-Installer-*`（Inno Setup 路线），自 v1.27 起不再随 tag 自动发布。**推荐直接使用本页的分卷压缩包**：不需要管理员权限、不写注册表、不装第二遍。

## 校验

```powershell
Get-FileHash .\SeedVR2-Portable-v*-model-fp8.zip.001 -Algorithm SHA256   # 与 SHA256SUMS.txt 比对
```

解包脚本本身会在解压前强制逐卷校验 SHA256，校验不通过会拒绝继续。

进阶验证（确认清单本身可信，Git Bash / WSL / Linux）：

```bash
sha256sum -c SHA256SUMS.txt
gpg --import release-signing-key.asc   # 公钥：仓库 website/docs/guide/release-signing-key.asc
gpg --verify SHA256SUMS.gpg SHA256SUMS.txt
```

`SHA256SUMS.gpg` 为发布链路自动生成的 GPG 分离签名（自配置签名密钥后的版本起提供）。
