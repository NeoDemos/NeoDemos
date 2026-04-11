# NeoDemos - Multi-City Political Analysis Platform
# Production-ready Docker image for NeoDemos
# Supports: Rotterdam, Amsterdam, Den Haag, and extensible to other Dutch cities

FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Set environment variables for Python
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (for better layer caching)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --upgrade pip setuptools wheel && \
    pip install -r requirements.txt

# Copy application code
COPY . .

# Create directories for data, logs, and output
RUN mkdir -p /app/logs /app/data/pipeline /app/data/profiles /app/output

# Health check - works for both the web service (port 8000) and the MCP
# accessory (port 8001). Both expose a /up liveness endpoint that returns
# 200 OK without touching DB/Qdrant. The PORT env var is set per service in
# config/deploy.yml.
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/up || exit 1

# Expose port
EXPOSE ${PORT:-8000}

# Run FastAPI application with Uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
