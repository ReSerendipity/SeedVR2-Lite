# DR 演练记录：history_db 备份恢复实测（2026-11-06）

> 对应 `docs/SRE_RUNBOOK.md` §3「灾难恢复演练」场景表「history_db 损坏」行（计划为每季度一次）。
> 本记录是**首轮执行证据**（此前全仓仅有计划、无执行记录，评估报告 O4/D6 门禁两轮 fail）。
> 取证口径：每步 = 实际命令 + 实测输出 + 结论，全程可在本机复核（命令均为仓库根目录下可直接重跑）。

## 0. 演练元数据

| 项 | 值 |
|---|---|
| 执行时间 | 2026-11-06 01:04:51 – 01:07（本地） |
| 执行对象 | `data/history.db`（155,648 字节，最后写入 2026-10-17，真实业务数据） |
| 备份工具 | `scripts/backup-db.sh`（sqlite3 CLI 3.50.6 `.backup` 一致性快照路径） |
| 演练沙箱 | `C:/Users/Doro/AppData/Local/Temp/dr_seedvr2_20261106/`（654 KB，仓库外，不污染工作树） |
| 仓库状态 | 分支 `fix/desktop-shell-csrf-unify`，HEAD `b8ab4de` |

## 1. 预检（故障前基线）

```
$ sha256sum data/history.db
823ab25bc66e4399901978d59eb9e90818716e58b9b8a426044ac5fa3a37c1fd *data/history.db

$ sqlite3 data/history.db "PRAGMA integrity_check;"
ok

$ sqlite3 data/history.db "SELECT 'history', COUNT(*) FROM history UNION ALL SELECT 'tasks', COUNT(*) FROM tasks;"
history|25
tasks|166
```

## 2. 备份（用仓内脚本，默认相对路径）

```
$ bash scripts/backup-db.sh
[1/4] 备份 SQLite 历史数据库...
  [OK] history.db 已备份 (152K)
[2/4] 备份配置文件...
  [OK] config.yaml 已备份
[3/4] 备份输出目录...
  [OK] outputs/ 已备份 (0)
[4/4] 备份上传目录...
  [OK] data/uploads/ 已备份 (2.0K)
[压缩] 创建 tar.gz 压缩包...
  [OK] 压缩包: backups/seedvr2_backup_20261106_010510.tar.gz (40K)
```

产物归档内容（解包实测）：`history.db` + `config.yaml` + `uploads/image/` 2 个 png。
`backups/` 已被 `.gitignore:312` 覆盖，演练产物不入库。

## 3. 备份可恢复性校验（逻辑对平，非字节对平）

```
$ tar -xzf backups/seedvr2_backup_20261106_010510.tar.gz -C "$RESTORE_ROOT"   # 实测 0.120s
$ sqlite3 "$RESTORE_ROOT/.../history.db" "PRAGMA integrity_check;"
ok
$ sqlite3 "$RESTORE_ROOT/.../history.db" "SELECT 'history', COUNT(*) FROM history UNION ALL SELECT 'tasks', COUNT(*) FROM tasks;"
history|25
tasks|166
$ sqlite3 data/history.db .dump | sha256sum
25bb6267eb18e69ef4239042b53c6f192782efc5bd77c57cf5bae0fd391b5a36 *-
$ sqlite3 "$RESTORE_ROOT/.../history.db" .dump | sha256sum
25bb6267eb18e69ef4239042b53c6f192782efc5bd77c57cf5bae0fd391b5a36 *-
```

**实测发现（诚实记录）**：字节级 sha256 **不一致**（源库 `823ab25b…37c1fd` vs 备份库 `f23d6748…1e8aec`）——`sqlite3 .backup` 是页级在线快照（freelist/页序可不同），**逻辑等价但字节不必相同**。因此恢复验收的正确口径是：integrity_check ok + 行数一致 + `.dump` 逻辑摘要一致（本次三者全对平）。**不要把「备份后 sha256 与源库不同」误判为备份损坏。**

## 4. 故障注入 → 检出 → 恢复 → 复验

```
$ cp "$R/history.db" "$SIM/history.db"                        # 复制一份当「在役库」
$ head -c 4096 /dev/urandom | dd of="$SIM/history.db" bs=1 count=4096 conv=notrunc
                                                              # 覆写头部 4096 字节模拟位腐
$ sqlite3 "$SIM/history.db" "PRAGMA integrity_check;"
Error: in prepare, file is not a database (26)                # 损坏被可靠检出（rc=26）
```

恢复（RTO 实测）：

```
$ T0=$(date +%s.%N)
$ cp "$R/history.db" "$SIM/history.db"                        # 从备份恢复
$ sqlite3 "$SIM/history.db" "PRAGMA integrity_check;"         # ok
$ sqlite3 "$SIM/history.db" "SELECT COUNT(*) FROM history;"   # 25
$ sqlite3 "$SIM/history.db" "SELECT COUNT(*) FROM tasks;"     # 166
$ sqlite3 "$SIM/history.db" .dump | sha256sum                 # 25bb6267…b5a36（与 §3 对平）
$ T1=$(date +%s.%N)
```

## 5. RTO / RPO 实测值

| 指标 | 实测值 | 说明 |
|---|---|---|
| 恢复拷贝（单步） | **0.072 s** | 仅 `cp` 备份库覆盖在役位 |
| 恢复 + 三重复验（integrity + 行数×2 + dump 摘要） | **0.370 s** | 端到端恢复验收窗 |
| 归档解包（tar.gz 40K → 目录） | **0.120 s** | 从压缩归档起算的前置步 |
| 端到端（解包 + 恢复 + 复验） | **0.490 s**（= 0.120 + 0.370，两步实测值之和） | 用户可感知的灾难恢复总窗 |
| RPO | **0 s** | 备份（01:05:10）紧接故障注入，备份点与故障点之间无写入 |

对照 SRE_RUNBOOK §3 目标「history_db 损坏 RTO < 15 min」：实测 0.490 s，**达标且余量 >1800×**（同库 152K 规模；库增长后按行数量级线性外推仍远低于 15 min）。

## 6. 演练暴露的脚本边界（如实记录，未在本单修改）

1. **MSYS `/tmp` 目标路径失败**：`backup-db.sh` 传 Git Bash `/tmp/...` 时，原生 `sqlite3.exe` 报
   `Error: cannot open "/tmp/..."`（Windows 原生二进制不识别 MSYS 虚拟路径）——**Windows 下须传 Windows 原生路径或仓内相对路径**（默认 `backups/` 即相对路径，正常）。
2. **GNU tar 盘符陷阱**：传 `C:/...` 绝对路径时 tar 报 `Cannot connect to C: resolve failed`
   （GNU tar 把 `C:` 当远程主机，同 gpu-smoke.yml 注释 KNOWN_ISSUES #35）——绝对盘符路径下
   步骤 1-4 的文件已拷出但压缩步失败，属脚本在 Windows 绝对路径输入下的已知边界。
3. 两条边界均不影响默认用法（相对路径全链路通过）；跨平台鲁棒化（如 `tar -czf` 前置
   `cd`、路径归一）留给属主裁决是否立项，本单不动脚本。

## 7. 结论

- **备份→检出→恢复→对平全链路实测通过**：O4/D6 门禁「补一次备份→恢复→校验对平实测即可关闭」的条件在本轮满足。
- 检出手段可靠：`PRAGMA integrity_check` 对头部损坏报 `file is not a database (26)`。
- 验收口径固化：**逻辑对平**（integrity + 行数 + `.dump` 摘要），字节级 sha256 不作恢复验收依据（见 §3 发现）。
- 下次演练按 SRE_RUNBOOK 计划频率（每季度）执行，重跑本记录 §1-§4 命令即可复核。
