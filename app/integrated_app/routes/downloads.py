#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""下载管理路由：运行时与权重的就绪检测、安装、取消、卸载。

瘦包首次启动时后端跑在引导解释器上（无 torch），本模块与 `download_service.py`
是「下载管理」页面的全部依赖，**两者都禁止顶层 import torch**——否则页面打不开，
用户就陷入「装了软件却装不上环境」的死局。

组件 ID 约定：环境为 `runtime`，权重为 `model:<size>/<precision>`（如 `3b/mxfp8`）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.integrated_app.download_service import (
    build_status,
    catalog_view,
    get_download_manager,
    uninstall_model,
    uninstall_runtime,
)
from app.integrated_app.utils.response import respond_error, respond_success

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/downloads", tags=["下载管理"])

# 与 system/shutdown.py 同一套回环白名单：下载/删除是重操作，只认本机
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"})


def _deny_remote(request: Request):
    client_host = (request.client.host if request.client else "") or ""
    if client_host not in LOOPBACK_HOSTS:
        logger.warning(f"拒绝非本机下载管理请求，来源: {client_host or '<unknown>'}")
        return respond_error(code="FORBIDDEN", message="仅允许本机调用", status=403)
    return None


class StartRequest(BaseModel):
    kind: str = Field(description="runtime | model")
    target: str = Field(default="", description="kind=model 时为 <size>/<precision>")


class ComponentRequest(BaseModel):
    kind: str = Field(description="runtime | model")
    target: str = Field(default="", description="kind=model 时为 <size>/<precision>")


def _config(request: Request) -> dict:
    """直接从应用状态取配置。

    刻意不用 `dependencies.get_config`：那个模块顶层 import 了 ModelManager →
    推理引擎 → torch，在引导态（瘦包无 torch）会把本路由连带拖挂，下载页就打不开了。
    """
    return getattr(request.app.state, "config", {}) or {}


@router.get("/status")
async def status(request: Request):
    """整体状态：运行模式、运行时就位情况、各权重就位情况。"""
    return respond_success(build_status(_config(request)))


@router.get("/catalog")
async def catalog(request: Request):
    """可下载清单：环境包（含分卷与校验和）+ 各权重变体。"""
    return respond_success(catalog_view(_config(request)))


@router.post("/start")
async def start(request: Request, payload: StartRequest):
    """启动下载任务。同一组件重复调用返回既有任务，不会并行下两遍。"""
    denied = _deny_remote(request)
    if denied:
        return denied
    kind = payload.kind.strip()
    if kind not in ("runtime", "model"):
        return respond_error(code="BAD_REQUEST", message="kind 必须是 runtime 或 model", status=400)
    if kind == "model" and not payload.target.strip():
        return respond_error(code="BAD_REQUEST", message="kind=model 必须指定 target", status=400)
    if kind == "runtime":
        from app.integrated_app.download_service import runtime_catalog

        if not runtime_catalog():
            return respond_error(
                code="NO_CATALOG",
                message="未找到环境包清单 downloads_catalog.json，无法确定下载地址",
                status=503,
            )
    task = get_download_manager().start(kind, payload.target.strip(), _config(request))
    return respond_success(task.to_dict())


@router.get("/progress")
async def progress():
    """全部任务的进度快照（前端轮询）。"""
    return respond_success({"tasks": get_download_manager().progress()})


@router.post("/cancel")
async def cancel(request: Request, payload: ComponentRequest):
    """请求取消：置取消标志，下载循环在下一次读块时退出。"""
    denied = _deny_remote(request)
    if denied:
        return denied
    component = "runtime" if payload.kind == "runtime" else f"model:{payload.target.strip()}"
    return respond_success({"cancelled": get_download_manager().cancel(component)})


@router.post("/uninstall")
async def uninstall(request: Request, payload: ComponentRequest):
    """卸载：删除运行时目录，或删除指定权重文件（共享 VAE/嵌入保留）。"""
    denied = _deny_remote(request)
    if denied:
        return denied
    kind = payload.kind.strip()
    if kind == "runtime":
        uninstall_runtime()
        return respond_success({"uninstalled": "runtime"})
    if kind == "model" and payload.target.strip():
        uninstall_model(payload.target.strip(), _config(request))
        return respond_success({"uninstalled": f"model:{payload.target.strip()}"})
    return respond_error(code="BAD_REQUEST", message="kind 必须是 runtime 或 model+target", status=400)
