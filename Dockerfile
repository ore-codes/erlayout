FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH"

WORKDIR /app
# dependencies first, so code changes do not reinstall them
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY README.md ./
COPY erlayout ./erlayout
RUN uv sync --locked --no-dev --no-editable

RUN useradd --create-home app
USER app
# a public instance: at most 2 solves at once, 5 minutes each, 2 threads each
ENV PORT=8000 ERLAYOUT_MAX_RUNNING=2 ERLAYOUT_MAX_TIME=300 ERLAYOUT_WORKERS=2
EXPOSE 8000
HEALTHCHECK CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health')"
CMD ["sh", "-c", "exec erlayout serve --host 0.0.0.0 --port \"$PORT\""]
