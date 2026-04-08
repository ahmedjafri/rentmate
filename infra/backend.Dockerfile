# Backend Dockerfile
FROM python:3.12-slim

# System dependencies
# We need build-essential for packages like fastuuid that require a C linker
RUN apt-get update && apt-get install -y \
    curl \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
ENV POETRY_VERSION=1.8.2
RUN curl -sSL https://install.python-poetry.org | python3 -
ENV PATH="/root/.local/bin:$PATH"

WORKDIR /app

# Copy dependency files
COPY pyproject.toml poetry.lock* ./

# Install Python dependencies
RUN poetry config virtualenvs.create false \
  && poetry install --no-root

# Copy the rest of the backend code
COPY . .

# Environment variable for dev
ENV RENTMATE_ENV=development
ENV RENTMATE_DB_PATH=/app/data/rentmate.db

# Expose backend port
EXPOSE 8002

# Run FastAPI with auto-reload
CMD ["python", "main.py", "--port", "8002", "--reload", "--log-level", "debug"]
