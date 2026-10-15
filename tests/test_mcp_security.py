"""MCP 工具权限登记与调用预算测试（LLM 工程评估 P1#1 落地）。

覆盖：runtime.security.mcp_enabled_tools 白名单在 tools/list 与工具处理函数双侧生效、
禁用工具拒绝、mcp_max_calls_per_minute 滑动窗口预算、配置加载失败回退默认。
桩与真实 AppConfig 形状对齐（属性访问 runtime.security），不读真实 config.yaml、
不依赖 GPU/引擎、不联网、不写仓库数据目录。
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

import app.integrated_app.config as config_mod
from app.integrated_app.mcp_server import MCPRequest, MCPServer


def _cfg_stub(security: dict) -> types.SimpleNamespace:
    """构造与 get_app_config() 返回形状一致的桩：cfg.runtime.security.<field>。"""
    sec = types.SimpleNamespace(
        mcp_enabled_tools=security.get("mcp_enabled_tools"),
        mcp_max_calls_per_minute=security.get("mcp_max_calls_per_minute", 0),
    )
    return types.SimpleNamespace(runtime=types.SimpleNamespace(security=sec))


def _server_with(monkeypatch: pytest.MonkeyPatch, security: dict) -> MCPServer:
    """以给定 runtime.security 段构造 MCPServer（替换 get_app_config）。"""
    cfg = _cfg_stub(security)
    monkeypatch.setattr(config_mod, "get_app_config", lambda *a, **k: cfg)
    return MCPServer()


def _stub_model_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 model_registry 换成轻量桩，避免测试触碰真实引擎导入。"""
    stub = types.ModuleType("app.integrated_app.model_registry")
    stub.model_registry = types.SimpleNamespace(
        list_engines=lambda: ["3b", "7b", "7b_sharp"],
        get_status=lambda: {},
    )
    monkeypatch.setitem(sys.modules, "app.integrated_app.model_registry", stub)


class TestMCPPolicyLoading:
    """策略加载与回退。"""

    def test_default_policy_enables_all_tools(self):
        """config.yaml 缺省白名单为全部 4 个工具（与历史行为一致），预算 30/分钟。"""
        server = MCPServer()
        assert server._enabled_tools == {"list_tools", "restore", "status", "history"}
        assert server._max_calls_per_minute == 30

    def test_security_config_model_defaults(self):
        """RuntimeSecurityConfig 新字段缺省值与 config.yaml 对齐。"""
        from app.integrated_app.config_models import RuntimeSecurityConfig

        cfg = RuntimeSecurityConfig()
        assert set(cfg.mcp_enabled_tools) == {"list_tools", "restore", "status", "history"}
        assert cfg.mcp_max_calls_per_minute == 30

    def test_policy_fallback_when_config_load_fails(self, monkeypatch):
        """配置加载失败回退默认（全部工具、不限频），不阻断 MCP 启动。"""

        def boom(*_a, **_k):
            raise RuntimeError("config unavailable")

        monkeypatch.setattr(config_mod, "get_app_config", boom)
        server = MCPServer()
        assert server._enabled_tools == {"list_tools", "restore", "status", "history"}
        assert server._max_calls_per_minute == 0

    def test_zero_budget_means_unlimited(self, monkeypatch):
        """mcp_max_calls_per_minute=0 表示不限频。"""
        server = _server_with(monkeypatch, {"mcp_max_calls_per_minute": 0})
        assert server._max_calls_per_minute == 0
        assert server._budget_exceeded() is False


class TestToolFiltering:
    """白名单在 tools/list 与工具处理函数双侧生效。"""

    def test_tools_list_filtered_by_allowlist(self, monkeypatch):
        server = _server_with(monkeypatch, {"mcp_enabled_tools": ["status"]})
        resp = server._handle_tools_list(MCPRequest(id=1, method="tools/list"))
        names = [t["name"] for t in resp.result["tools"]]
        assert names == ["status"]

    def test_list_tools_handler_filtered_too(self, monkeypatch):
        """名为 list_tools 的工具处理函数同样按白名单过滤。"""
        _stub_model_registry(monkeypatch)
        server = _server_with(monkeypatch, {"mcp_enabled_tools": ["status"]})
        result = asyncio.run(server._handle_list_tools())
        names = [t["name"] for t in result["tools"]]
        assert names == ["status"]
        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_disabled_tool_rejected_with_32602(self, monkeypatch):
        server = _server_with(monkeypatch, {"mcp_enabled_tools": ["status"]})
        resp = await server._handle_tools_call(MCPRequest(id=2, method="tools/call", params={"name": "restore"}))
        assert resp.error is not None
        assert resp.error["code"] == -32602
        assert "禁用" in resp.error["message"]

    def test_empty_allowlist_disables_all(self, monkeypatch):
        server = _server_with(monkeypatch, {"mcp_enabled_tools": []})
        resp = server._handle_tools_list(MCPRequest(id=1, method="tools/list"))
        assert resp.result["tools"] == []


class TestCallBudget:
    """滑动窗口调用预算。"""

    @pytest.mark.asyncio
    async def test_budget_exhausted_rejected_with_32000(self, monkeypatch):
        _stub_model_registry(monkeypatch)
        server = _server_with(monkeypatch, {"mcp_enabled_tools": ["status"], "mcp_max_calls_per_minute": 2})
        for i in (1, 2):
            resp = await server._handle_tools_call(MCPRequest(id=i, method="tools/call", params={"name": "status"}))
            assert resp.error is None, f"第 {i} 次调用应在预算内"
        resp3 = await server._handle_tools_call(MCPRequest(id=3, method="tools/call", params={"name": "status"}))
        assert resp3.error is not None
        assert resp3.error["code"] == -32000
        assert "预算" in resp3.error["message"]

    @pytest.mark.asyncio
    async def test_unknown_tool_checked_before_policy(self, monkeypatch):
        """未知工具报 -32602（未知工具），不落入禁用分支。"""
        server = _server_with(monkeypatch, {"mcp_enabled_tools": []})
        resp = await server._handle_tools_call(MCPRequest(id=4, method="tools/call", params={"name": "no_such_tool"}))
        assert resp.error is not None
        assert resp.error["code"] == -32602
        assert "未知工具" in resp.error["message"]


if __name__ == "__main__":
    pytest.main([__file__, "-q", "-p", "no:cacheprovider"])
