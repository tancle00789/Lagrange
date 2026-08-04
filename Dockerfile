FROM node:20-alpine AS frontend

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app/src

RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY src ./src
COPY --from=frontend /frontend/dist /app/static

EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "pk_agent.agentos.main:app", "--host", "0.0.0.0", "--port", "8000"]
