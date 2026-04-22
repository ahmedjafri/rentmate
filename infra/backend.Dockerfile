# Backend Dockerfile
FROM python:3.12-slim

# System dependencies
# We need build-essential for packages like fastuuid that require a C linker
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
ENV POETRY_VERSION=1.8.2
RUN curl -sSL https://install.python-poetry.org | python3 -
ENV PATH="/root/.local/bin:$PATH"
ENV POETRY_VIRTUALENVS_CREATE=false
ENV POETRY_NO_INTERACTION=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /app

# Copy dependency files
COPY pyproject.toml poetry.lock* ./

# Install Python dependencies, including dev tools for containerized local testing
RUN poetry install --no-root

# Install shared libraries for WeasyPrint HTML->PDF rendering
RUN apt-get update && apt-get install -y --no-install-recommends \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libpangocairo-1.0-0 \
    shared-mime-info \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Copy only runtime backend files to preserve build cache and avoid frontend/test churn
COPY alembic.ini ./
COPY main.py ./
COPY backends ./backends
COPY db ./db
COPY gql ./gql
COPY handlers ./handlers
COPY llm ./llm

# Environment variable for dev
ENV RENTMATE_ENV=development
ENV RENTMATE_DB_URI=postgresql+psycopg2://postgres:postgres@postgres:5432/rentmate
ENV RENTMATE_DEPLOYMENT_MODE=single-machine

# Expose backend port
EXPOSE 8002

# Run FastAPI with auto-reload
CMD ["python", "main.py", "--port", "8002", "--reload", "--log-level", "debug"]
