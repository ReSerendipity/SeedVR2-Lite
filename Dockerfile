# syntax=docker/dockerfile:1
# SeedVR2 容器镜像
#
# 设计要点（对应云原生评估报告 P0/P1 项）：
# 1. Multi-stage：依赖安装在 builder 层，运行时层只携带成品，减小体积与攻击面。
# 2. 基础镜像精确锁 tag；发布前建议用以下命令取得 digest 并改写 FROM 行
#    （本仓库开发环境无法联网校验 digest，故不硬编码以免伪造）：
#      docker manifest inspect -v python:3.12-slim-bookworm | findstr digest
# 3. GPU 说明：torch 的 Linux wheel 自带 CUDA 用户态运行库（nvidia-*-cu12 pip 包），
#    运行时仅需 NVIDIA 驱动经 nvidia-container-toolkit 注入：
#      docker run --gpus all ...
#    下面的 NVIDIA_VISIBLE_DEVICES / NVIDIA_DRIVER_CAPABILITIES 保证 nvidia runtime 自动生效。
# 4. 依赖锁定（评估报告 R4c）：安装 requirements-container-lock.txt（uv.lock 导出的
#    跨平台精确钉版锁，112 包全量带哈希，pip 自动进入 --require-hashes 校验模式）。
#    torch==2.13.0+cu132 的 manylinux 轮哈希由 CI 构建实测补录（cu132 索引不发布
#    PEP 658 哈希元数据，uv.lock 无法自动记录）。锁文件再生成后须同样补录 torch 哈希：
#      uv export --format requirements-txt --no-dev --no-emit-project --emit-index-url \
#          -o requirements-container-lock.txt
#      # 在 torch 条目补 --hash=sha256:<构建日志实测值>
# 5. 优雅关闭：gunicorn 收到 SIGTERM 后等待 --graceful-timeout，
#    覆盖应用 lifespan 关闭链（任务队列排空 ≤30s + 模型卸载），避免强杀丢任务。
FROM python:3.12-slim-bookworm AS builder

WORKDIR /app

# 仅复制依赖清单以最大化层缓存命中（代码变更不击穿依赖层）
COPY requirements-container-lock.txt .

# 锁文件头部自带 --index-url/--extra-index-url（PyPI + cu132 源），pip 按行解析
RUN pip install --no-cache-dir --prefix=/install -r requirements-container-lock.txt

# ---------------------------------------------------------------- runtime stage
FROM python:3.12-slim-bookworm

# OCI 标准标签：来源与版本可追溯（APP_VERSION 由 CI 构建参数注入）
ARG APP_VERSION=0.0.0-dev
LABEL org.opencontainers.image.title="SeedVR2" \
      org.opencontainers.image.description="AI-powered video & image super-resolution backend (FastAPI + CUDA)" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.source="https://github.com/ReSerendipity/SeedVR2-Turbo" \
      org.opencontainers.image.licenses="Apache-2.0"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1 \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility \
    SEEDVR2_DEPLOYMENT=container
# SEEDVR2_DEPLOYMENT=container：容器/编排暴露标记（评估报告 R1）。容器内绑定 0.0.0.0，
# 应用启动时强制 Basic Auth fail-closed——需注入 SEEDVR2_AUTH_USERNAME/PASSWORD，
# 或在端口映射严格限定回环时设 SEEDVR2_ALLOW_UNAUTHENTICATED=1 显式豁免
# （判定逻辑见 middleware/basic_auth.py 的 ensure_exposure_auth）。

WORKDIR /app

# opencv 运行所需系统库（libgl1 / libglib2.0-0，bookworm 包名）
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# 从 builder 层复制已安装依赖（不携带 pip 缓存与构建痕迹）
COPY --from=builder /install /usr/local

# 应用代码
COPY . .

# 创建运行时数据目录并切换非 root 用户（CWE-250）。
# UID/GID 钉版 1000（原 groupadd -r 自增系统 ID 在不同基础镜像上不可复现）：
# k8s securityContext 的 runAsUser/runAsGroup/fsGroup 与 compose named volume
# 的初始化属主都依赖该值，改动必须同步 deploy/kubernetes/deployment.yaml。
RUN mkdir -p data/uploads data/logs model outputs && \
    groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid 1000 --no-create-home appuser && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 7870

# 容器级健康检查：复用轻量探针 /api/system/ping（无需 curl，用标准库 urllib）
# start-period=180s 覆盖慢速 GPU 初始化 / 权重 SHA256 校验 / 模型预热
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7870/api/system/ping', timeout=4)"]

# K8s 部署探针分工（评估报告 Q2 结论）：
#   - livenessProbe 用 /api/system/ping（进程存活，三档中最轻）
#   - readinessProbe 用 /api/system/ready（模型加载中 / GPU 运行时不健康返回
#     503 + Retry-After，就绪语义由专用端点承载，避免接流到带病实例）
#   示例片段（startupProbe 宽限期需覆盖 180s+ 的 GPU 初始化与权重校验）：
#     readinessProbe:
#       httpGet: { path: /api/system/ready, port: 7870 }
#       periodSeconds: 10
#     startupProbe:
#       httpGet: { path: /api/system/ready, port: 7870 }
#       periodSeconds: 10
#       failureThreshold: 36

# 显式声明停止信号（Docker/K8s 默认即 SIGTERM，写明以防基础镜像变更）
STOPSIGNAL SIGTERM

# 模型引擎为进程内单例，worker 必须为 1（多 worker 重复加载模型到 GPU 会 OOM）
# --graceful-timeout 90：覆盖 lifespan 关闭链（队列 30s 排空 + 模型卸载）
# --timeout 120：worker 级心跳超时，容忍长推理帧间无心跳的间隙
CMD ["gunicorn", "-w", "1", "-k", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:7870", \
     "--graceful-timeout", "90", \
     "--timeout", "120", \
     "--keep-alive", "5", \
     "app.integrated_app.app_server:app"]
