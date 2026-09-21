FROM node:22-alpine AS frontend
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /usr/local/bin/uv
WORKDIR /app
ARG INSTALL_CREW=false
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ ./src/
COPY --from=frontend /build/src/agentlatch/static/ ./src/agentlatch/static/
RUN if [ "$INSTALL_CREW" = "true" ]; then uv sync --frozen --extra crew --no-dev; else uv sync --frozen --no-dev; fi
RUN useradd --create-home appuser && mkdir /data && chown appuser /data
USER appuser
ENV AGENTLATCH_DB=/data/state.db
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["agentlatch", "serve", "--host", "0.0.0.0"]
