# syntax=docker/dockerfile:1

FROM python:3.12-slim

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends curl build-essential && rm -rf /var/lib/apt/lists/*

# Install Poetry
ENV POETRY_VERSION=1.8.2
RUN curl -sSL https://install.python-poetry.org | python3 -
ENV PATH="/root/.local/bin:$PATH"
ENV POETRY_VIRTUALENVS_CREATE=false
ENV POETRY_NO_INTERACTION=1
ENV PIP_NO_CACHE_DIR=1

# Set workdir
WORKDIR /app

# Copy files
COPY pyproject.toml poetry.lock* ./

# Install dependencies
RUN poetry install --no-root --only main

COPY alembic.ini ./
COPY main.py ./
COPY agents ./agents
COPY backends ./backends
COPY db ./db
COPY gql ./gql
COPY handlers ./handlers
COPY llm ./llm

# Expose port and run uvicorn
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "debug"]
