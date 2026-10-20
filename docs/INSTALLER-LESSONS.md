# docs/INSTALLER-LESSONS.md — Inno Setup & CI 编译经验教训（v1.4.8 分卷打包）

## A. 背景与目标

**目标**：在 GitHub Release 单文件 2GB 限制下，提供完整离线安装包（不含模型），用户下载后无需联网即可安装全部依赖。

**方案**：
- **Full 包** (~350MB)：WinPython + app 代码 + 启动器 → 单文件
- **Torch 包** (~2.7GB wheels)：用 Inno Setup `DiskSpanning` 自动拆成多个 <2GB 分卷

---

## B. Inno Setup 编译错误汇总（v1.4.5 → v1.4.8）

### B.1 非法指令 `InfoBeforeMsg`（v1.4.5 失败）

**现象**：
```
Error on line 29 in launcher\installer_full.iss: Unrecognized [Setup] section directive "InfoBeforeMsg"
Compile aborted.
```

**根因**：Inno Setup **没有 `InfoBeforeMsg` 指令**。正确的显示安装前提示的方式是：
- `InfoBeforeFile=<filename.txt>` — 引用外部文本文件
- 或直接在 `[Setup]` 用 `InfoBefore=`（不推荐长文本）

**修复**：
```ini
; ❌ 错误
InfoBeforeMsg=本安装包仅包含 Torch GPU 依赖...

; ✅ 正确
InfoBeforeFile=installer_torch_info.txt
```

**配套操作**：创建对应的 `.txt` 说明文件：
```text
launcher/installer_full_info.txt
launcher/installer_torch_info.txt
```

---

### B.2 误加不存在的 `DiskName` 指令（v1.4.6 失败）

**现象**：
```
Error on line 32: Unrecognized [Setup] section directive "DiskName"
```

**根因**：Inno Setup 的 `DiskSpanning` 相关指令只有：
- `DiskSpanning=yes/no` — 启用多卷拆分
- `DiskSliceSize=<bytes>` — 每卷最大字节数
- `DiskName=<name>` 不存在！这是我自己臆造的指令。

**修复**：
```ini
; ❌ 错误
DiskSpanning=yes
DiskSliceSize=1900000000
DiskName=SeedVR2Torch  ; ← 非法指令，删除

; ✅ 正确
DiskSpanning=yes
DiskSliceSize=1900000000
```

**产物**：自动生成 `setup.exe` + `setup-1.bin` + `setup-2.bin` ...

---

### B.3 `Exec()` 的 `ResultCode` 传 `Nil`（v1.4.7 失败）

**现象**：
```
Error on line 84 in installer_torch.iss: Column 80:
Compile aborted.
```

**根因**：Inno Setup Pascal 的 `Exec()` 函数签名：
```pascal
function Exec(const Filename, Parameters, WorkingDir: String;
              const ShowCmd: Integer; const Wait: TExecWait; var ResultCode: Integer): Boolean;
```
最后一个参数 `ResultCode` 是 **`var` 引用参数**，必须传变量，**不能传 `Nil`**。

**错误代码**：
```pascal
Exec('cmd.exe', '/c ' + pipCmd, '', SW_HIDE, ewWaitUntilTerminated, Nil);  // ❌
```

**修复**：
```pascal
var
  rc: Integer;
begin
  ...
  Exec('cmd.exe', cmdArgs, '', SW_HIDE, ewWaitUntilTerminated, rc);  // ✅
end;
```

---

### B.4 字符串行长超限（潜在问题）

**现象**：Pascal 脚本中一行超过 ~255 字符可能触发编译警告或错误。

**修复**：用行连接符 `+` 换行：
```pascal
; ❌ 超长单行
cmdArgs := '/c "' + pythonExe + '" -m pip install --no-index --find-links=' + ExpandConstant('{tmp}\torch_wheels') + ' torch torchvision torchaudio';

; ✅ 换行连接
cmdArgs := '/c "' + pythonExe + '" -m pip install --no-index --find-links=' +
           ExpandConstant('{tmp}\torch_wheels') + ' torch torchvision torchaudio';
```

---

## C. CI/CD工作流坑点

### C.1 torch wheels 下载超时

**现象**：GitHub Actions runner 访问 `download.pytorch.org` 偶尔超时。

**修复**：
```powershell
& $py -m pip download torch torchvision torchaudio `
  --index-url https://download.pytorch.org/whl/cu128 `
  -d dist\torch_wheels --no-deps --timeout 300 --retries 3
```
- `--timeout 300` — 延长到 5 分钟
- `--retries 3` — 重试 3 次
- `--no-deps` — 只取 torch 家族本体，避免拉取大量小依赖

---

### C.2 WinPython 解压后 `python` junction 导致 ISCC 失败

**现象**：
```
ISCC: cannot open path ...\WPy64-312101\python\...
```

**根因**：WinPython 归档里 `python` 是一个 **junction/reparse point**（符号链接），ISCC 无法跟随。

**修复**：解压后立即删除 junction：
```powershell
$pyLink = "WPy64-312101\python"
if (Test-Path $pyLink) {
  $it = Get-Item $pyLink -Force
  if ($it.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    [System.IO.Directory]::Delete($pyLink)
  }
}
```

---

### C.3 冒烟测试图源文件不存在导致编译失败

**现象**：
```
Source file not found: demo\assets\inputs\input-1.jpg
```

**修复**：在 `[Files]` 加 `skipifsourcedoesntexist`：
```ini
Source: "..\demo\assets\inputs\input-1.jpg"; DestDir: "{app}\launcher\test-assets"; Flags: skipifsourcedoesntexist
```

---

## D. DiskSpanning 分卷打包验证（v1.4.8 成功）

### D.1 配置参数
```ini
DiskSpanning=yes
DiskSliceSize=1900000000  ; 1.9GB，留余量给 2GB 限制
```

### D.2 产物大小（符合限制）
| 文件 | 大小 |
|------|------|
| `SeedVR2-Setup-Full-v1.4.8.exe` | 172 MB |
| `SeedVR2-Torch-Installer-v1.4.8.exe` | 2 MB |
| `SeedVR2-Torch-Installer-v1.4.8-1.bin` | 1897 MB |
| `SeedVR2-Torch-Installer-v1.4.8-2.bin` | 849 MB |

所有文件都 **< 2GB** ✅

### D.3 用户安装流程
1. 下载所有分卷放到同一文件夹
2. 双击第一个 `SeedVR2-Torch-Installer-v*.exe`
3. 安装程序自动读取同目录的 `.bin` 分卷，合并注入 torch 到已装的 Full 包环境

---

## E. 经验总结（可复用）

| 场景 | 解决方案 |
|------|---------|
| 超 2GB 大文件打包 | 用 `DiskSpanning=yes` + `DiskSliceSize` 自动分卷 |
| 安装前提示信息 | 用 `InfoBeforeFile=<.txt>`，不要用 `InfoBeforeMsg` |
| Pascal `Exec()` 调用 | `ResultCode` 必须传变量，不能传 `Nil` |
| 字符串过长 | 用 `+` 换行连接 |
| 源文件可能缺失 | 加 `skipifsourcedoesntexist` 标志 |
| junction/symlink 路径 | ISCC 无法跟随，解压后手动删除 |
| pip 下载大文件 | `--timeout 300 --retries 3 --no-deps` |

---

## F. 后续优化方向

1. **分卷压缩算法**：当前 `lzma2` 压缩率已很高，但可尝试 `zip` 格式提升兼容性
2. **断点续传支持**：引导页检测已下载的分卷，跳过重复下载
3. **多镜像源回退**：torch wheels 下载失败时切到阿里云镜像
4. **分卷校验**：安装前校验 `.bin` 完整性（MD5/SHA256）

---

*最后更新：2026-08-25（v1.4.8 分卷打包成功）*
