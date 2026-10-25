# One image runs both the API (with the dashboard) and the worker.

# ---- 1. Build the dashboard as static files
FROM node:24-alpine AS dashboard
WORKDIR /dashboard
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY dashboard/ ./
RUN npm run build

# ---- 2. Install Python dependencies with uv
FROM python:3.13-slim AS python-deps
RUN pip install --no-cache-dir uv==0.11.32
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ---- 3. Runtime image
FROM python:3.13-slim
RUN useradd --create-home --uid 1000 app
WORKDIR /app
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 STATIC_DIR=/app/static
COPY --from=python-deps /opt/venv /opt/venv
COPY backend/ ./
COPY --from=dashboard /dashboard/out ./static
RUN chmod +x scripts/*.sh && chown -R app:app /app
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1
CMD ["scripts/start-api.sh"]
