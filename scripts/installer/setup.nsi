; SeedVR2 桌面版 NSIS 安装器
; 用法：makensis setup.nsi
Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"

Name "SeedVR2 桌面版"
OutFile "SeedVR2-Setup-v${APP_VERSION}.exe"
InstallDir "$LOCALAPPDATA\Programs\SeedVR2"
InstallDirRegKey HKCU "Software\SeedVR2" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
CRCCheck on
BrandingText "SeedVR2"

; 版本号：发布时用 -DAPP_VERSION=<pyproject.toml 的 version> 显式传入；下面的默认值只在
; 脱离参数编译时兜底，务必与 pyproject.toml / scripts/installer/version.json 保持一致
; （历史上就因为这里停在 1.5.4，装完提示「已是最新版本 1.5.4」而实际代码是 1.7.0）。
!ifndef APP_VERSION
  !define APP_VERSION "1.7.0"
!endif
!define DATA_PREFIX "SeedVR2-Data.7z"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\SeedVR2"
; 图标路径相对 setup.nsi 所在目录（scripts/installer/），克隆后无需改路径即可编译；
; 也可用 makensis -DAPP_ICON=... 覆盖
!ifndef APP_ICON
  !define APP_ICON "..\..\desktop\src-tauri\icons\icon.ico"
!endif
; 7za 默认从 PATH 查找，或用 makensis -DSRC_7ZA=... 指定（勿写死本机绝对路径）
!ifndef SRC_7ZA
  !define SRC_7ZA "7za.exe"
!endif

Var StartMenuFolder
Var TOOLSDIR

; ---------- 终止运行中的 SeedVR2（壳 + 其 Python 子进程）----------
!macro KillRunning
  DetailPrint "检测到 SeedVR2 正在运行，正在自动终止..."
  nsExec::ExecToLog 'taskkill /f /im SeedVR2.exe'
  Sleep 1500
  nsExec::ExecToLog 'powershell -NoProfile -ExecutionPolicy Bypass -File "$TOOLSDIR\killseedvr2.ps1"'
  Sleep 1500
!macroend

; ---------- MUI 页面 ----------
!define MUI_ABORTWARNING
!define MUI_ICON "${APP_ICON}"
!define MUI_UNICON "${APP_ICON}"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "license.txt"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_STARTMENU "Application" $StartMenuFolder
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\SeedVR2.exe"
!define MUI_FINISHPAGE_RUN_TEXT "立即运行 SeedVR2"
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

; ---------- 安装 ----------
Section "SeedVR2 桌面版" SEC_APP
  ; 自建临时工具目录（不依赖 $PLUGINSDIR，该变量在 NSIS 3.10 实测为空）
  StrCpy $TOOLSDIR "$TEMP\seedvr2-tools"
  RMDir /r "$TOOLSDIR"
  CreateDirectory "$TOOLSDIR"
  SetOutPath "$TOOLSDIR"
  ; 统一嵌入名为 7za.exe：编译机上的 7-Zip 可能是 7z.exe，也可能是独立的 7za.exe，
  ; 下面 ExecWait 只认 7za.exe。不改名会出现「文件在但进程起不来、错误码空串」的
  ; 静默失败（VOSR2-Lite 0.2.1 真机踩过）。
  File /oname=7za.exe "${SRC_7ZA}"
  File "killseedvr2.ps1"

  !insertmacro KillRunning

  ; 校验数据分卷存在
  ${IfNot} ${FileExists} "$EXEDIR\${DATA_PREFIX}.001"
    MessageBox MB_ICONSTOP "缺少数据分卷 ${DATA_PREFIX}.001。`n请确认 setup.exe 与数据分卷（.001/.002/...）位于同一目录。"
    Abort
  ${EndIf}

  ; 创建安装目录并解压数据分卷
  DetailPrint "正在解压数据分卷（约 1.3GB，视磁盘速度需 1-3 分钟，请勿关闭窗口）..."
  ExecWait '"$TOOLSDIR\7za.exe" x "$EXEDIR\${DATA_PREFIX}.001" -o"$INSTDIR" -y -bso0 -bsp0' $0
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "数据解压失败（7za 错误码 $0）。`n请检查：1) 磁盘空间是否充足（需约 4GB）；2) 数据分卷是否完整（SHA256 校验见随附说明）。"
    Abort
  ${EndIf}

  ; 只覆盖壳：app 代码（style.css / version.json / pyproject.toml / task.py）
  ; 在数据卷里已是同一次构建的版本，安装器再覆盖会把它们退回旧版本
  ; ——1.7.0 装完显示 1.5.4 就是这么来的。换壳无需重打数据卷。
  SetOutPath "$INSTDIR"
  File "SeedVR2.exe"



  ; 校验主程序存在
  ${IfNot} ${FileExists} "$INSTDIR\SeedVR2.exe"
    MessageBox MB_ICONSTOP "安装异常：未找到 SeedVR2.exe，安装可能不完整。"
    Abort
  ${EndIf}

  ; 快捷方式（开始菜单 + 桌面）
  !insertmacro MUI_STARTMENU_WRITE_BEGIN Application
    CreateDirectory "$SMPROGRAMS\$StartMenuFolder"
    CreateShortcut "$SMPROGRAMS\$StartMenuFolder\SeedVR2.lnk" "$INSTDIR\SeedVR2.exe"
  !insertmacro MUI_STARTMENU_WRITE_END
  CreateShortcut "$DESKTOP\SeedVR2.lnk" "$INSTDIR\SeedVR2.exe"

  ; 注册表（卸载信息）
  WriteRegStr HKCU "Software\SeedVR2" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "SeedVR2 桌面版"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "SeedVR2"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\SeedVR2.exe"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "EstimatedSize" 8000000

  ; 卸载器
  WriteUninstaller "$INSTDIR\uninstall.exe"

  ; 清理临时工具目录
  RMDir /r "$TOOLSDIR"
SectionEnd

; ---------- 版本信息 ----------
VIProductVersion "1.5.4.0"
VIAddVersionKey "ProductName" "SeedVR2 桌面版"
VIAddVersionKey "ProductVersion" "${APP_VERSION}"
VIAddVersionKey "FileDescription" "SeedVR2 桌面版安装程序"
VIAddVersionKey "FileVersion" "${APP_VERSION}"

; ---------- 卸载 ----------
Section "Uninstall"
  ; 自建临时工具目录
  StrCpy $TOOLSDIR "$TEMP\seedvr2-tools"
  RMDir /r "$TOOLSDIR"
  CreateDirectory "$TOOLSDIR"
  SetOutPath "$TOOLSDIR"
  File "killseedvr2.ps1"

  !insertmacro KillRunning

  ; 清理快捷方式
  Delete "$DESKTOP\SeedVR2.lnk"
  !insertmacro MUI_STARTMENU_GETFOLDER Application $StartMenuFolder
  Delete "$SMPROGRAMS\$StartMenuFolder\SeedVR2.lnk"
  RMDir "$SMPROGRAMS\$StartMenuFolder"
  ; 清理注册表
  DeleteRegKey HKCU "Software\SeedVR2"
  DeleteRegKey HKCU "${UNINST_KEY}"
  ; 删除安装目录（循环重试 30 秒，等待句柄释放）
  RMDir /r "$INSTDIR"
  StrCpy $R0 0
  ${DoWhile} ${FileExists} "$INSTDIR"
    IntOp $R0 $R0 + 1
    ${If} $R0 >= 30
      ${Break}
    ${EndIf}
    Sleep 1000
    RMDir /r "$INSTDIR"
  ${Loop}
  ; 兜底：卸载器退出后由独立 cmd 延迟清理（等文件锁释放后删除）
  nsExec::Exec 'cmd /c ping -n 6 127.0.0.1 >nul & rd /s /q "$INSTDIR"'
  ; 清理临时工具目录
  RMDir /r "$TOOLSDIR"
SectionEnd
