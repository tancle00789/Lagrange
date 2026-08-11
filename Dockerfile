FROM node:20.19-alpine AS frontend

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM python:3.12-slim AS python-base

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app/src

RUN pip install --no-cache-dir uv==0.12.1 \
    && useradd --create-home --uid 10001 appuser
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY --chown=appuser:appuser src ./src

FROM python-base AS executor

# Heavy ML deps (sklearn/torch) live only in the executor image; the PyPI
# torch wheel bundles the CUDA runtime, so GPU passthrough needs no extra
# base image. ARG kept for environments that must use the pytorch index.
ARG TORCH_EXTRA_INDEX=""
RUN if [ -n "$TORCH_EXTRA_INDEX" ]; then \
      uv sync --frozen --no-dev --group executor --index-url "$TORCH_EXTRA_INDEX"; \
    else \
      uv sync --frozen --no-dev --group executor; \
    fi

USER appuser
CMD ["/app/.venv/bin/pk-agent-executor"]

FROM python-base AS app

COPY --chown=appuser:appuser --from=frontend /frontend/dist /app/static

EXPOSE 8000
USER appuser
CMD ["/app/.venv/bin/uvicorn", "pk_agent.agentos.main:app", "--host", "0.0.0.0", "--port", "8000"]
