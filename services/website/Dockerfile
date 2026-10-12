FROM ghcr.io/astral-sh/uv:0.13.0 AS uv
FROM python:3.13-slim
COPY --from=uv /uv /usr/local/bin/uv
RUN useradd --uid 1001 --create-home --shell /usr/sbin/nologin app
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
COPY services ./services
RUN uv sync --frozen --no-dev --package synchronisica-website
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
