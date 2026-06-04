# Use the official Python base image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies required for OpenCASCADE/Build123D
# Build123D (and OCP) requires some GL and X11 libraries
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    libxrender1 \
    libxext6 \
    libx11-6 \
    libice6 \
    libsm6 \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the server directory
COPY server/ /app/server/

# Expose the API port
EXPOSE 8000

# Run the master FastAPI server
CMD ["python", "server/main.py"]
