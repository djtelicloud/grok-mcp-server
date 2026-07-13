FROM python:3.12-slim

WORKDIR /app

# Install system dependencies. Git is used only when contributor mode attaches
# a workspace; the stable service itself is workspace-neutral.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    git \
    && rm -rf /var/lib/apt/lists/*

# The contributor compose file bind-mounts /workspace from a host with a
# different uid, so mark only that conventional development mount as safe.
RUN git config --system --add safe.directory /workspace

# Install uv using the installer script (version-pinned, world-readable location)
ADD https://astral.sh/uv/0.11.24/install.sh /uv-installer.sh
ENV UV_INSTALL_DIR="/usr/local/bin"
RUN sh /uv-installer.sh && rm /uv-installer.sh

# Configure uv for optimal Docker usage
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install dependencies with sync (project itself is copied and run from source)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra forge --no-install-project

# Install the grok CLI (Linux build) so the local CLI plane works inside
# the container. Version-pinned for reproducible builds. The installer drops
# the binary under /root/.grok/bin; move it to a world-readable path because
# the runtime user's ~/.grok is reserved for the persistent service-level OAuth
# volume (auth.json OAuth session + refresh token).
RUN curl -fsSL https://x.ai/cli/install.sh | bash -s 0.2.93 \
    && install -m 0755 /root/.grok/bin/grok /usr/local/bin/grok \
    && rm -rf /root/.grok

# Copy application code
COPY main.py ./
COPY src/ ./src/
COPY mcp_ui/ ./mcp_ui/
COPY docs/okf/ ./docs/okf/
COPY .grok/hyperparams/ ./.grok/hyperparams/
COPY .grok/prompts/ ./.grok/prompts/

# Run as an unprivileged user. Stable mutable data lives under /state, never
# in the application bundle or an IDE project.
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin appuser \
    && mkdir -p /state /home/appuser/.grok \
    && chown -R appuser:appuser /app /state /home/appuser/.grok
USER appuser

# Expose port (optional for stdio, required for HTTP transport later if I will add it)
EXPOSE 8080

# Containers must listen on all interfaces: the container loopback is not
# reachable through published ports, so the local-runtime 127.0.0.1 default
# would make `docker run -p 4765:8080` silently unreachable while the
# in-container healthcheck still reports healthy. Exposure is controlled by
# how the host publishes the port (docker-compose binds it to 127.0.0.1).
ENV UNIGROK_HOST=0.0.0.0
ENV PORT=8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT', '8080'), timeout=5)"]

# Command to run the HTTP gateway for Cloud Run/container deployments
# (--no-sync: the venv is baked at build time; never resolve/build at runtime)
CMD ["uv", "run", "--no-sync", "python", "main.py", "--http"]
