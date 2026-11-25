# 升级与回滚

便携离线分卷包的版本升级与手工回滚指南。所有历史 Release 均长期保留，且每个 Release 自带 `SHA256SUMS.txt` 完整性清单。

## 就地升级

1. 到 [Releases 页面](https://github.com/ReSerendipity/SeedVR2-Turbo/releases) 下载新版的全部分卷（`.zip.001/.002 …`）+ `manifest.json` + `SHA256SUMS.txt` + 两个 `.ps1` 脚本，放进**同一个文件夹**；
2. 双击运行 `unpack_portable_bundle.ps1`（或 `powershell -ExecutionPolicy Bypass -File .\unpack_portable_bundle.ps1`）；
3. 解包器会先逐卷校验 SHA256，再覆盖式解压到该目录下的 `SeedVR2-Portable\`，并自动离线重装 torch。

**哪些数据会保留、哪些会被覆盖：**

| 内容 | 升级后 |
|---|---|
| 历史记录（`data\`）、修复输出（`outputs\`）、日志 | ✅ 保留 |
| 应用代码、Python 运行时、torch、模型 | 🔄 替换为新版本 |
| `config.yaml`（应用配置） | ⚠️ **被默认配置覆盖**——升级前请备份你改过的配置项，升级后合并回去 |

::: tip 只重下变化的组件
对照 Release 页的资产清单，没变的组件可以不下载，解包时用参数跳过，例如只更 core 与模型：

```powershell
powershell -ExecutionPolicy Bypass -File .\unpack_portable_bundle.ps1 -Component core,model-shared,model-fp8
```

下载有疑问时可先加 `-VerifyOnly`：只校验分卷完整性，不解压落盘。
:::

## 回滚（降级到旧版本）

没有自动回滚通道，手工步骤：

1. 在 Releases 页找到旧版本 tag，下载该 Release 的全部分卷；
2. 解包到一个**新的独立文件夹**（不要在现有目录上直接覆盖降级，避免新旧文件混布）；
3. 需要迁移数据时，把旧目录的 `data\` 与 `outputs\` 拷贝进新目录，`config.yaml` 逐项核对后合并；
4. 双击新目录的 `start-portable.bat` 验证可用后，再删除或归档不再使用的版本目录。

::: warning 降级兼容性
新版会以增量迁移方式升级历史数据库；**从新版降回旧版**时数据库不保证向后兼容，回滚前建议先备份 `data\history.db`。
:::

## 验证下载完整性

每个 Release 附带 `SHA256SUMS.txt`（部分 Release 另有 GPG 签名 `SHA256SUMS.gpg`）：

```powershell
# PowerShell（把清单和分卷放在同一目录）
Get-Content SHA256SUMS.txt | ForEach-Object {
  $h, $n = $_ -split '\s+', 2
  if ((Get-FileHash $n.Trim('*') -Algorithm SHA256).Hash -ne $h.ToUpper()) { "FAIL  $n" } else { "OK    $n" }
}
```

解包脚本本身也会在解压前强制逐卷校验——校验不通过会拒绝继续。

部分 Release 另带 GPG 分离签名 `SHA256SUMS.gpg`（自签名密钥配置后起），可进一步确认校验清单本身可信（Git Bash / WSL / Linux）：

```bash
sha256sum -c SHA256SUMS.txt
gpg --import release-signing-key.asc   # 公钥：<https://github.com/ReSerendipity/SeedVR2-Turbo/raw/main/website/docs/guide/release-signing-key.asc>
gpg --verify SHA256SUMS.gpg SHA256SUMS.txt
```

`gpg` 输出 `Good signature from "SeedVR2 Release Signing"` 且指纹与公钥文件一致即通过（信任网警告可忽略，公钥来源以本仓库为准）。
