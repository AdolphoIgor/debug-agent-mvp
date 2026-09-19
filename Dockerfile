FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_SYSTEM_PYTHON=1

WORKDIR /workspace

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    ca-certificates \
    gnupg \
    lsb-release \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.6.5 /uv /bin/uv

# Dependency cache boundary: Manifests and mandatory documentation are staged first
COPY pyproject.toml uv.lock* README.md* ./

# Project dependencies are compiled and installed into system Python
RUN uv pip compile pyproject.toml -o /tmp/requirements.txt && \
    uv pip install --system -r /tmp/requirements.txt

# Application source tree is copied
COPY . .

# Root package metadata is registered in editable mode with dependencies bypassed
RUN uv pip install --system --no-deps -e .

EXPOSE 8080

CMD ["python", "src/mcp_db_server.py"]