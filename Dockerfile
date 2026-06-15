# Use the official Python base image
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/usr/local

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set working directory
WORKDIR /app

# Install system dependencies required for OpenCASCADE/Build123D
# Build123D (and OCP) requires some GL and X11 libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libxrender1 \
    libxext6 \
    libx11-6 \
    libice6 \
    libsm6 \
    git \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies via uv from the root pyproject + lockfile
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --locked --no-install-project

# Copy the server directory and install the project itself
COPY server/ /app/server/
RUN uv sync --no-dev --locked

RUN chmod +x /app/server/start-api.sh /app/server/start-compile-job.sh

# Expose the API port
EXPOSE 8000

# Run the master FastAPI server
CMD ["python", "server/main.py"]
