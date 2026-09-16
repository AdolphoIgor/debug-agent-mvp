FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_SYSTEM_PYTHON=1

# Install system dependencies, security utilities, and Docker CLI
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    ca-certificates \
    gnupg \
    lsb-release \
    build-essential \
    libpq-dev \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null \
    && apt-get update && apt-get install -y --no-install-recommends \
    docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

# Install uv for deterministic dependency resolution
RUN pip install --no-cache-dir uv>=0.1.40

WORKDIR /workspace

# Cache dependency specifications to optimize rebuild layers
COPY pyproject.toml uv.lock* ./

# Install project runtime and development dependencies into system Python
RUN uv pip install --system -e ".[dev]" || pip install --no-cache-dir -e ".[dev]"

# Copy project repository assets
COPY . /workspace

# Maintain container persistence for DevContainer and remote orchestrator execution
CMD ["tail", "-f", "/dev/null"]