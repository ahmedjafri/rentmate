# syntax=docker/dockerfile:1

FROM python:3.12-slim AS python-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1

FROM python-base AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

ENV POETRY_VERSION=2.3.3
RUN curl -sSL https://install.python-poetry.org | python3 -
ENV PATH="/root/.local/bin:$PATH"

WORKDIR /app

COPY pyproject.toml poetry.lock* ./

RUN poetry install --no-root --only main \
    && find /usr/local/lib/python3.12/site-packages -type d \( -name test -o -name tests \) -prune -exec rm -rf '{}' + \
    && rm -rf /usr/local/bin/pip* /usr/local/lib/python3.12/site-packages/pip* /root/.cache

FROM python-base AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-dejavu-core \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libpangocairo-1.0-0 \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /usr/local /usr/local

COPY alembic.ini ./
COPY main.py ./
COPY memory_watchdog.py ./
COPY backends ./backends
COPY db ./db
COPY gql ./gql
COPY handlers ./handlers
COPY llm ./llm
COPY rentmate ./rentmate

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "debug"]
