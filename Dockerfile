FROM python:3.13-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    REMBG_HOME=/opt/rembg

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libfribidi0 libharfbuzz0b \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
# poster.py: bake the person-cutout model (176 MB) into the image instead of downloading it on the first post
RUN uv run --no-sync python -c "from rembg import new_session; new_session('u2net_human_seg')"

COPY . .
RUN uv sync --frozen --no-dev

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
