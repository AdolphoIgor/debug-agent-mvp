# ==============================================================================
# STAGE 1: Minimal Base (Only runtime shared libraries)
# ==============================================================================
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_SYSTEM_PYTHON=1

WORKDIR /workspace

# Install only shared runtime libraries (no compilers, no git)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.6.5 /uv /bin/uv
COPY pyproject.toml uv.lock* README.md* ./


# ==============================================================================
# STAGE 2: Build Environment (Compiles native C extensions & wheels)
# ==============================================================================
FROM base AS builder
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Compile shared base dependencies
RUN uv pip compile pyproject.toml -o /tmp/requirements-base.txt && \
    uv pip install --system -r /tmp/requirements-base.txt


# ==============================================================================
# MCP-SERVER TARGET: Zero Git, Zero Compilers
# ==============================================================================
FROM builder AS mcp-build
RUN uv pip compile pyproject.toml --extra mcp -o /tmp/requirements-mcp.txt && \
    uv pip install --system -r /tmp/requirements-mcp.txt

FROM base AS mcp-server
# Copy installed Python site-packages from builder stage
COPY --from=mcp-build /usr/local /usr/local
COPY . .
RUN uv pip install --system --no-deps -e .

USER 1000:1000
EXPOSE 8080


# ==============================================================================
# ORCHESTRATOR-APP TARGET: Runtime Git Enabled
# ==============================================================================
FROM builder AS orchestrator-build
RUN uv pip compile pyproject.toml --extra orchestrator -o /tmp/requirements-orchestrator.txt && \
    uv pip install --system -r /tmp/requirements-orchestrator.txt

FROM base AS orchestrator-app
# Install Git solely on the orchestrator runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY --from=orchestrator-build /usr/local /usr/local
COPY . .
RUN uv pip install --system --no-deps -e .

EXPOSE 8000