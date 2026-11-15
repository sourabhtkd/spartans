# syntax=docker/dockerfile:1
# --- builder: resolve the locked dependency set into /app/.venv ---------------------------
FROM python:3.11-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
# sentence-transformers pulls CUDA torch (multi-GB). Swap in the CPU wheel and drop the CUDA libs.
RUN --mount=type=cache,target=/root/.cache/uv \
    TORCH_VERSION="$(uv pip show torch | awk '/^Version:/{print $2}')" && \
    uv pip install --reinstall --no-deps "torch==${TORCH_VERSION%%+*}" --index-url https://download.pytorch.org/whl/cpu && \
    (uv pip freeze | awk -F'==' '/^(nvidia-|triton)/{print $1}' | xargs -r uv pip uninstall)
# Bake the embedding model into the image so the first request needs no download.
ENV HF_HOME=/opt/hf
RUN /app/.venv/bin/python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# --- runtime ------------------------------------------------------------------------------
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends util-linux curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 app \
    && mkdir -p /data && chown app:app /data
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /opt/hf /opt/hf
COPY codeforge ./codeforge
COPY templates ./templates
COPY config.yaml ./config.yaml
COPY .streamlit ./.streamlit
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/opt/hf \
    CODEFORGE_HOST=0.0.0.0 \
    CODEFORGE_DB_URL=sqlite:////data/codeforge.db \
    CODEFORGE_CHECKPOINT_PATH=/data/checkpoints.db \
    SANDBOX_BACKEND=local
USER app
VOLUME /data
EXPOSE 8501
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD curl -fsS http://127.0.0.1:8501/_stcore/health || exit 1
CMD ["python", "-m", "codeforge.ui"]
