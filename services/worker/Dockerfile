# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.10.10 AS uv

FROM python:3.14.7-slim-bookworm AS build
WORKDIR /build
COPY --from=uv /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.14.7-slim-bookworm AS runtime
ENV DEBIAN_FRONTEND=noninteractive \
    PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

RUN apt-get update \
    && apt-get upgrade --yes \
    && apt-get install --yes --no-install-recommends libgomp1 tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 noteflow \
    && useradd --system --uid 10001 --gid noteflow --home-dir /nonexistent --shell /usr/sbin/nologin noteflow

WORKDIR /app
COPY --from=build /build/.venv /app/.venv
COPY --chown=noteflow:noteflow noteflow_worker ./noteflow_worker

USER 10001:10001
ENTRYPOINT ["python", "-m", "noteflow_worker.main"]
