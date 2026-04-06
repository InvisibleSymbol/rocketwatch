# syntax=docker/dockerfile:1
FROM python:3.14-slim

RUN apt-get update && apt-get install -y --no-install-recommends git ffmpeg && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml pyproject.toml
RUN uv pip install --system --no-cache -r pyproject.toml

COPY rocketwatch/ /app/rocketwatch
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app
WORKDIR /app/rocketwatch
CMD [ "python", "." ]
