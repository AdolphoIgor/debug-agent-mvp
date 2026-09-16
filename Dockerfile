FROM <IMAGE>

USER root

# 1. Install system toolchain (Includes cmake and g++ for C++ extensions)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gcc g++ cmake procps && apt-get clean && rm -rf /var/lib/apt/lists/*

# 2. Install uv binary globally
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="/usr/local/bin" sh


# 4. Copy workspace manifests FIRST to leverage Docker layer caching
COPY ./pyproject.toml ./uv.lock* ./

# 5. Pre-install CPU-specific third-party dependencies
RUN uv pip install --no-cache -r pyproject.toml 

# 6. Copy application code FIRST
COPY ./src/ ./src/

USER <USER>