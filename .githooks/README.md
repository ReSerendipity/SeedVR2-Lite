# .githooks

本仓库的 Git 钩子源码。启用一次即可：

```sh
git config core.hooksPath .githooks      # 或执行 ./.githooks/install.sh
```

启用后钩子直接从本目录读取，**改这里立即生效**，不需要再往 `.git/hooks` 复制文件。

| 钩子 | 作用 |
| --- | --- |
| `pre-commit` | 分发器按「仓库 `.venv` → PATH 上的 python → 控制台 `pre-commit` → `pre-commit-lite`」四级回退执行框架检查（含 `post-edit-validation`） |
| `pre-push` | gitleaks 待推送扫描 + 执行 `precheck.ps1`（维护者本地预检脚本，未随仓库分发；传入推送范围 `-FromSha/-ToSha`，仅文档变更走快速跳检）。无该脚本时：待推送范围含 `.py` 则直接跑 `scripts/post_edit_gate.py --full`（全量 pytest + 覆盖率门槛 55%），再走根目录守卫 |
| `prepare-commit-msg` | 自动追加 `Signed-off-by`（幂等，插在注释块之前） |
| `commit-msg` | DCO 硬校验（缺签名阻断）+ conventional 规范软提示 |
| `post-merge` / `post-checkout` | 依赖清单变更提醒（pip / pnpm / npm / cargo / gradle） |
| `pre-commit-lite` | 轻量检查：大文件、私钥与令牌、冲突标记 + 根目录守卫 + 编辑后验证门禁 |

## 编辑后验证（两层门禁）

同一个入口 `scripts/post_edit_gate.py` 跑两层，区别只在测试范围：

| 层 | 调用点 | 命令 | 开销 |
| --- | --- | --- | --- |
| commit | `.pre-commit-config.yaml` 的 `post-edit-validation`（或回退链里的 `pre-commit-lite` 第 5 步） | `ruff check <改动的 .py>` + 定向 `pytest tests/test_<模块名>*.py -q`；一个定向用例都对不上时退回全量 | 约 10 秒 |
| push | `precheck.ps1`（维护者本地脚本，未随仓库分发）或无该脚本时 `pre-push` 直接调用 | 同上 + 强制 `--full`：`pytest tests/ -q --cov=app/integrated_app --cov-fail-under=55` | 约 3 分钟 |

要点：

- commit 层的钩子开了 `verbose: true`，验证输出**总是**留在终端（进而留在会话上下文）当证据；
  静默通过等于没留痕。
- 每次运行追加一行到 `logs/post-edit-gate.jsonl`（`logs/` 已被 .gitignore 排除），事后能查「这次改动的
  代码到底验过没有」。
- 紧急出口：`POST_EDIT_GATE=off` 或 `git commit --no-verify`；前者会打醒目告警并记台账，两者都不豁免 CI。

## 说明

- `pre-commit` 分发器的框架解析优先级为四级回退：① 仓库 `.venv` 内的 python → ② PATH 上的
  `python` → ③ 控制台命令 `pre-commit`（uv tool / pipx / 系统安装）→ ④ 均不可用时退回
  `pre-commit-lite` 兜底。**要点**：③ 这一级保证「装了 pre-commit 但没建 `.venv`」的机器
  仍然跑完整框架钩子，而不是静默退化成 lite 的几项检查。
- `pre-commit-lite` 找不到 Python 时会打印醒目 `[WARN]` 并放行；设 `GUARD_STRICT=1` 可让它改为阻断。
- 大文件阈值默认 5MB，可用 `GUARD_MAX_FILE_MB` 调整。
- `core.hooksPath` 是**本地**配置，不会随克隆传播——换机器请重新执行上面那行命令。
