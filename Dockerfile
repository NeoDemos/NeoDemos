# NeoDemos - Multi-City Political Analysis Platform
# Production-ready Docker image for NeoDemos
# Supports: Rotterdam, Amsterdam, Den Haag, and extensible to other Dutch cities

FROM python:3.12.13-slim-bookworm

# Set working directory
WORKDIR /app

# Set environment variables for Python
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies (incl. Tesseract OCR for Docling/RapidOCR + Node.js for Vite)
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    curl \
    tesseract-ocr \
    tesseract-ocr-nld \
    libgl1 \
    libglib2.0-0 \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (for better layer caching)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --upgrade pip setuptools wheel && \
    pip install -r requirements.txt

# Copy package.json first for npm layer caching
COPY package.json package-lock.json* ./
RUN npm ci --ignore-scripts 2>/dev/null || npm install

# Copy application code
COPY . .

# Build frontend assets (Vite + Tailwind CSS v4)
RUN npm run build

# Create directories for data, logs, and output
RUN mkdir -p /app/logs /app/data/pipeline /app/data/profiles /app/output

# Health check - works for both the web service (port 8000) and the MCP
# accessory (port 8001). Both expose a /up liveness endpoint that returns
# 200 OK without touching DB/Qdrant. The PORT env var is set per service in
# config/deploy.yml.
HEALTHCHECK --interval=30s --timeout=30s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/up || exit 1

# Run as non-root (WS4 Dockerfile hardening)
RUN useradd -m -u 1000 app && chown -R app:app /app
USER app

# Expose port
EXPOSE ${PORT:-8000}

# Run FastAPI application with Uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
