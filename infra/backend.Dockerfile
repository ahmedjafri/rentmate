# Backend Dockerfile
FROM python:3.12-slim AS python-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1

FROM python-base AS builder

# Build-only dependencies. Wheels are copied into the runtime stage.
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

# Copy only runtime backend files to preserve build cache and avoid frontend/test churn
COPY alembic.ini ./
COPY main.py ./
COPY memory_watchdog.py ./
COPY backends ./backends
COPY db ./db
COPY gql ./gql
COPY handlers ./handlers
COPY llm ./llm
COPY rentmate ./rentmate

# Environment variable for dev
ENV RENTMATE_ENV=development
ENV RENTMATE_DB_URI=postgresql+psycopg2://postgres:postgres@postgres:5432/rentmate
ENV RENTMATE_DEPLOYMENT_MODE=single-machine

# Expose backend port
EXPOSE 8002

# Run FastAPI with auto-reload
CMD ["python", "main.py", "--port", "8002", "--reload", "--log-level", "debug"]
