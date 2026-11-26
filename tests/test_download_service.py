"""下载管理焦点测试：attempt 归属、失败保留、取消与路由。"""

from unittest.mock import MagicMock

import pytest

from app.integrated_app import download_service as download_service
from app.integrated_app.routes import downloads as download_routes
from tests.conftest import csrf_post


@pytest.fixture
def deferred_threads(monkeypatch):
    created = []

    class DeferredThread:
        def __init__(self, target, args=(), name=None, daemon=None):
            self.target = target
            self.args = args
            self.name = name
            self.daemon = daemon
            created.append(self)

        def start(self):
            return None

    monkeypatch.setattr(download_service.threading, "Thread", DeferredThread)
    return created


def _fail_runtime(progress, is_cancelled):
    progress("installing", 0.2, "test install")
    raise OSError("synthetic download failure")


def test_manager_reuses_inflight_task(deferred_threads):
    manager = download_service.DownloadManager()

    first = manager.start("runtime", "", {})
    second = manager.start("runtime", "", {})

    assert first is second
    assert len(deferred_threads) == 1


def test_retry_preserves_failed_attempt(monkeypatch, deferred_threads):
    monkeypatch.setattr(download_service, "install_runtime", _fail_runtime)
    manager = download_service.DownloadManager()

    failed = manager.start("runtime", "", {})
    deferred_threads[-1].target(*deferred_threads[-1].args)
    retried = manager.start("runtime", "", {})

    assert failed.stage == "error"
    assert failed.finished is True
    assert failed.attempt_id != retried.attempt_id
    assert manager.recent_failures() == [failed.to_dict()]
    assert manager.progress() == [retried.to_dict()]


def test_cancel_only_targets_active_task(deferred_threads):
    manager = download_service.DownloadManager()
    task = manager.start("runtime", "", {})

    assert manager.cancel("runtime") is True
    assert task._cancel.is_set() is True

    task.finished = True
    assert manager.cancel("runtime") is False


def test_attempt_id_is_written_to_logs(monkeypatch, caplog):
    caplog.set_level("INFO")
    monkeypatch.setattr(download_service, "install_runtime", lambda progress, is_cancelled: None)
    task = download_service.DownloadTask("runtime", "runtime", "")

    task.run({})

    assert task.stage == "done"
    assert task.attempt_id in caplog.text
    assert "completed" in caplog.text


@pytest.mark.integration
def test_download_routes_expose_attempts_and_failures(test_app, monkeypatch, deferred_threads):
    manager = download_service.DownloadManager()
    monkeypatch.setattr(download_routes, "get_download_manager", lambda: manager)
    monkeypatch.setattr(download_routes, "_deny_remote", lambda request: None)
    monkeypatch.setattr(download_service, "runtime_catalog", lambda: {"runtime": {}})
    monkeypatch.setattr(download_service, "install_runtime", _fail_runtime)

    started = csrf_post(test_app, "/api/downloads/start", json={"kind": "runtime", "target": ""})
    assert started.status_code == 200
    assert started.json()["success"] is True
    first_attempt = started.json()["data"]["attempt_id"]
    assert first_attempt

    deferred_threads[-1].target(*deferred_threads[-1].args)
    retried = csrf_post(test_app, "/api/downloads/start", json={"kind": "runtime", "target": ""})
    assert retried.json()["data"]["attempt_id"] != first_attempt

    progress = test_app.get("/api/downloads/progress")
    assert progress.status_code == 200
    assert progress.json()["data"]["recent_failures"][0]["attempt_id"] == first_attempt
    assert progress.json()["data"]["tasks"][0]["attempt_id"] == retried.json()["data"]["attempt_id"]

    cancelled = csrf_post(test_app, "/api/downloads/cancel", json={"kind": "runtime", "target": ""})
    assert cancelled.json()["data"]["cancelled"] is True

    uninstall = MagicMock()
    monkeypatch.setattr(download_routes, "uninstall_runtime", uninstall)
    removed = csrf_post(test_app, "/api/downloads/uninstall", json={"kind": "runtime", "target": ""})
    assert removed.json()["success"] is True
    uninstall.assert_called_once_with()
