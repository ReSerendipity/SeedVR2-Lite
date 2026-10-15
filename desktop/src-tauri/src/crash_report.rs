//! 本地崩溃遥测（形态包 X2 保留项 · 运维稳定性 P1#1）。
//!
//! 壳 panic 与 Python 后端异常退出各写一条 JSON 崩溃记录到 `logs/crashes/`
//! （schema `seedvr2-crash/1`）。隐私口径与产品定位一致：只落时间/类别/线程/
//! 位置与消息摘要，不落用户数据与环境变量，无任何远端上报。
//! 消费方：`app/integrated_app/utils/crash_telemetry.py`（/metrics 暴露）与
//! `scripts/check_crash_budget.py`（发布前崩溃预算核验）。

use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::json;

/// 崩溃记录 schema 版本（Python 侧按此校验）。
pub const CRASH_SCHEMA: &str = "seedvr2-crash/1";

static RECORD_SEQ: AtomicU64 = AtomicU64::new(0);

/// 崩溃记录目录：logs/crashes/（与壳日志同根）。
pub fn crash_dir(log_dir: &Path) -> PathBuf {
    log_dir.join("crashes")
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

/// 构造崩溃记录 JSON（纯函数，便于单测）。
pub fn build_record(kind: &str, message: &str, location: &str) -> String {
    json!({
        "schema": CRASH_SCHEMA,
        "timestamp_ms": now_ms(),
        "kind": kind,
        "thread": std::thread::current().name().unwrap_or("<unnamed>"),
        "message": message,
        "location": location,
    })
    .to_string()
}

/// 写入一条崩溃记录（尽力而为；create_new + 序列号保证唯一，不覆盖旧记录）。
pub fn write_record(dir: &Path, kind: &str, record: &str) -> std::io::Result<PathBuf> {
    std::fs::create_dir_all(dir)?;
    let seq = RECORD_SEQ.fetch_add(1, Ordering::Relaxed);
    let file = dir.join(format!("crash-{}-{}-{seq}.json", now_ms(), kind));
    let mut f = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&file)?;
    f.write_all(record.as_bytes())?;
    Ok(file)
}

/// 安装壳 panic 钩子：先落崩溃记录，再交还原默认钩子（stderr 输出不丢）。
pub fn install(log_dir: &Path) {
    let dir = crash_dir(log_dir);
    let default_hook = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        let msg = if let Some(s) = info.payload().downcast_ref::<&str>() {
            (*s).to_string()
        } else if let Some(s) = info.payload().downcast_ref::<String>() {
            s.clone()
        } else {
            "unknown panic payload".to_string()
        };
        let loc = info
            .location()
            .map(|l| format!("{}:{}:{}", l.file(), l.line(), l.column()))
            .unwrap_or_default();
        let record = build_record("shell_panic", &msg, &loc);
        if let Err(e) = write_record(&dir, "shell_panic", &record) {
            eprintln!("[crash-report] 写崩溃记录失败: {e}");
        }
        default_hook(info);
    }));
}

/// 记录 Python 后端异常退出（watchdog 检测到 Running 却不存活时调用）。
pub fn record_backend_exit(log_dir: &Path, pid: Option<u32>, detail: &str) {
    let record = json!({
        "schema": CRASH_SCHEMA,
        "timestamp_ms": now_ms(),
        "kind": "backend_exit",
        "thread": "watchdog",
        "pid": pid,
        "message": detail,
        "location": "",
    })
    .to_string();
    if let Err(e) = write_record(&crash_dir(log_dir), "backend_exit", &record) {
        eprintln!("[crash-report] 写后端退出记录失败: {e}");
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn record_has_schema_and_kind() {
        let r = build_record("shell_panic", "boom", "main.rs:1:1");
        let v: serde_json::Value = serde_json::from_str(&r).unwrap();
        assert_eq!(v["schema"], CRASH_SCHEMA);
        assert_eq!(v["kind"], "shell_panic");
        assert_eq!(v["message"], "boom");
        assert!(v["timestamp_ms"].as_u64().unwrap_or(0) > 0);
    }

    #[test]
    fn write_is_unique_and_readable() {
        let tmp = tempfile::tempdir().unwrap();
        let dir = tmp.path().join("crashes");
        let r1 = build_record("shell_panic", "a", "");
        let r2 = build_record("backend_exit", "b", "");
        let p1 = write_record(&dir, "shell_panic", &r1).unwrap();
        let p2 = write_record(&dir, "backend_exit", &r2).unwrap();
        assert_ne!(p1, p2);
        assert_eq!(std::fs::read_to_string(&p1).unwrap(), r1);
    }
}
