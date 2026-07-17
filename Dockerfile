FROM python:3.12-slim

ARG GROK_CLI_VERSION=0.2.101
ARG UV_VERSION=0.11.24

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

ADD https://astral.sh/uv/${UV_VERSION}/install.sh /uv-installer.sh
ENV UV_INSTALL_DIR=/usr/local/bin
RUN sh /uv-installer.sh && rm /uv-installer.sh

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

RUN curl -fsSL https://x.ai/cli/install.sh | bash -s "${GROK_CLI_VERSION}" \
    && install -m 0755 /root/.grok/bin/grok /usr/local/bin/grok \
    && rm -rf /root/.grok

COPY src/ ./src/

RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin appuser \
    && mkdir -p /home/appuser/.grok /state \
    && chown -R appuser:appuser /app /home/appuser/.grok /state

USER appuser

ENV PYTHONPATH=/app/src \
    UNIGROK_HOST=0.0.0.0 \
    PORT=8080

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=5)"]

CMD ["/app/.venv/bin/python", "-m", "unigrok_public.server"]
