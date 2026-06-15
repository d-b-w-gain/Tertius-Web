FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    CODEX_INSTALL_DIR=/usr/local/bin

ARG CODEX_RELEASE=0.139.0

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gawk git openssh-client \
    && rm -rf /var/lib/apt/lists/*

# Install the OpenAI Codex CLI with the official non-interactive installer.
# CODEX_HOME is only used during installation here; runtime state is mounted at /codex-home.
RUN mkdir -p /opt/codex-installer \
    && curl -fsSL https://chatgpt.com/codex/install.sh \
       | CODEX_NON_INTERACTIVE=1 CODEX_RELEASE=${CODEX_RELEASE} CODEX_HOME=/opt/codex-installer CODEX_INSTALL_DIR=/usr/local/bin sh \
    && chmod -R a+rx /opt/codex-installer /usr/local/bin/codex \
    && codex --version

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
RUN uv sync --no-dev --compile-bytecode

COPY app /app/app

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin codex \
    && mkdir -p /codex-home /workspace /tmp/codex \
    && chown -R 10001:10001 /app /codex-home /workspace /tmp/codex /home/codex

USER 10001:10001

ENV HOME=/home/codex \
    CODEX_HOME=/codex-home \
    CODEX_WORKSPACE=/workspace \
    CODEX_TIMEOUT_SECONDS=900 \
    CODEX_SANDBOX=read-only \
    MAX_PROMPT_CHARS=100000 \
    MAX_CONCURRENT_CODEX=1 \
    UV_CACHE_DIR=/tmp/uv-cache

EXPOSE 8000

CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
